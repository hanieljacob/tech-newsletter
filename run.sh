#!/bin/bash
set -a
source "$(dirname "$0")/.env"
set +a
/Library/Frameworks/Python.framework/Versions/3.12/bin/python3 "$(dirname "$0")/tech_check.py" >> "$(dirname "$0")/tech_check.log" 2>&1
