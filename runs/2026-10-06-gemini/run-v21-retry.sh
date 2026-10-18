#!/bin/bash
# Rerun the 9 v2.1 trials our gateway's daily token cap aborted (label gx4b; gx4's aborted ones are excluded).
set -e
cd /Users/skavi/Documents/ChatGPT/EconoContext
.venv/bin/python benchmarks/tblite/run_gemini.py --label gx4b --arm econo+jev-nocache \
  --tasks bandit-delayed-feedback,maven-slf4j-conflict,scan-linux-persistence-artifacts,api-endpoint-permission-canonicalizer \
  --repeats 2 --workers 2 --budget 35
.venv/bin/python benchmarks/tblite/run_gemini.py --label gx4b --arm econo+jev-nocache \
  --tasks malicious-package-forensics --repeats 1 --repeat-from 2 --workers 1 --budget 35
echo "exit(retry)=$?"; date
