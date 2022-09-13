import os
import sys
import json
import boto3
import urllib3
import logging
from datetime import date, datetime
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

class PutDeliveryChannelFailed(Exception):
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

def update_member_channel(event):
    status = False
    org_id = event['org_id']
    account_id = event['member_account']
    region = event['member_region']
    logarchive_account = event['logarchive_account']
    audit_account = event['audit_account']
    ct_home_region = event['ct_home_region']
    role_name = event['assume_role']
    s3BucketName = ct_log_bucket.format(logarchive_account, ct_home_region)
    member_session = assume_role(org_id, account_id, role_name)
    config_client = member_session.client('config', endpoint_url=f"https://config.{region}.amazonaws.com", region_name=region)
    channels_response = config_client.describe_delivery_channels()
    if len(channels_response['DeliveryChannels']) > 0:
        # update delivery channel
        snsTopicARN = topicArn.format(region, audit_account)
        channel_name = channels_response['DeliveryChannels'][0]['name']
        channel_dict = {
            'name': channel_name,
            's3BucketName': s3BucketName,
            's3KeyPrefix': org_id,
            'snsTopicARN': snsTopicARN,
            'configSnapshotDeliveryProperties': {
                'deliveryFrequency': 'TwentyFour_Hours'
            }
        }
        try:
            config_client.put_delivery_channel(
                DeliveryChannel=channel_dict
            )
            status = True
        except Exception as ex:
            LOGGER.error(f"put_delivery_channel(..) call failed for Account {account_id} in Region {region}")
            LOGGER.error(str(ex))
            raise PutDeliveryChannelFailed(f"put_delivery_channel(..) call failed for Account {account_id} in Region {region}")
        finally:
            return status
    else:
        # no delivery channel, so create it
        LOGGER.info(f"No delivery channel found for Account {account_id} in Region {region}. Creating it now.")
        snsTopicARN = topicArn.format(region, audit_account)
        status = create_member_delivery_channel(event, config_client, s3BucketName, snsTopicARN)
    return status

def create_member_delivery_channel(event, config_client, s3BucketName, snsTopicARN):
    status = False
    org_id = event['org_id']
    account_id = event['member_account']
    region = event['member_region']
    channel_dict = {
        'name': org_delivery_channel_name,
        's3BucketName': s3BucketName,
        's3KeyPrefix': org_id,
        'snsTopicARN': snsTopicARN,
        'configSnapshotDeliveryProperties': {
            'deliveryFrequency': 'TwentyFour_Hours'
        }
    }
    try:
        config_client.put_delivery_channel(
            DeliveryChannel=channel_dict
        )
        status = True
    except Exception as ex:
        LOGGER.error(f"put_delivery_channel(..) call failed for Account {account_id} in Region {region}")
        LOGGER.error(str(ex))
        raise PutDeliveryChannelFailed(f"put_delivery_channel(..) call failed for Account {account_id} in Region {region}")
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
    assume_role = event['assume_role']    
    status = update_member_channel(event)
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
            'assume_role': assume_role,
            'modify_delivery_channel_success': status
        }
    }

