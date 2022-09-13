import os
import sys
import json
import boto3
import csv
import argparse
import urllib3
from datetime import date, datetime
import time
from botocore.exceptions import ClientError

session = boto3.Session()
ct_config_recorder_name = 'aws-controltower-BaselineConfigRecorder'

parser = argparse.ArgumentParser()
parser.add_argument('account_id', help='Member Account Id')

def json_serial(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError('Type %s not serializable' % type(obj))

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
    print("Assumed region_session for Account {}".format(aws_account_number))
    return sts_session

def get_ct_regions(ct_session):
    cf = ct_session.client('cloudformation')
    region_set = set()
    try:
        # stack instances are outdated
        stacks = cf.list_stack_instances(
            StackSetName='AWSControlTowerBP-BASELINE-CLOUDWATCH')
        for stack in stacks['Summaries']:
            region_set.add(stack['Region'])
    except Exception as ex:
        print("Control Tower StackSet not found in this Region")
        print(str(ex))
    print("Control Tower Regions: {}".format(list(region_set)))
    return list(region_set)

def start_config_recorder(member_session, aws_account_number, region):
    print("Stopping Config Recorder for Account: {} in Region: {} ..".format(aws_account_number, region))
    try:
        config_client = member_session.client('config', endpoint_url=f"https://config.{region}.amazonaws.com", region_name=region)
        response = config_client.describe_configuration_recorders()
        if response['ConfigurationRecorders']:
            for recorder in response['ConfigurationRecorders']:
                start_response = config_client.stop_configuration_recorder(ConfigurationRecorderName=recorder['name'])
                print('Stopped Config Recorder {} for Account: {} in Region: {}'.format(recorder['name'], aws_account_number, region))
        else:
            print('No Configuration Recorder found for Account: {} in Region: {}'.format(aws_account_number, region))
    except Exception as ex:
        print("Failed in stop_configuration_recorder(..) for Account: {} in Region: {}".format(aws_account_number, region))
        print(str(ex))

def main():
    args = parser.parse_args()
    os.environ['log_level'] = 'INFO'
    os.environ['org_id'] = 'o-a4tlobvmc0'
    os.environ['assume_role'] = 'AWSControlTowerExecution'
    ct_regions = get_ct_regions(session)
    if args.account_id:
        member_session = assume_role(args.account_id, os.environ['assume_role'])
        for region in ct_regions:
            stop_config_recorder(member_session, args.account_id, region)

if __name__ == '__main__':
    main()
