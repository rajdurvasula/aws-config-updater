#!/bin/bash
SCRIPT_DIRECTORY="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

pushd $SCRIPT_DIRECTORY > /dev/null

rm -rf .package modify_aggr_authorizations.zip

zip modify_aggr_authorizations.zip modify_aggr_authorizations.py

popd > /dev/null
