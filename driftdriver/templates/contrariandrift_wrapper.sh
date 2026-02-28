#!/usr/bin/env bash
# ABOUTME: Contrariandrift lane wrapper for speedrift
# ABOUTME: Runs contrarian analysis and outputs drift score
set -euo pipefail
cd "${1:-.}"
python3 -m driftdriver.contrariandrift "$@"
