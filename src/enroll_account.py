import os
import sys
import json
import boto3
import urllib3
import logging
from random import randint
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

# Control Tower Account Factory Product
product_id = 'prod-be2kwmlhqyo3o'

class SearchProvisionedProductsFailed(Exception):
    pass

class GetProvisionedProductFailed(Exception):
    pass

class ServiceCatalogOperationBlocked(Exception):
    pass

class AccountEnrolmentFailed(Exception):
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

def get_ou_name(ou_id):
    try:
        org_client = session.client('organizations')
        response = org_client.describe_organizational_unit(OrganizationalUnitId=ou_id)
        if response['OrganizationalUnit']:
            return response['OrganizationalUnit']['Name']
    except Exception as ex:
        LOGGER.error(f"failed in describe_organizational_unit(..) for Organizaional Unit Id: {ou_id}")
        LOGGER.error(str(ex))
        raise ex
    return ''

def get_account(account_id):
    try:
        org_client = session.client('organizations')
        response = org_client.describe_account(AccountId=account_id)
        if response['Account']:
            account = response['Account']
            account_id = account['Id']
            name = account['Name']
            email = account['Email']
            status = account['Status']
            account_json = {
                'account_id': account_id,
                'name': name,
                'email': email,
                'status': status
            }
            return account_json
    except Exception as ex:
        LOGGER.error(f"Failed in describe_account(..) for Account: {account_id}")
        LOGGER.error(str(ex))
        raise ex

def search_provisioned_products(sc_client):
    ct_pp_list = []
    error_list = []
    transit_list = []
    pp_list = search_provisioned_products_full_list(sc_client)
    for pp in pp_list:
        if pp['Type'] == 'CONTROL_TOWER_ACCOUNT':
            if pp['Status'] == 'ERROR':
                error_list.append(pp)
            elif pp['Status'] == 'UNDER_CHANGE' or pp['Status'] == 'PLAN_IN_PROGRESS':
                transit_list.append(pp)
            else:
                ct_pp_list.append(pp)
    return (ct_pp_list, error_list, transit_list)

def search_provisioned_products_full_list(sc_client):
    pp_list = []
    filters = { 'Key': 'Account', 'Value': 'self' }
    response = {}
    try:
        response = sc_client.search_provisioned_products(AccessLevelFilter=filters)
        #print(json.dumps(response, default=json_serial))
        pp_list.extend(response['ProvisionedProducts'])
    except Exception as ex:
        LOGGER.error("Failed in search_provisioned_products(..)")
        LOGGER.error(str(ex))
        raise SystemExit()
    while 'NextPageToken' in response:
        next_token = response['NextPageToken']
        try:
            response = sc_client.search_provisioned_products(AccessLevelFilter=filters, PageToken=next_token)
            pp_list.extend(response['ProvisionedProducts'])
        except Exception as ex:
            LOGGER.error("Failed in search_provisioned_products(..)")
            LOGGER.error(str(ex))
            raise SearchProvisionedProductsFailed(str(ex))
    return pp_list

def get_provisioning_artifact_id(sc_client):
    try:
        response = sc_client.describe_product_as_admin(Id=product_id)
        if response['ProvisioningArtifactSummaries']:
            # get last element id
            return response['ProvisioningArtifactSummaries'][-1]['Id']
    except Exception as ex:
        LOGGER.error(f"Failed in describe_product_as_admin(..) for Product: {product_id}")
        LOGGER.error(str(ex))
        raise GetProvisionedProductFailed(f"Failed in describe_product_as_admin(..) for Product: {product_id}")

def provisioning_params(account_json, managed_ou):
    account_email = account_json['email']
    if "@org" not in account_json['email']:
        account_email = "rajasekhar.durvasula@org.com"
    params = [
        { 'Key': 'SSOUserEmail', 'Value': account_email },
        { 'Key': 'SSOUserFirstName', 'Value': 'Admin' },
        { 'Key': 'SSOUserLastName', 'Value': 'User' },
        { 'Key': 'AccountName', 'Value': account_json['name'] },
        { 'Key': 'AccountEmail', 'Value': account_email },
        { 'Key': 'ManagedOrganizationalUnit', 'Value': managed_ou }
    ]
    return params

def create_account_pp(account_json, managed_ou):
    status = False
    status_message = ''
    sc_client = session.client('servicecatalog')
    account_id = account_json['account_id']
    # get list of pps and their states
    (ct_pp_list, error_list, transit_list) = search_provisioned_products(sc_client)
    if len(transit_list) > 0:
        status_message = 'Another ServiceCatalog operation is in progress. '
        status_message += 'Allow UNDER_CHANGE or PLAN_IN_PROGRESS provisioned products to complete: \n'
        status_message += json.dumps(transit_list, indent=1, default=json_serial)
        raise ServiceCatalogOperationBlocked(status_message)
    else:
        try:
            pp_name = 'Enroll-Account-{}'.format(account_json['account_id'])
            provisioning_artifact_id = get_provisioning_artifact_id(sc_client)
            params = provisioning_params(account_json, managed_ou)
            response = sc_client.provision_product(ProductId=product_id,
                ProvisioningArtifactId=provisioning_artifact_id,
                ProvisionedProductName=pp_name,
                ProvisioningParameters=params,
                ProvisionToken=str(randint(1000000000000, 9999999999999)))
            status = True
        except Exception as ex:
            LOGGER.error(f"Failed in provision_product(..) for Account: {account_id}")
            LOGGER.error(str(ex))
            raise AccountEnrolmentFailed(f"Failed in provision_product(..) for Account: {account_id}")
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
    account_json = get_account(account_id)
    managed_ou = get_ou_name(ou_id)
    status = create_account_pp(account_json, managed_ou)
    return {
        'statusCode': 200,
        'body': {
            'org_id': org_id,
            'org_unit_id': ou_id,
            'ct_home_region': ct_home_region,
            's3bucket': s3bucket,
            's3key': s3key,
            'member_account_id': account_id,
            'logarchive_account': logarchive_account,
            'audit_account': audit_account,
            'member_account_region': account_region,
            'assume_role': role_name,
            'enrol_account_success': status
        }
    }
