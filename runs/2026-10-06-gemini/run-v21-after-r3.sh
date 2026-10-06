#!/bin/bash
# Wait for repeat 3 to finish, then fast-forward econo-jev to v2.1 (branch v21-nocache) and run the
# no-cache arm on the 5 longest tasks, 2 repeats, 2 workers, inside the $30 Gemini budget.
cd /Users/skavi/Documents/ChatGPT/EconoContext
until grep -q 'exit(econo+jev r3)' runs/2026-10-06-gemini/repeat23.out; do sleep 30; done
date; echo "repeat 3 finished; merging v21-nocache"
git merge --ff-only v21-nocache; echo "merge exit=$?"
git log --oneline -1
.venv/bin/python -m pytest tests/v2 -q 2>&1 | tail -1
.venv/bin/python benchmarks/tblite/run_gemini.py --label gx4 --arm econo+jev-nocache \
  --tasks bandit-delayed-feedback,malicious-package-forensics,maven-slf4j-conflict,scan-linux-persistence-artifacts,api-endpoint-permission-canonicalizer \
  --repeats 2 --workers 2 --budget 30
echo "exit(nocache)=$?"; date
