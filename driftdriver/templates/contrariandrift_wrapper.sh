#!/usr/bin/env bash
# ABOUTME: Contrariandrift lane wrapper for speedrift
# ABOUTME: Runs contrarian analysis and outputs drift score
set -euo pipefail
python3 -m driftdriver.contrariandrift "$@"
