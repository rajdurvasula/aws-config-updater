#!/bin/bash
SCRIPT_DIRECTORY="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

pushd $SCRIPT_DIRECTORY > /dev/null

rm -rf .package verify_cloudtrails.zip

zip verify_cloudtrails.zip verify_cloudtrails.py

popd > /dev/null
