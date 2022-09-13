#!/bin/bash
SCRIPT_DIRECTORY="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

pushd $SCRIPT_DIRECTORY > /dev/null

rm -rf .package start_config_recorder.zip

zip start_config_recorder.zip start_config_recorder.py

popd > /dev/null
