import os
import sys
import json
import boto3
import urllib3
import logging
from datetime import date, datetime
import time
from botocore.exceptions import ClientError

# currently only these aggregation regions are visible in CT enrolled accounts
aggregation_regions = [ 'ap-southeast-2', 'eu-west-1', 'us-east-1', 'us-east-2', 'us-west-2' ]

LOGGER = logging.getLogger()
if 'log_level' in os.environ:
    LOGGER.setLevel(os.environ['log_level'])
    LOGGER.info('Log level set to %s' % LOGGER.getEffectiveLevel())
else:
    LOGGER.setLevel(logging.ERROR)

session = boto3.Session()

class DescribeAggregationAuthFailed(Exception):
    pass

class PutAggregationAuthFailed(Exception):
    pass

class DeleteAggregationAuthFailed(Exception):
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

def delete_aggr_authorizations(account_id, config_client, region):
    status = False
    try:
        response = config_client.describe_aggregation_authorizations()
        if response['AggregationAuthorizations']:
            for auth in response['AggregationAuthorizations']:
                arn = auth['AggregationAuthorizationArn']
                authAccountId = auth['AuthorizedAccountId']
                authRegion = auth['AuthorizedAwsRegion']
                try:
                    config_client.delete_aggregation_authorization(AuthorizedAccountId=authAccountId, AuthorizedAwsRegion=authRegion)
                    LOGGER.info(f"Deleted Aggregation Authorization ARN: {arn}")
                    status = True
                except Exception as ex1:
                    LOGGER.error(f"delete_aggregation_authorization(..) failed for Account {account_id} in Region: {region}")
                    LOGGER.error(str(ex1))
                    raise DeleteAggregationAuthFailed(f"delete_aggregation_authorization(..) failed for Account {account_id} in Region: {region}")
        else:
            LOGGER.info(f"No Aggregation Authorizations found for Account: {account_id} in Region: {region}")
    except Exception as ex:
        LOGGER.error(f"describe_aggregation_authorizations failed for Account: {account_id} in Region: {region}")
        LOGGER.error(str(ex))
        raise DescribeAggregationAuthFailed(f"describe_aggregation_authorizations failed for Account: {account_id} in Region: {region}")
    return status

def recreate_member_authorization(event):
    status = False
    org_id = event['org_id']
    account_id = event['member_account']
    region = event['member_region']
    audit_account = event['audit_account']
    role_name = event['assume_role']
    member_session = assume_role(org_id, account_id, role_name)
    config_client = member_session.client('config', endpoint_url=f"https://config.{region}.amazonaws.com", region_name=region)
    # SCP doesn't allow deletion of aggregation authorizations
    #delete_aggr_authorizations(account_id, config_client, region)
    for agg_region in aggregation_regions:
        try:
            config_client.put_aggregation_authorization(
                AuthorizedAccountId=audit_account,
                AuthorizedAwsRegion=agg_region
            )
            status = True
            LOGGER.info(f"put_aggregation_authorization(..) for Account {account_id} in Aggregation Region {agg_region} in Account Region: {region} successful")
        except Exception as ex:
            LOGGER.error(f"put_aggregation_authorization(..) failed for Account {account_id} in Aggregation Region {agg_region} in Account Region: {region}")
            LOGGER.error(str(ex))
            raise PutAggregationAuthFailed(f"put_aggregation_authorization(..) failed for Account {account_id} in Aggregation Region {agg_region} in Account Region: {region}")
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
    status = recreate_member_authorization(event)
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
            'modify_aggr_authorizations_success': status
        }
    }
