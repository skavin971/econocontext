| arm | questions | judged correct | exact match | answer files | calls / q | searches / q | reads / q | PFLOPs / q canonical | observed | peak prompt (max) | CLM edits / rollbacks per q | wall time / q | calls valid (split-only) | max abs dP | how runs ended |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| base | 4 | 1/4 | 1/4 | 1/4 | 5.0 | 3.8 | 0.2 | 1.593 | 3.109 | 24,839 | – (compactions 0) | 1.9 min | 20/20 (0) | 3 | budget_exceeded 1, budget_exceeded (no answer) 3 |
| clm | 8 | 2/8 | 2/8 | 8/8 | 64.1 | 37.5 | 2.2 | 13.430 | 21.214 | 34,393 | 7.0 / 3.8 | 21.0 min | 513/513 (0) | 1 | budget_exceeded 3, submitted 5 |

Projection for 150 questions x 3 arms x 3 repeats = 1,350 runs, at the pilot's mean of 44.4 calls and 14.6 minutes per run: about 61,312 requests (judge included), so 7.7 days at the gateway's 8,000 requests a day (2.1 days at Purdue's 20 a minute if the cap allowed it); 13.7 days of trials run one at a time.
