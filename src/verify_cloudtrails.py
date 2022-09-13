import os
import sys
import json
import boto3
import csv
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

class VerifyFailedException(Exception):
    pass

def json_serial(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError('Type %s not serializable' % type(obj))

def assume_role(org_id, aws_account_number, role_name):
    sts_client = boto3.client('sts')
    partition = sts_client.get_caller_identity()['Arn'].split(":")[1]
    response = sts_client.assume_role(
        RoleArn='arn:%s:iam::%s:role/%s' % (
            partition, aws_account_number, role_name
        ),
        RoleSessionName=str(aws_account_number+'-'+role_name),
        ExternalId=org_id
    )
    sts_session = boto3.Session(
        aws_access_key_id=response['Credentials']['AccessKeyId'],
        aws_secret_access_key=response['Credentials']['SecretAccessKey'],
        aws_session_token=response['Credentials']['SessionToken']
    )
    LOGGER.info(f"Assumed region_session for Account {aws_account_number}")
    return sts_session

def get_ct_regions(account_id):
    cf = session.client('cloudformation')
    region_set = set()
    try:
        paginator = cf.get_paginator('list_stack_instances')
        iterator = paginator.paginate(StackSetName='AWSControlTowerBP-BASELINE-CONFIG', StackInstanceAccount=account_id)
        for page in iterator:
            for summary in page['Summaries']:
                region_set.add(summary['Region'])
    except Exception as ex:
        LOGGER.warning("Control Tower StackSet not found in this Region")
        LOGGER.warning(str(ex))
    LOGGER.info(f"Control Tower Region Count: {str(len(list(region_set)))}")
    LOGGER.info(f"Control Tower Regions: {list(region_set)}")
    return list(region_set)

def get_cloudtrails(member_session, account_id, region):
    LOGGER.info(f"Get CloudTrails for Account: {account_id} in Region: {region} ..")
    cloud_trails = 0
    try:
        trail_client = member_session.client('cloudtrail', endpoint_url=f"https://cloudtrail.{region}.amazonaws.com", region_name=region)
        paginator = trail_client.get_paginator('list_trails')
        iterator = paginator.paginate()
        for page in iterator:
            for trail in page['Trails']:
                cloud_trails += 1
    except Exception as ex:
        LOGGER.error(f"Failed in get_paginator('list_trails') for Account: {account_id} in Region: {region}")
        LOGGER.error(str(ex))
        raise VerifyFailedException('failed to verify cloudtrails for Account: {} in Region: {}'.format(account_id, region))
    return cloud_trails

def lambda_handler(event, context):
    LOGGER.info(f"REQUEST RECEIVED: {json.dumps(event, default=str)}")
    org_id = event['org_id']
    ou_id = event['org_unit_id']
    ct_home_region = event['ct_home_region']
    s3bucket = event['s3_bucket']
    s3key = event['s3_key']
    account_id = event['member_account']
    logarchive_account = event['logarchive_account']
    audit_account = event['audit_account']    
    assume_role_name = event['assume_role']
    ct_regions = get_ct_regions(audit_account)
    member_session = assume_role(org_id, account_id, assume_role_name)
    region_cloudtrails = []
    for region in ct_regions:
        try:
            cloudtrail_count = get_cloudtrails(member_session, account_id, region)
            region_cloudtrails.append({ 
                'org_id': org_id,
                'org_unit_id': ou_id,
                'ct_home_region': ct_home_region,
                's3_bucket': s3bucket,
                's3_key': s3key,
                'trail_count': cloudtrail_count,
                'member_account': account_id,
                'member_region': region,
                'logarchive_account': logarchive_account,
                'audit_account': audit_account,
                'assume_role': assume_role_name
            })
        except VerifyFailedException as vfe:
            raise vfe
    return {
        'statusCode': 200,
        'body': {
            'region_cloudtrails': region_cloudtrails
        }
    }