import os
import sys
import json
import boto3
import urllib3
import logging
from datetime import datetime
import time
from botocore.exceptions import ClientError

# globals
ct_log_bucket = 'aws-controltower-logs-{}-{}'
topicArn = 'arn:aws:sns:{}:{}:aws-controltower-AllConfigNotifications'
org_delivery_channel_name = 'aws-controltower-ConfigDeliveryChannel-org-created'

LOGGER = logging.getLogger()
if 'log_level' in os.environ:
    LOGGER.setLevel(os.environ['log_level'])
    LOGGER.info('Log level set to %s' % LOGGER.getEffectiveLevel())
else:
    LOGGER.setLevel(logging.ERROR)

session = boto3.Session()

def assume_role(aws_account_number, role_name):
    #sts_client = boto3.client('sts', region_name=os.environ['AWS_REGION'],
    #    endpoint_url=f"https://sts.{os.environ['AWS_REGION']}.amazonaws.com")
    #partition = sts_client.get_caller_identity()['Arn'].split(":")[1]
    #current_account = sts_client.get_caller_identity()['Arn'].split(":")[4]
    #if aws_account_number == current_account:
    #    LOGGER.info(f"Using existing region_session for Account {aws_account_number}")
    #    return session
    #else:
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
        LOGGER.warning("Control Tower StackSet not found in this Region")
        LOGGER.warning(str(ex))
    LOGGER.info(f"Control Tower Regions: {list(region_set)}")
    return list(region_set)

def is_valid_ou(ou_id):
    valid_ou = False
    org_client = session.client('organizations', region_name=os.environ['ct_home_region'])
    try:
        root_response = org_client.list_roots()
        if root_response['Roots']:
            root_id = root_response['Roots'][0]['Id']
            LOGGER.info(f"List Organizational Units for Root {root_id} ..")
            paginator = org_client.get_paginator('list_children')
            iterator = paginator.paginate(ParentId=root_id, ChildType='ORGANIZATIONAL_UNIT')
            for page in iterator:
                for ou in page['Children']:
                    if ou['Id'] == ou_id:
                        valid_ou = True
                        break
    except Exception as ex:
        LOGGER.error(f"Error in call org_client.list_children(..)")
    return valid_ou

def get_ou_accounts(ou_id):
    accounts = []
    org_client = session.client('organizations', region_name=os.environ['ct_home_region'])
    try:
        response = org_client.list_accounts_for_parent(ParentId=ou_id)
        for account in response['Accounts']:
            if account['Id']:
                accounts.append(account['Id'])
    except Exception as ex:
        LOGGER.error(f"Error in call list_accounts_for_parent(..) for Organizational Unit {ou_id}")
    return accounts

def update_member_channel(accountId, ct_regions):
    s3BucketName = ct_log_bucket.format(os.environ['logarchive_account'], os.environ['ct_home_region'])
    member_session = assume_role(accountId, os.environ['assume_role'])
    for region in ct_regions:
        config_client = member_session.client('config', endpoint_url=f"https://config.{region}.amazonaws.com", region_name=region)
        channels_response = config_client.describe_delivery_channels()
        if len(channels_response['DeliveryChannels']) > 0:
            # update delivery channel
            snsTopicARN = topicArn.format(region, os.environ['audit_account'])
            channel_name = channels_response['DeliveryChannels'][0]['name']
            channel_dict = {
                'name': channel_name,
                's3BucketName': s3BucketName,
                's3KeyPrefix': os.environ['org_id'],
                'snsTopicARN': snsTopicARN,
                'configSnapshotDeliveryProperties': {
                    'deliveryFrequency': 'TwentyFour_Hours'
                }
            }
            try:
                config_client.put_delivery_channel(
                    DeliveryChannel=channel_dict
                )
            except Exception as ex:
                LOGGER.error(f"put_delivery_channel(..) call failed for Account {accountId} in Region {region}")
                LOGGER.error(str(ex))
        else:
            # no delivery channel, so create it
            LOGGER.info(f"No delivery channel found for Account {accountId} in Region {region}. Creating it now.")
            snsTopicARN = topicArn.format(region, os.environ['audit_account'])
            create_member_delivery_channel(config_client, accountId, region, s3BucketName, snsTopicARN)

def create_member_delivery_channel(config_client, accountId, region, s3BucketName, snsTopicARN):
    channel_dict = {
        'name': org_delivery_channel_name,
        's3BucketName': s3BucketName,
        's3KeyPrefix': os.environ['org_id'],
        'snsTopicARN': snsTopicARN,
        'configSnapshotDeliveryProperties': {
            'deliveryFrequency': 'TwentyFour_Hours'
        }
    }
    try:
        config_client.put_delivery_channel(
            DeliveryChannel=channel_dict
        )
    except Exception as ex:
        LOGGER.error(f"put_delivery_channel(..) call failed for Account {accountId} in Region {region}")
        LOGGER.error(str(ex))

def lambda_handler(event, context):
    LOGGER.info(f"REQUEST RECEIVED: {json.dumps(event, default=str)}")
    ou_id = os.environ['org_unit_id']
    account_id = os.environ['member_account']
    ct_regions = get_ct_regions(session)
    if not is_valid_ou(ou_id):
        LOGGER.error(f"Invalid Organizational Unit {ou_id}. Exiting now.")
        raise SystemExit()
    #accounts = get_ou_accounts(ou_id)
    #for member_account_id in accounts:
    #    update_member_channel(member_account_id, ct_regions)
    update_member_channel(account_id, ct_regions)