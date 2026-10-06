Work in /app. Do the steps below in order, one tool call per step unless a step says otherwise. Use exactly the tool and arguments given.

1. Use the Grep tool: pattern `def item_`, path `src/app.py`, output mode content, with line numbers.
2. Use the Grep tool again with exactly the same arguments as step 1.
3. Use the Read tool on `src/app.py` with offset 1 and limit 60.
4. Use the Read tool on `src/app.py` with offset 40 and limit 61.
5. Run with Bash: `python3 -m pytest -q tests/test_app.py`
6. Run with Bash again, exactly the same command: `python3 -m pytest -q tests/test_app.py`
7. Run with Bash: `cat data/big.log`
8. Use the Agent tool (subagent type general-purpose) with the prompt: "Print the first 120 lines of /app/data/big.log verbatim in your final report, then one line saying how many lines it has."
9. Use the Agent tool again (general-purpose) with the prompt: "Count how many lines of /app/data/big.log contain the word ERROR and report only the number." If you are told to continue an existing agent instead, do that.
10. Run with Bash: `cat src/app.py`
11. Edit `src/app.py`: change `return qty * 7` to `return qty * 70`.
12. Run with Bash again, exactly the same command: `cat src/app.py`
13. Write `/app/answers.txt` with two lines: line 1 is the value of SECRET_TOKEN found in data/big.log, line 2 is the number of lines in data/big.log that contain ERROR.

Then reply DONE.
