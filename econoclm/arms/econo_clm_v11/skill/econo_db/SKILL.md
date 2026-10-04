Your memory database (EconoCLM)
Every command output is saved in full, with an ID shown at its top as [obs N].
Removing an output from your context file does not lose it:
  econo get N          prints output N again, exactly
  econo get N A-B      prints only lines A to B
  econo search WORDS   finds matching lines across all saved outputs
  econo note "TEXT"    saves a note;  econo notes  lists your notes
  econo sql "QUERY"    your own tables (if sqlite3 or python3 is available)
When an output is cut, its missing lines are in obs N too.
After each command, a line starting with [econo] reports what the last model
call cost, how much of it was cached, and what editing your context would cost.
Gemini only caches prompts of at least 4,096 tokens, and only up to the first
changed character. An edit that changes anything in roughly the first 4,096
tokens makes Gemini re-read the whole prompt at full price.
