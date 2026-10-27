#!/usr/bin/env bash
# As run for Study 2 (2026-10-25), copied unchanged except for this line. Paths are the cloud container's.
# Study 2, CLM arms C1/C2 for one question: repeats 1-3 in parallel (one Docker trial each at a time), arm order
# alternating per repeat. All share the gateway on :8787 (12 calls/min).
# Usage: run_clm.sh <qid>
set -u
qid=$1
R=/home/user/skavin971/econocontext
cd "$R" || exit 9
for r in 1 2 3; do
  if [ $((r % 2)) -eq 1 ]; then arms=clm,clm_instructed; else arms=clm_instructed,clm; fi
  "$R/.venv/bin/python" benchmarks/bcp/run.py --label bcp-s2 --arms "$arms" --qids "$qid" --repeat "$r" \
    --gateway "${GW:-http://127.0.0.1:8787}" --progress /root/econo/bcp/setup-logs/clm-progress.md \
    > "$R/econo/bcp/setup-logs/s2-clm-q$qid-r$r.log" 2>&1 &
done
wait
echo "clm q$qid done $(date -u +%T)"
grep -h "bcp-s2\." "$R"/econo/bcp/setup-logs/s2-clm-q"$qid"-r*.log | cut -c1-300
