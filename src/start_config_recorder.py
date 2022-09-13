import os
import sys
import json
import boto3
import csv
import argparse
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

ct_config_recorder_name = 'aws-controltower-BaselineConfigRecorder'

class StartConfigRecorderFailed(Exception):
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
    print("Assumed region_session for Account {}".format(aws_account_number))
    return sts_session

def start_config_recorder(member_session, aws_account_number, region):
    status = False
    LOGGER.info(f"Starting Config Recorder for Account: {aws_account_number} in Region: {region} ..")
    try:
        config_client = member_session.client('config', endpoint_url=f"https://config.{region}.amazonaws.com", region_name=region)
        response = config_client.describe_configuration_recorders()
        if response['ConfigurationRecorders']:
            for recorder in response['ConfigurationRecorders']:
                start_response = config_client.start_configuration_recorder(ConfigurationRecorderName=recorder['name'])
                LOGGER.info(f"Started Config Recorder {recorder['name']} for Account: {aws_account_number} in Region: {region}")
        else:
            LOGGER.info(f"No Configuration Recorder found for Account: {aws_account_number} in Region: {region}")
        status = True
    except Exception as ex:
        LOGGER.error(f"Failed in start_configuration_recorder(..) for Account: {aws_account_number} in Region: {region}")
        print(str(ex))
        raise StartConfigRecorderFailed(f"Failed in start_configuration_recorder(..) for Account: {aws_account_number} in Region: {region}")
    finally:
        return status

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
    account_region = event['member_region']
    role_name = event['assume_role']
    member_session = assume_role(org_id, account_id, role_name)
    status = start_config_recorder(member_session, account_id, account_region)
    return {
        'statusCode': 200,
        'body': {
            'org_id': org_id,
            'org_unit_id': ou_id,
            'ct_home_region': ct_home_region,
            's3_bucket': s3bucket,
            's3_key': s3key,
            'member_account': account_id,
            'logarchive_account': logarchive_account,
            'audit_account': audit_account,
            'member_region': account_region,
            'assume_role': role_name,
            'start_config_recorder_success': status
        }
    }
