import os
import sys
import json
import boto3
import urllib3
import logging
from datetime import date, datetime
import time
from botocore.exceptions import ClientError

stackInstanceCheckCount=120
stackInstanceCheckFrequencySeconds=30
stackSetCheckFrequencySeconds=30
stackSetCheckCount=120
stackSetOpCheckFrequencySeconds=30
ct_config_recorder_name = 'aws-controltower-BaselineConfigRecorder'
org_config_recorder_role_name = 'aws-controltower-ConfigRecorderRole-customer-created'
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

class OUMismatchException(Exception):
    pass

class InvalidOUException(Exception):
    pass

class PutConfigRecorderFailed(Exception):
    pass

def json_serial(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError('Type %s not serializable' % type(obj))

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

def is_valid_ou(ou_id, ct_home_region):
    valid_ou = False
    org_client = session.client('organizations', region_name=ct_home_region)
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

def get_ou(account_id, ct_home_region):
    org_client = session.client('organizations', region_name=ct_home_region)
    list_parents_response = org_client.list_parents(ChildId=account_id)
    parent_id = list_parents_response['Parents'][0]['Id']
    parent_type = list_parents_response['Parents'][0]['Type']
    if parent_type != 'ROOT':
        return parent_id
    return None

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

def wait_on_stackset_operation(ct_session, operationId):
    cf_client = ct_session.client('cloudformation')
    op_status = ''
    try:
        stackset_op_response = cf_client.describe_stack_set_operation(StackSetName=kyndryConfigRecorderRoleStackSetName,
        OperationId=operationId)
        LOGGER.info(f"{json.dumps(stackset_op_response, default=json_serial)}")
        op_status = stackset_op_response['StackSetOperation']['Status']
        if op_status == 'RUNNING':
            LOGGER.info(f"StackSet {kyndryConfigRecorderRoleStackSetName} Operation with Id {operationId} is Running")
            time.sleep(stackSetOpCheckFrequencySeconds)
    except Exception as ex:
        LOGGER.info(f"Error waiting on StackSet {kyndryConfigRecorderRoleStackSetName} Operation Id: {operationId}")
        LOGGER.error(str(ex))
    return op_status

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
            status = response['StackInstance']['Status']
            if status == 'CURRENT' or status == 'OUTDATED':
                return True
    except Exception as ex:
        LOGGER.error(f"Failed in call describe_stack_instance(..) for StackSet {stackSetName} for Account {accountId} in Region {region}")
        LOGGER.error(str(ex))
    return False

def wait_on_stack(ct_session, accountId, region):
    cf_client = ct_session.client('cloudformation', endpoint_url=f"https://cloudformation.{region}.amazonaws.com", region_name=region)
    stackId = ''
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

def create_ct_exec_role(ct_session, ou_id, accountId, region):
    cf_client = ct_session.client('cloudformation')
    stackSetName = 'AWSControlTowerExecutionRole'
    try:
        # get stackset
        # CallAs = 'SELF' since running from Org Management Account
        parent_ou_id = get_ou(accountId, region)
        if parent_ou_id != ou_id:
            LOGGER.error(f"Parent OU: {parent_ou_id} of Account: {accountId} does not match Function Parameter (ou_id): {ou_id}")
            raise OUMismatchException(f"Parent OU: {parent_ou_id} of Account: {accountId} does not match Function Parameter (ou_id): {ou_id}")
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

def create_configrecorder_stack_instance(ct_session, ou_id, region):
    cf_client = ct_session.client('cloudformation')
    instance_count = 0
    try:
        targets = {
            'OrganizationalUnitIds': [ ou_id ]
        }
        op_prefs = {
            'RegionConcurrencyType': 'PARALLEL'
        }
        launch_response = cf_client.create_stack_instances(
            StackSetName=kyndryConfigRecorderRoleStackSetName,
            DeploymentTargets=targets,
            Regions=[ region ],
            OperationPreferences=op_prefs
        )
        LOGGER.info(f"StackInstance for StackSet {kyndryConfigRecorderRoleStackSetName} launched with Operation Id: {launch_response['OperationId']}")        
        while True:
            operation_status = wait_on_stackset_operation(ct_session, launch_response['OperationId'])
            if operation_status == 'FAILED' or operation_status == 'STOPPED':
                LOGGER.info(f"StackSet {kyndryConfigRecorderRoleStackSetName} Operation failed. Exiting here !")
                raise SystemExit()
            elif operation_status == 'SUCCEEDED':
                LOGGER.info(f"StackSet {kyndryConfigRecorderRoleStackSetName} Operation successful.")
                break
    except Exception as ex:
        LOGGER.error(f"Launch of StackInstance for StackSet {kyndryConfigRecorderRoleStackSetName} failed !")
        LOGGER.error(str(ex))

def configrecorder_stackset_exists(ct_session, master_account_id):
    cf_client = ct_session.client('cloudformation')
    try:
        response = cf_client.describe_stack_set(StackSetName=kyndryConfigRecorderRoleStackSetName)
        if response['StackSet'] and response['StackSet']['Status'] == 'ACTIVE':
            return True
    except Exception as ex:
        LOGGER.error(f"Failed in describe_stack_set(..)")
        LOGGER.error(str(ex))
    return False

def create_configrecorder_stackset(ct_session, master_account_id, s3bucket, s3key):
    cf_client = ct_session.client('cloudformation')
    templateUrl = 'https://s3.amazonaws.com/'+s3bucket+'/'+s3key
    adminRoleArn = 'arn:aws:iam::{}:role/AWSCloudFormationStackSetAdministrationRole'.format(master_account_id)
    execRoleName = 'AWSCloudFormationStackSetExecutionRole'
    try:
        create_response = cf_client.create_stack_set(
            StackSetName=kyndryConfigRecorderRoleStackSetName,
            Description='Create MyOrg ConfigRecorder Role in member accounts',
            TemplateURL=templateUrl,
            Capabilities=['CAPABILITY_NAMED_IAM'],
            PermissionModel='SERVICE_MANAGED',
            AutoDeployment={
                'Enabled': True,
                'RetainStacksOnAccountRemoval': False
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

def create_configrecorder_role(ct_session, ou_id, accountId, region):
    cf_client = ct_session.client('cloudformation')
    LOGGER.info(f"Launching StackInstances for StackSet {kyndryConfigRecorderRoleStackSetName} ..")
    try:
        LOGGER.info(f"Launching StackSet {kyndryConfigRecorderRoleStackSetName} for Organizational Unit: {ou_id} in Region {region} ..")
        targets = {
            'OrganizationalUnitIds': [ ou_id ]
        }
        op_prefs = {
            'RegionConcurrencyType': 'PARALLEL'
        }
        launch_response = cf_client.create_stack_instances(
            StackSetName=kyndryConfigRecorderRoleStackSetName,
            DeploymentTargets=targets,
            Regions=[ region ],
            OperationPreferences=op_prefs
        )
        LOGGER.info(f"StackInstance for StackSet {kyndryConfigRecorderRoleStackSetName} launched with Operation Id: {launch_response['OperationId']}")        
        while True:
            operation_status = wait_on_stackset_operation(ct_session, launch_response['OperationId'])
            if operation_status == 'FAILED' or operation_status == 'STOPPED':
                LOGGER.info(f"StackSet {kyndryConfigRecorderRoleStackSetName} Operation failed. Exiting here !")
                raise SystemExit()
            elif operation_status == 'SUCCEEDED':
                LOGGER.info(f"StackSet {kyndryConfigRecorderRoleStackSetName} Operation successful.")
                break
    except Exception as ex:
        LOGGER.error(f"Launch of StackInstance for StackSet {kyndryConfigRecorderRoleStackSetName} failed !")
        LOGGER.error(str(ex))

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

def update_member_recorder(org_id, accountId, region, role_name):
    status = False
    roleArn = 'arn:aws:iam::{}:role/{}'.format(accountId, org_config_recorder_role_name)
    member_session = assume_role(org_id, accountId, role_name)
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
            status = True
        except Exception as ex:
            LOGGER.error(f"put_configuration_recorder(..) call failed for Account {accountId} in Region {region}")
            LOGGER.error(str(ex))
            raise PutConfigRecorderFailed(f"put_configuration_recorder(..) call failed for Account {accountId} in Region {region}")
        finally:
            return status
    else:
        # no configuration recorder, so create it
        LOGGER.info(f"No configuration recorder found for Account {accountId} in Region {region}. Creating it now.")
        status = create_member_config_recorder(config_client, accountId, region, roleArn)
    return status

def create_member_config_recorder(config_client, accountId, region, roleArn):
    status = False
    recorder_dict = {
        'name': ct_config_recorder_name,
        'roleARN': roleArn,
        'recordingGroup': {
            'allSupported': True,
            'includeGlobalResourceTypes': True
        }
    }
    try:
        config_client.put_configuration_recorder(ConfigurationRecorder=recorder_dict)
        status = True
    except Exception as ex:
        LOGGER.error(f"put_configuration_recorder(..) call failed for Account {accountId} in Region {region}")
        LOGGER.error(str(ex))
        raise PutConfigRecorderFailed(f"put_configuration_recorder(..) call failed for Account {accountId} in Region {region}")
    finally:
        return status

def lambda_handler(event, context):
    LOGGER.info(f"REQUEST RECEIVED: {json.dumps(event, default=str)}")
    org_id = event['org_id']
    ou_id = event['org_unit_id']
    ct_home_region = event['ct_home_region']
    s3bucket = event['s3_bucket']
    s3key = event['s3_key']
    master_account_id = boto3.client('sts').get_caller_identity()['Account']
    account_id = event['member_account']
    logarchive_account = event['logarchive_account']
    audit_account = event['audit_account']
    account_region = event['member_region']
    assume_role = event['assume_role']
    if not is_valid_ou(ou_id, ct_home_region):
        LOGGER.error(f"Invalid Organizational Unit {ou_id}. Exiting now.")
        raise InvalidOUException(f"Invalid Organizational Unit {ou_id}. Exiting now.")
    # one account at a time
    create_ct_exec_role(session, ou_id, account_id, ct_home_region)
    reset_stackinstance_check_count()
    if not configrecorder_stackset_exists(session, master_account_id):
        create_configrecorder_stackset(session, master_account_id, s3bucket, s3key)
        create_configrecorder_role(session, ou_id, account_id, ct_home_region)
    else:
        create_configrecorder_stack_instance(session, ou_id, ct_home_region)
    status = update_member_recorder(org_id, account_id, account_region, assume_role)
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
            'modify_config_recorder_success': status
        }
    }