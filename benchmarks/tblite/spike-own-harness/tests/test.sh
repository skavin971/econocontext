#!/bin/bash
# Reward 1 when the answers are right and the edit was made.
ok=1
diff -q <(head -2 /app/answers.txt | tr -d ' \r') /tests/expected.txt || ok=0
grep -q 'return qty \* 70' /app/src/app.py || ok=0
echo $ok > /logs/verifier/reward.txt
