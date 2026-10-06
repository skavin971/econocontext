#!/bin/bash
# Experiment repeat 1: the 10 frozen TBLite tasks, raw then econo+jev, 2 workers each.
cd /Users/skavi/Documents/ChatGPT/EconoContext
for arm in raw econo+jev; do
  date; .venv/bin/python benchmarks/tblite/run_gemini.py --label gx1 --arm $arm --tasks frozen --repeats 1 --workers 2
  echo "exit($arm)=$?"
done
date
