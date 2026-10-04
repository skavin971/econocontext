Your memory database (EconoCLM)
Every command output is saved in full, with an ID shown at its top as [obs N].
Removing an output from your context file does not lose it:
  econo get N          prints output N again, exactly
  econo get N A-B      prints only lines A to B
  econo search WORDS   finds matching lines across all saved outputs
  econo note "TEXT"    saves a note;  econo notes  lists your notes
When an output is cut, its missing lines are in obs N too.
After each command, a line starting with [econo] reports how many tokens the last
call reused from the server's cache and how many it computed, the compute used so
far, and how many tokens an edit at different depths would recompute.
How to use it:
- After you have used a large output, delete it from your context file; it stays in obs N.
- When you need details from a cut or deleted output, fetch only those lines with econo get.
- Save findings you will need later (paths, error messages, test results) with econo note.
