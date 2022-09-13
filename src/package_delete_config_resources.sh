#!/bin/bash
SCRIPT_DIRECTORY="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

pushd $SCRIPT_DIRECTORY > /dev/null

rm -rf .package delete_config_resources.zip

zip delete_config_resources.zip delete_config_resources.py

popd > /dev/null
