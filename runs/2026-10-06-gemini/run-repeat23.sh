#!/bin/bash
# Repeats 2 and 3: the 10 frozen TBLite tasks, arms interleaved (raw r2, econo r2, raw r3, econo r3),
# 2 workers. Budget $30 total for Gemini on this host (user, 2026-10-06), enforced before each trial.
cd /Users/skavi/Documents/ChatGPT/EconoContext
for rep in 2 3; do
  for arm in raw econo+jev; do
    date; .venv/bin/python benchmarks/tblite/run_gemini.py --label gx23 --arm $arm --tasks frozen \
      --repeats 1 --repeat-from $rep --workers 2 --budget 30
    echo "exit($arm r$rep)=$?"
  done
done
date
