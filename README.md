# Modify AWS Config in Member accounts with existing AWS Config resources
This automation artifact modifies AWS Config resources of an Organization **unenrolled** member account. AWS config resources on member account will be pointing to Control Tower configured AWS Config Reccorder, Delivery Channel, AWS Config aggregation authorization.

# Purpose
- Register organization **unenrolled** member account to Control Tower
- **unenrolled** member account should be part of an **registered** Organization Unit (OU)
- Automation checks Cloud Trail usage of **unenrolled** member account
- Automation modifies **unenrolled** member account's AWS Config resources
- Post the automation execution, Organization Unit (OU) which contains **unenrolled** member account should be registered in Control Toweer **manually** using Control Tower console

## NOTE
- This automation can be used to address the AWS Config pre-requisite step before account enrolment to Control Tower.

## Instructions

1. Upload files
  - src/verify_cloudtrails.zip to S3 Bucket. Note down the S3 Bucket name. Note down the S3 Key
  - src/verify_config_resources.zip to S3 Bucket. Note down the S3 Bucket name. Note down the S3 Key
  - src/modify_config_recorder.zip to S3 Bucket. Note down the S3 Bucket name. Note down the S3 Key
  - src/modify_delivery_channel.zip to S3 Bucket. Note down the S3 Bucket name. Note down the S3 Key
  - src/modify_aggr_authorizations.zip to S3 Bucket. Note down the S3 Bucket name. Note down the S3 Key
  - src/start_config_recorder.zip to S3 Bucket. Note down the S3 Bucket name. Note down the S3 Key
  - src/modify_config_sm4.json to S3 Bucket. Note down the S3 Bucket name. Note down the S3 Key.
    - *This is referred* **ConfigEnablerSM** *statemachine*
  - org_configrecorder.yaml to S3 Bucket. Note down the S3 Bucket name. Note down the S3 Key
  - setup-config-sf11.yaml to S3 Bucket. Note down the S3 Bucket name. Note down the S3 Key
2. Gather Control Tower and Organization information:
  - In AWS Organizations, lookup the Settings page for the Organization ID.
  - In AWS Organizations, lookup the Settings page for the Organization Unit ID. This is where **unenrolled** Member Account exists.
  - In AWS Organizations, lookup the Settings page for the **unenrolled** Member Account
  - In AWS Organizations, lookup the Accounts page for the Audit account ID.
  - In AWS Organizations, lookup the Accounts page for the Log Archive account ID.
3. Launch CloudFormation stack using setup-config-sf11.yaml as source
4. Launch State Machine **ConfigEnablerSM-**
  - This State Machine calls 6 Lambda Functions
    - Verify Cloud Trails of Account by Region
    - Verify Config Resources of Account by Region
    - Setup (Create / Update) Recorder by Region
    - Setup (Create / Update) Delivery Channel by Region
    - Setup (Create / Update) Aggregation Authorization by Region
    - Start Config Recorder by Region

## State Machine
This state machine executes each Lambda function to modify AWS Config resources on **unenrolled** member account

![modify_config_sm4.png](./modify_config_sm4.png?raw=true)

## Launch State Machine
- JSON input for launching State Machine **ConfigEnablerSM**
- Adapt the sample JSON dictionary below with
  - `org_unit_id` Account's Target Organization Unit
  - `member_acccount` Account Id

```
{
  "org_id": "o-a4tlobvmc0",
  "org_unit_id": "ou-6ulx-i3xsex7t",
  "ct_home_region": "us-east-1",
  "s3_bucket": "org-sh-ops",
  "s3_key": "org_configrecorder.yaml",
  "member_account": "632203099578",
  "logarchive_account": "559816438515",
  "audit_account": "413157014023",
  "assume_role": "AWSControlTowerExecution"
}
```

## Limitations
- Account Enrolment process described here can be initiated for 1 Account at a time
  - Enrolment workflow either via CT console or Service Catalog is **single-threaded**
- OU Registration does not have associated API / SDK
  - Alternative is to Create Service Catalog **Provisioned Product** for 1 Account at a time
- Account Enrolment on Control Tower have multiple dependencies on Control Tower, Service Catalog
  - Often times, Account Enrolment could fail due to unmet dependencies in Control Tower
  - Often times, such failures have to be resolved by opening AWS Support Case

## Issues
- Automation can be executed for single **unenrolled** member account
