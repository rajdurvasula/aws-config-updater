#!/bin/bash
SCRIPT_DIRECTORY="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

pushd $SCRIPT_DIRECTORY > /dev/null

rm -rf .package modify_delivery_channel.zip

zip modify_delivery_channel.zip modify_delivery_channel.py

popd > /dev/null
