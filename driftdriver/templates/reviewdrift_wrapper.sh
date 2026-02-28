#!/usr/bin/env bash
# ABOUTME: Reviewdrift lane wrapper for speedrift
# ABOUTME: Runs adversarial review gate and outputs PASS/FAIL verdict
set -euo pipefail
python3 -m driftdriver.adversarial_review "$@"
