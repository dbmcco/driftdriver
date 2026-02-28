#!/usr/bin/env bash
# ABOUTME: Qadrift lane wrapper for speedrift
# ABOUTME: Runs QA analysis and outputs drift score
set -euo pipefail
cd "${1:-.}"
python3 -m driftdriver.qadrift "$@"
