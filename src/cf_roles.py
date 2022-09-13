import os
import sys
import json
import boto3
import urllib3
import logging
from datetime import date, datetime
import time
from botocore.exceptions import ClientError

LOGGER = logging.getLogger()
if 'log_level' in os.environ:
    LOGGER.setLevel(os.environ['log_level'])
    LOGGER.info('Log level set to %s' % LOGGER.getEffectiveLevel())
else:
    LOGGER.setLevel(logging.ERROR)

session = boto3.Session()

def json_serial(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError('Type %s not serializable' % type(obj))

def check_cf_admin_role(ct_session):
    iam_client = ct_session.client('iam')
    paginator = iam_client.get_paginator('list_roles')
    role_iterator = paginator.paginate()
    role_found = True
    for page in role_iterator:
        for role in page['Roles']:
            if role['RoleName'] and role['RoleName'] == 'AWSCloudFormationStackSetAdministrationRole':
                role_found = True
                break
        if role_found:
            break
    return role_found

def create_cf_admin_role(ct_session, s3bucket):
    cf_client = ct_session.client('cloudformation')
    stackName = 'AWSCloudFormationStackSetAdministrationRole'
    template_url = 'https://s3.amazonaws.com/'+s3bucket+'/'+stackName+'.yml'
    create_response = {}
    try:
        create_response = cf_client.create_stack(
            StackName=stackName,
            TemplateURL=template_url,
            Capabilities=[ 'CAPABILITY_NAMED_IAM' ]
        )
        LOGGER.info(f"Stack {stackName} created.")
        LOGGER.info(f"{json.dumps(create_response)}")
        wait_on_stack(ct_session, stackName)
    except Exception as ex:
        LOGGER.error(f"Create Stack {stackName} failed. Exiting here.")
        LOGGER.error(str(ex))
        raise SystemExit()

def create_cf_exec_role(master_account_id, account_id, s3bucket):
    stackName = 'AWSCloudFormationStackSetExecutionRole'
    roleName = os.environ['assume_role']
    member_session = assume_role(account_id, roleName)
    cf_client = member_session.client('cloudformation')
    template_url = 'https://s3.amazonaws.com/'+s3bucket+'/'+stackName+'.yml'
    parameter = {
        'ParameterKey': 'AdministratorAccountId',
        'ParameterValue': master_account_id
    }
    create_response = {}
    try:
        create_response = cf_client.create_stack(
            StackName=stackName,
            TemplateURL=template_url,
            Parameters=[ parameter ],
            Capabilities=[ 'CAPABILITY_NAMED_IAM' ]
        )
        LOGGER.info(f"Stack {stackName} created.")
        LOGGER.info(f"{json.dumps(create_response)}")
        wait_on_stack(member_session, stackName)
    except Exception as ex:
        LOGGER.error(f"Create Stack {stackName} failed. Exiting here.")
        LOGGER.error(str(ex))
        raise SystemExit()

def wait_on_stack(session, stackName):
    cf_client = session.client('cloudformation')
    try:
        waiter = cf_client.waiter('stack_create_complete')
        waiter.wait(
            StackName=stackName
        )
        LOGGER.info(f"Stack {stackName} create completed.")
    except Exception as ex:
        LOGGER.error(f"Stack {stackName} creation failed. Exiting here.")
        LOGGER.error(str(ex))
        raise SystemExit()

def assume_role(aws_account_number, role_name):
    sts_client = boto3.client('sts')
    partition = sts_client.get_caller_identity()['Arn'].split(":")[1]
    response = sts_client.assume_role(
        RoleArn='arn:%s:iam::%s:role/%s' % (
            partition, aws_account_number, role_name
        ),
        RoleSessionName=str(aws_account_number+'-'+role_name),
        ExternalId=os.environ['org_id']
    )
    sts_session = boto3.Session(
        aws_access_key_id=response['Credentials']['AccessKeyId'],
        aws_secret_access_key=response['Credentials']['SecretAccessKey'],
        aws_session_token=response['Credentials']['SessionToken']
    )
    LOGGER.info(f"Assumed region_session for Account {aws_account_number}")
    return sts_session

def lambda_handler(event, context):
    LOGGER.info(f"REQUEST RECEIVED: {json.dumps(event, default=str)}")
    s3bucket = os.environ['S3Bucket']
    member_account_id = os.environ['member_account']
    master_account_id = boto3.client('sts').get_caller_identity()['Account']
    if not check_cf_admin_role(session):
        create_cf_admin_role(session, s3bucket)
    create_cf_exec_role(master_account_id, member_account_id, s3bucket)
