#!/bin/bash -x
if [ $# -ne 1 ]; then
  echo "Mandatory argument missing: bucket_name"
  exit 0
fi
aws s3 rm s3://$1/verify_cloudtrails.zip
aws s3 rm s3://$1/verify_config_resources.zip
aws s3 rm s3://$1/modify_config_recorder.zip
aws s3 rm s3://$1/modify_delivery_channel.zip
aws s3 rm s3://$1/modify_aggr_authorizations.zip
aws s3 rm s3://$1/start_config_recorder.zip
aws s3 rm s3://$1/modify_config_sm4.json
aws s3 rm s3://$1/setup-config-sf11.yaml
aws s3 rm s3://$1/org_configrecorder.yaml

