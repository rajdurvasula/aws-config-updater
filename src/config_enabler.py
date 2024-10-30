import os
import sys
import json
import boto3
import urllib3
import logging
from datetime import datetime
import time
from botocore.exceptions import ClientError

#
# These can be parameterized. Using these for now.
#
stackInstanceCheckCount=120
stackInstanceCheckFrequencySeconds=30
stackSetCheckFrequencySeconds=30
stackSetCheckCount=120
org_config_recorder_name = 'aws-controltower-ConfigRecorderRole-customer-created'
org_delivery_channel_name = 'aws-controltower-ConfigDeliveryChannel-customer-created'
default_recorder_name = 'aws-controltower-ConfigRecorderRole'
ct_log_bucket = 'aws-controltower-logs-{}-{}'
topicArn = 'arn:aws:sns:{}:{}:aws-controltower-AllConfigNotifications'
kyndryConfigRecorderRoleStackSetName = 'MyOrgConfigRecorderRole'

LOGGER = logging.getLogger()
if 'log_level' in os.environ:
    LOGGER.setLevel(os.environ['log_level'])
    LOGGER.info('Log level set to %s' % LOGGER.getEffectiveLevel())
else:
    LOGGER.setLevel(logging.ERROR)

session = boto3.Session()

def decrement_stackset_check_count():
    global stackSetCheckCount
    stackSetCheckCount = stackSetCheckCount -1
    LOGGER.info(f"{stackSetCheckCount} more times to check ..")

def decrement_stackinstance_check_count():
    global stackInstanceCheckCount
    stackInstanceCheckCount = stackInstanceCheckCount - 1
    LOGGER.info(f"{stackInstanceCheckCount} more times to check ..")

def reset_stackcheck_check_count():
    global stackSetCheckCount
    stackSetCheckCount=120

def reset_stackinstance_check_count():
    global stackInstanceCheckCount
    stackInstanceCheckCount = 120

def send(event, context, response_status, response_data, physical_resource_id=None, no_echo=False):
    response_url = event['ResponseURL']
    print(response_url)
    logstream = context.log_stream_name
    response_body = {}
    response_body['Status'] = response_status
    response_body['Reason'] = 'Check details in log stream: '+logstream
    response_body['PhysicalResourceId'] = physical_resource_id or logstream
    response_body['StackId'] = event['StackId']
    response_body['RequestId'] = event['RequestId']
    response_body['LogicalResourceId'] = event['LogicalResourceId']
    response_body['NoEcho'] = no_echo
    response_body['Data'] = response_data

    json_response_body = json.dumps(response_body)
    print('Response Body:\n'+json_response_body)
    headers = {
        'content-type': '',
        'content-length': str(len(json_response_body))
    }
    http = urllib3.PoolManager()
    try:
        response = http.request('PUT', response_url, body=json_response_body, headers=headers)
        print('HTTP Status: '+response.reason)
    except Exception as ex:
        print("send(..) failed executing requests.put(..): "+str(ex))

def get_ct_regions(ct_session):
    cf = ct_session.client('cloudformation')
    region_set = set()
    try:
        # stack instances are outdated
        stacks = cf.list_stack_instances(
            StackSetName='AWSControlTowerBP-BASELINE-CLOUDWATCH')
        for stack in stacks['Summaries']:
            LOGGER.info(f"Region: {stack['Region']}")
            region_set.add(stack['Region'])
    except Exception as ex:
        LOGGER.warning("Control Tower StackSet not found in this Region")
        LOGGER.warning(str(ex))
    LOGGER.info(f"Control Tower Regions: {list(region_set)}")
    return list(region_set)

def get_ou(account_id):
    org_client = session.client('organizations', region_name=os.environ['ct_home_region'])
    list_parents_response = org_client.list_parents(ChildId=account_id)
    parent_id = list_parents_response['Parents'][0]['Id']
    parent_type = list_parents_response['Parents'][0]['Type']
    if parent_type != 'ROOT':
        return parent_id
    return None

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

def wait_for_stackinstance(ct_session, accountId, region, stackSetName):
    global stackInstanceCheckCount
    time.sleep(stackInstanceCheckFrequencySeconds)
    cf_client = ct_session.client('cloudformation')
    try:
        response = cf_client.describe_stack_instance(
            StackSetName=stackSetName,
            StackInstanceAccount=accountId,
            StackInstanceRegion=region,
            CallAs='SELF'
        )
        if response['StackInstance']:
            if response['StackInstance']['Status'] == 'CURRENT':
                return True
    except Exception as ex:
        LOGGER.error(f"Failed in call describe_stack_instance(..) for StackSet {stackSetName} for Account {accountId} in Region {region}")
        LOGGER.error(str(ex))
    return False

def create_ct_exec_role(ct_session, accountId, region):
    cf_client = ct_session.client('cloudformation')
    stackSetName = 'AWSControlTowerExecutionRole'
    try:
        # get stackset
        # CallAs = 'SELF' since running from Org Management Account
        ou_id = get_ou(accountId)
        targets = {
            'OrganizationalUnitIds': [ ou_id ]
        }
        response = cf_client.create_stack_instances(
            StackSetName=stackSetName,
            DeploymentTargets=targets,
            Regions=[region],
            CallAs='SELF'
        )
        LOGGER.info(f"Stack Instance launched for StackSet {stackSetName} with OperationId: {response['OperationId']}")
    except Exception as ex:
        LOGGER.error(f"Create Stack Instance for StackSet {stackSetName} failed !")
        LOGGER.error(str(ex))
    # check stack instance is availabe
    while stackInstanceCheckCount > 0:
        if wait_for_stackinstance(ct_session, accountId, region, stackSetName):
            LOGGER.info(f"Stack Instance for StackSet {stackSetName} found in Account {accountId} in Region {region}")
            break
        decrement_stackinstance_check_count()

def wait_for_stackset(ct_session):
    global stackSetCheckCount
    time.sleep(stackSetCheckFrequencySeconds)
    cf_client = ct_session.client('cloudformation')
    try:
        response = cf_client.describe_stack_set(StackSetName=kyndryConfigRecorderRoleStackSetName)
        if response['StackSet']:
            if response['StackSet']['Status'] == 'ACTIVE':
                return True
    except Exception as ex:
        LOGGER.error(f"Failed in call describe_stack_set(..) for StackSet {kyndryConfigRecorderRoleStackSetName}")
        LOGGER.error(str(ex))
    return False

def create_configrecorder_role(ct_session, accountId, region, s3bucket, s3key):
    cf_client = ct_session.client('cloudformation')
    templateUrl = 'https://s3.amazonaws.com/'+s3bucket+'/'+s3key    
    try:
        create_response = cf_client.create_stack_set(
            StackSetName=kyndryConfigRecorderRoleStackSetName,
            Description='Create MyOrg ConfigRecorder Role in member accounts',
            TemplateURL=templateUrl,
            Capabilities=['CAPABILITY_NAMED_IAM'],
            PermissionModel='SERVICE_MANAGED',
            AutoDeployment={
                'Enabled': False
            }
        )
        LOGGER.info(f"StackSet {kyndryConfigRecorderRoleStackSetName} created with Id: {create_response['StackSetId']}")
    except Exception as ex:
        LOGGER.error(f"Create StackSet {kyndryConfigRecorderRoleStackSetName} failed !")
        LOGGER.error(str(ex))
    # check StackSet status
    while stackSetCheckCount > 0:
        if wait_for_stackset(ct_session):
            break
        decrement_stackset_check_count()
    try:
        ou_id = get_ou(accountId)
        LOGGER.info(f"Launching StackSet {kyndryConfigRecorderRoleStackSetName} for Organizational Unit: {ou_id} in Region {region} ..")
        targets = {
            'OrganizationalUnitIds': [ ou_id ]
        }
        launch_response = cf_client.create_stack_instances(
            StackSetName=kyndryConfigRecorderRoleStackSetName,
            DeploymentTargets=targets,
            Regions=[ region ]
        )
        LOGGER.info(f"StackSet {kyndryConfigRecorderRoleStackSetName} launched with Operation Id: {launch_response['OperationId']}")
    except Exception as ex:
        LOGGER.error(f"Launch of StackSet {kyndryConfigRecorderRoleStackSetName} failed !")
        LOGGER.error(str(ex))

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
    cf_client = ct_session.client('cloudformation')
    region_set = set()
    try:
        stacks = cf_client.list_stack_instances(
            StackSetName='AWSControlTowerBP-BASELINE-CLOUDWATCH')
        for stack in stacks['Summaries']:
            region_set.add(stack['Region'])
    except Exception as ex:
        LOGGER.warning("Control Tower StackSet not found in this Region")
        LOGGER.error(str(ex))
    LOGGER.info(f"Control Tower Regions: {list(region_set)}")
    return list(region_set)

def is_ct_config_recorder(accountId, recorder):
    defaultRoleArn = 'arn:aws:iam::{}:role/{}'.format(accountId, default_recorder_name)
    orgRoleArn = org_config_recorder_name.format(accountId, org_config_recorder_name)
    if recorder['roleARN'] == defaultRoleArn or recorder['roleARN'] == orgRoleArn:
        return True
    return False

def is_ct_delivery_channel(accountId, region, channel):
    snsTopicARN = topicArn.format(region, os.environ['audit_account'])
    s3BucketName = ct_log_bucket.format(os.environ['logarchive_account'], os.environ['ct_home_region'])
    if channel['s3BucketName'] == s3BucketName and channel['snsTopicARN'] == snsTopicARN:
        return True
    return False

def wait_on_stack(ct_session, accountId, region):
    cf_client = ct_session.client('cloudformation', endpoint_url=f"https://cloudformation.{region}.amazonaws.com", region_name=region)
    try:
        list_response = cf_client.list_stack_instances(
            StackSetName=kyndryConfigRecorderRoleStackSetName,
            StackInstanceAccount=accountId,
            StackInstanceRegion=region)
        if len(list_response['Summaries']) > 0:
            stackId = list_response['Summaries'][0]['StackId']
            waiter = cf_client.get_waiter('stack_create_complete')
            LOGGER.info(f"Waiting on Stack {stackId} in Account {accountId} for CREATE_COMPLETE status..")
            waiter.wait(StackName=stackId)
    except Exception as ex:
        LOGGER.error(f"Failed while waiting on {stackId} status=CREATE_COMPLETE for Account {accountId} in Region {region}")
        LOGGER.error(str(ex))


def update_member_recorder(accountId, ct_regions):
    roleArn = 'arn:aws:iam::{}:role/{}'.format(accountId, org_config_recorder_name)
    member_session = assume_role(accountId, os.environ['assume_role'])
    for region in ct_regions:
        config_client = member_session.client('config', endpoint_url=f"https://config.{region}.amazonaws.com", region_name=region)
        recorders_response = config_client.describe_configuration_recorders()
        if len(recorders_response['ConfigurationRecorders']) > 0:
            # update 1st recorder
            recorder_name = recorders_response['ConfigurationRecorders'][0]['name']
            recorder_dict = {
                'name': recorder_name,
                'roleARN': roleArn,
                'recordingGroup': {
                    'allSupported': True,
                    'includeGlobalResourceTypes': True
                }
            }
            try:
                config_client.put_configuration_recorder(ConfigurationRecorder=recorder_dict)
            except Exception as ex:
                LOGGER.error(f"put_configuration_recorder(..) call failed for Account {accountId} in Region {region}")
                LOGGER.error(str(ex))
        else:
            # no configuration recorder, so create it
            LOGGER.info(f"No configuration recorder found for Account {accountId} in Region {region}. Creating it now.")
            create_member_config_recorder(config_client, accountId, region, roleArn)

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

def create_member_authorization(accountId, ct_regions):
    member_session = assume_role(accountId, os.environ['assume_role'])
    for region in ct_regions:
        config_client = member_session.client('config', endpoint_url=f"https://config.{region}.amazonaws.com", region_name=region)
        try:
            config_client.put_aggregation_authorization(
                AuthorizedAccountId=os.environ['audit_account'],
                AuthorizedAwsRegion=os.environ['ct_home_region']
            )
        except Exception as ex:
            LOGGER.error(f"put_aggregation_authorization(..) failed for Account {accountId} in Region {region}")
            LOGGER.error(str(ex))

def modify_member_config(accountId, ct_regions):
        update_member_recorder(accountId, ct_regions)
        update_member_channel(accountId, ct_regions)
        create_member_authorization(accountId, ct_regions)

def create_member_config_recorder(config_client, accountId, region, roleArn):
    recorder_dict = {
        'name': org_config_recorder_name,
        'roleARN': roleArn,
        'recordingGroup': {
            'allSupported': True,
            'includeGlobalResourceTypes': True
        }
    }
    try:
        config_client.put_configuration_recorder(ConfigurationRecorder=recorder_dict)
    except Exception as ex:
        LOGGER.error(f"put_configuration_recorder(..) call failed for Account {accountId} in Region {region}")
        LOGGER.error(str(ex))
    
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
    partition = context.invoked_function_arn.split(":")[1]
    ou_id = os.environ['org_unit_id']
    admin_account_id = context.invoked_function_arn.split(':')[4]
    ct_regions = get_ct_regions(session)
    ct_region = os.environ['ct_home_region']
    s3bucket = os.environ['S3Bucket']
    s3key = os.environ['S3Key']
    if not is_valid_ou(ou_id):
        LOGGER.error(f"Invalid Organizational Unit {ou_id}. Exiting now.")
        raise SystemExit()
    accounts = get_ou_accounts(ou_id)
    # invocation through Template
    if 'RequestType' in event and (event['RequestType'] == 'Create' or event['RequestType'] == 'Delete' or event['RequestType'] == 'Update'):
        action = event['RequestType']
        if action == 'Create':
            for member_account_id in accounts:
                # AWSControlTowerExecutionRole
                create_ct_exec_role(session, member_account_id, ct_region)
                reset_stackinstance_check_count()
                # MyOrgConfigRecorderRole
                create_configrecorder_role(session, member_account_id, ct_region, s3bucket, s3key)
                # Wait on Role creation
                wait_on_stack(session, member_account_id, ct_region)
                # Modify Config resources on member account
                modify_member_config(member_account_id, ct_regions)
        response_data = {}
        send(event, context, "SUCCESS", response_data)
        if action == "Delete":
            raise SystemExit()
    else:
        for member_account_id in accounts:
            # direct lambda invocation
            create_ct_exec_role(session, member_account_id, ct_region)
            reset_stackinstance_check_count()
            create_configrecorder_role(session, member_account_id, ct_region, s3bucket, s3key)
            wait_on_stack(session, member_account_id, ct_region)
            # Modify Config resources on member account
            modify_member_config(member_account_id, ct_regions)

