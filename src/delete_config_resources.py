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

def json_serial(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError('Type %s not serializable' % type(obj))

class DeleteFailedException(Exception):
    pass

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

def delete_config_recorders(member_session, aws_account_number, region):
    LOGGER.info(f"Deleting Config Recorders for Account: {aws_account_number} in Region: {region} ..")
    is_successful = False
    try:
        config_client = member_session.client('config', endpoint_url=f"https://config.{region}.amazonaws.com", region_name=region)
        response = config_client.describe_configuration_recorders()
        if response['ConfigurationRecorders']:
            for recorder in response['ConfigurationRecorders']:
                name = recorder['name']
                try:
                    config_client.delete_configuration_recorder(ConfigurationRecorderName=name)
                    LOGGER.info(f"Deleted Configuration Recorder: {name}")
                    is_successful = True
                except Exception as ex1:
                    LOGGER.error(f"Failed in delete_configuration_recorder(..) for Configuration Recorder: {name}")
                    LOGGER.error(str(ex1))
                    is_successful = False           
        else:
            LOGGER.info("No Configuration Recorders found")
            is_successful = True
    except Exception as ex:
        LOGGER.error(f"Failed in describe_configuration_recorders(..) for Account: {aws_account_number} in Region: {region}")
        LOGGER.error(str(ex))
        raise DeleteFailedException('Failed in describe_configuration_recorders(..) for Account: {} in Region: {}'.format(aws_account_number, region))
    if not is_successful:
        raise DeleteFailedException('Failed to delete configuration recorders for Account: {} in Region: {}'.format(aws_account_number, region))
    

def delete_channels(member_session, aws_account_number, region):
    LOGGER.info(f"Deleting Delivery Channels for Account: {aws_account_number} in Region: {region} ..")
    is_successful = False
    try:
        config_client = member_session.client('config', endpoint_url=f"https://config.{region}.amazonaws.com", region_name=region)
        response = config_client.describe_delivery_channels()
        if response['DeliveryChannels']:
            for channel in response['DeliveryChannels']:
                name = channel['name']
                try:
                    config_client.delete_delivery_channel(DeliveryChannelName=name)
                    LOGGER.info(f"Deleted Delivery Channel: {name}")
                    is_successful = True
                except Exception as ex1:
                    LOGGER.error(f"Failed in delete_delivery_channel(..) for Delivery Channel: {name}" )
                    LOGGER.error(str(ex))
                    is_successful = False
        else:
            LOGGER.info("No Delivery Channels found")
            is_successful = True
    except Exception as ex:
        LOGGER.error(f"Failed in describe_delivery_channels(..) for Account: {aws_account_number} in Region: {region}")
        LOGGER.error(str(ex))
        raise DeleteFailedException('Failed to delete delivery channels for Account: {} in Region: {}'.format(aws_account_number, region))
    if not is_successful:
        raise DeleteFailedException('Failed to delete delivery channels for Account: {} in Region: {}'.format(aws_account_number, region))


def delete_authorizations(member_session, aws_account_number, region):
    LOGGER.info(f"Deleting Aggregation Authorizations for Account: {aws_account_number} in Region: {region} ..")
    is_successful = False
    try:
        config_client = member_session.client('config', endpoint_url=f"https://config.{region}.amazonaws.com", region_name=region)
        response = config_client.describe_aggregation_authorizations()
        if response['AggregationAuthorizations']:
            for auth in response['AggregationAuthorizations']:
                arn = auth['AggregationAuthorizationArn']
                authAccountId = auth['AuthorizedAccountId']
                authRegion = auth['AuthorizedAwsRegion']
                try:
                    config_client.delete_aggregation_authorization(AuthorizedAccountId=authAccountId, AuthorizedAwsRegion=authRegion)
                    LOGGER.info(f"Deleted Aggregation Authorization: {arn}")
                    is_successful = True
                except Exception as ex1:
                    LOGGER.error(f"Failed in delete_aggregation_authorization(..) for Account: {aws_account_number} in Region: {region}")
                    LOGGER.error(str(ex1))
                    is_successful = False
        else:
            LOGGER.info("No Aggregation Authorizations found")
            is_successful = True
    except Exception as ex:
        LOGGER.error(f"Failed in describe_aggregation_authorizations(..) for Account: {aws_account_number} in Region: {region}")
        LOGGER.error(str(ex))
        raise DeleteFailedException('Failed to delete aggregation authorizations for Account: {} in Region: {}'.format(aws_account_number, region))
    if not is_successful:
        raise DeleteFailedException('Failed to delete aggregation authorizations for Account: {} in Region: {}'.format(aws_account_number, region))

def lambda_handler(event, context):
    LOGGER.info(f"REQUEST RECEIVED: {json.dumps(event, default=str)}")
    account_id = event['member_account_id']
    org_id = event['org_id']
    assume_role_name = event['assume_role']
    ct_regions = get_ct_regions()
    member_session = assume_role(org_id, account_id, assume_role_name)
    for region in ct_regions:
        try:
            delete_config_recorders(member_session, account_id, region)
        except DeleteFailedException as dfe:
            raise dfe
    for region in ct_regions:
        try:
            delete_channels(member_session, account_id, region)
        except DeleteFailedException as dfe:
            raise dfe
    for region in ct_regions:
        try:
            delete_authorizations(member_session, account_id, region)
        except DeleteFailedException as dfe:
            raise dfe
    output = {
        'status': True
    }
    return output



