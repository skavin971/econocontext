Work in /app. Do the steps below in order, one tool call per step, with exactly the tool and arguments given.

1. bash: `grep -n "def item_" src/app.py`
2. bash again, exactly the same command: `grep -n "def item_" src/app.py`
3. read_file: path `src/app.py`, start_line 1, end_line 60
4. read_file: path `src/app.py`, start_line 40, end_line 100
5. bash: `python3 -m pytest -q tests/test_app.py`
6. bash again, exactly the same command: `python3 -m pytest -q tests/test_app.py`
7. bash: `cat data/big.log`
8. bash: `seq -w 1 1200`
9. bash: `grep -c ERROR data/big.log`
10. bash: `grep -n SECRET_TOKEN data/big.log`
11. bash again, exactly the same command as step 1: `grep -n "def item_" src/app.py`
12. bash: `cat src/app.py`
13. write_file: rewrite `src/app.py` with `return qty * 7` changed to `return qty * 70` (keep everything else the same).
14. bash again, exactly the same command as step 12: `cat src/app.py`
15. write_file: `/app/answers.txt` with two lines: line 1 is the value of SECRET_TOKEN found in data/big.log, line 2 is the number of lines in data/big.log that contain ERROR.

Then call submit.
