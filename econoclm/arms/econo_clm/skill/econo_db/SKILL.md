Every command output is saved in full in a database, with an ID shown as [obs N].
This includes outputs that are cut short in your context.
  econo list            list saved outputs
  econo get N [A-B]     print output N exactly (or lines A to B), e.g. econo get 3 200-260
  econo search WORDS    find lines across all saved outputs
  econo sql "QUERY"     your own tables in a SQLite file (if sqlite3 or python3 exists here)
After each command, a line starting with [econo] reports: the last model call's
cached vs. new tokens, the run's cost so far, the cost of editing your context at
three depths, the number of saved outputs, and files that changed since you read them.
After each context edit, a line starting with [econo] reports what that edit costs
and after how many calls it pays off.
