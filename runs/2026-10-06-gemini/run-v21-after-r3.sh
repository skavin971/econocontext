#!/bin/bash
# After repeat 3: merge v2.1 (branch v21-nocache), require all tests to pass, then run the no-cache
# arm on the 5 longest tasks x 2 repeats. Stops on any failure. Safety ceiling $35 (user: don't stop
# at $30; ask before going past it if the projection exceeds it).
set -e
cd /Users/skavi/Documents/ChatGPT/EconoContext
until grep -q 'exit(econo+jev r3)' runs/2026-10-06-gemini/repeat23.out; do sleep 30; done
date; echo "repeat 3 finished; merging v21-nocache"
git merge --no-edit v21-nocache
git log --oneline -1
.venv/bin/python -m pytest -q tests/v2
.venv/bin/python -m pytest -q 2>&1 | tail -1
.venv/bin/python benchmarks/tblite/run_gemini.py --label gx4 --arm econo+jev-nocache \
  --tasks bandit-delayed-feedback,malicious-package-forensics,maven-slf4j-conflict,scan-linux-persistence-artifacts,api-endpoint-permission-canonicalizer \
  --repeats 2 --workers 2 --budget 35
echo "exit(nocache)=$?"; date
