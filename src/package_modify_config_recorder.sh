#!/bin/bash
SCRIPT_DIRECTORY="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

pushd $SCRIPT_DIRECTORY > /dev/null

rm -rf .package modify_config_recorder.zip

zip modify_config_recorder.zip modify_config_recorder.py

popd > /dev/null
