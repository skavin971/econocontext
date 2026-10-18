#!/bin/bash
# Step 14a: finish question 986 (CLM was running when the runner loop was stopped; then base), then CLM alone on
# pilot positions 4-7 (8 CLM questions in total). Arm order unchanged; no other arm.
cd "$(dirname "$0")/../../.."
echo "step 14a: finish question 986, then CLM alone on pilot positions 4-7"
while ps -p "$1" >/dev/null; do sleep 30; done
echo "$(date +%H:%M:%S) CLM 986 trial ended"
.venv/bin/python benchmarks/bcp/run.py --label bcp13p --split pilot --summarize-only clm:986 2>&1 | grep -v PyTorch
.venv/bin/python benchmarks/bcp/run.py --label bcp13p --split pilot --arms base --start 3 --limit 1 2>&1 | grep -v "PyTorch\|reminder"
.venv/bin/python benchmarks/bcp/run.py --label bcp13p --split pilot --arms clm --start 4 --limit 4 2>&1 | grep -v "PyTorch\|reminder"
echo "$(date +%H:%M:%S) pilot done"
