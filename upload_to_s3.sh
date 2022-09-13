#!/bin/bash -x
if [ $# -ne 1 ]; then
  echo "Mandatory argument missing: bucket_name"
  exit 0
fi
aws s3 cp src/verify_cloudtrails.zip s3://$1/
aws s3 cp src/verify_config_resources.zip s3://$1/
aws s3 cp src/modify_config_recorder.zip s3://$1/
aws s3 cp src/modify_delivery_channel.zip s3://$1/
aws s3 cp src/modify_aggr_authorizations.zip s3://$1/
aws s3 cp src/start_config_recorder.zip s3://$1/
aws s3 cp src/modify_config_sm4.json s3://$1/
aws s3 cp setup-config-sf11.yaml s3://$1/
aws s3 cp org_configrecorder.yaml s3://$1/

