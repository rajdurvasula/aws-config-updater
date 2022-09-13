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

def get_ct_regions():
    cf = session.client('cloudformation')
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

def get_config_recorders(member_session, aws_account_number, region):
    LOGGER.info(f"Configuration Recorders for Region: {region}")
    config_recorders = []
    try:
        config_client = member_session.client('config', endpoint_url=f"https://config.{region}.amazonaws.com", region_name=region)
        response = config_client.describe_configuration_recorders()
        if response['ConfigurationRecorders']:
            for recorder in response['ConfigurationRecorders']:
                config_recorders.append(recorder)
    except Exception as ex:
        LOGGER.error(f"Failed in call describe_configuration_recorders(..) for Account {aws_account_number} in Region {region}")
        LOGGER.error(str(ex))
        raise VerifyFailedException('failed to verify configuration recorders for Account: {} in Region: {}'.format(aws_account_number, region))
    return config_recorders

def get_delivery_channels(member_session, aws_account_number, region):
    LOGGER.info(f"Delivery Channels for Region: {region}")
    delivery_channels = []
    try:
        config_client = member_session.client('config', endpoint_url=f"https://config.{region}.amazonaws.com", region_name=region)
        response = config_client.describe_delivery_channels()
        if response['DeliveryChannels']:
            for channel in response['DeliveryChannels']:
                delivery_channels.append(channel)
    except Exception as ex:
        LOGGER.error(f"Failed in call describe_delivery_channels(..) for Account {aws_account_number} in Region {region}")
        LOGGER.error(str(ex))
        raise VerifyFailedException('failed to verify delivery channels for Account: {} in Region: {}'.format(aws_account_number, region))
    return delivery_channels

def get_aggregation_authorizations(member_session, aws_account_number, region):
    LOGGER.info(f"Aggregation Authorizations for Region: {region}")
    aggregation_authorizations = []
    try:
        config_client = member_session.client('config', endpoint_url=f"https://config.{region}.amazonaws.com", region_name=region)
        response = config_client.describe_aggregation_authorizations()
        if response['AggregationAuthorizations']:
            for authorization in response['AggregationAuthorizations']:
                aggregation_authorizations.append(authorization)
    except Exception as ex:
        LOGGER.error(f"Failed in call describe_aggregation_authorizations(..) for Account {aws_account_number} in Region {region}")
        LOGGER.error(str(ex))
        raise VerifyFailedException('failed to verify aggregation authorizations for Account: {} in Region: {}'.format(aws_account_number, region))
    return aggregation_authorizations

    
def lambda_handler(event, context):
    LOGGER.info(f"REQUEST RECEIVED: {json.dumps(event, default=str)}")
    org_id = event['org_id']
    ou_id = event['org_unit_id']
    ct_home_region = event['ct_home_region']
    s3bucket = event['s3_bucket']
    s3key = event['s3_key']
    logarchive_account = event['logarchive_account']
    audit_account = event['audit_account']    
    account_id = event['member_account']
    region = event['member_region']
    assume_role_name = event['assume_role']
    #ct_regions = get_ct_regions()
    member_session = assume_role(org_id, account_id, assume_role_name)
    config_recorders = []
    delivery_channels = []
    aggregation_authorizations = []
    #for region in ct_regions:
    try:
        config_recorders.extend(get_config_recorders(member_session, account_id, region))
    except VerifyFailedException as vfe:
        raise vfe
    #for region in ct_regions:
    try:
        delivery_channels.extend(get_delivery_channels(member_session, account_id, region))
    except VerifyFailedException as vfe:
        raise vfe
    #for region in ct_regions:
    try:
        aggregation_authorizations.extend(get_aggregation_authorizations(member_session, account_id, region))
    except VerifyFailedException as vfe:
        raise vfe
    return {
        'statusCode': 200,
        'body': {
            'org_id': org_id,
            'org_unit_id': ou_id,
            'ct_home_region': ct_home_region,
            's3_bucket': s3bucket,
            's3_key': s3key,
            'logarchive_account': logarchive_account,
            'audit_account': audit_account,
            'member_account': account_id,
            'member_region': region,
            'assume_role': assume_role_name,
            'configuration_recorders': len(config_recorders),
            'delivery_channels': len(delivery_channels),
            'aggregation_authorizations': len(aggregation_authorizations)
        }
    }