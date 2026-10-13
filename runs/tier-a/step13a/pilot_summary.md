| arm | questions | judged correct | exact match | answer files | calls / q | searches / q | reads / q | PFLOPs / q | peak prompt (max) | CLM edits / rollbacks per q | wall time / q | calls valid |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| base | 1 | 0/1 | 0/1 | 0/1 | 5.0 | 4.0 | 0.0 | 1.839 | 24,689 | – (compactions 0) | 3.6 min | 5/5 |
| clm | 1 | 0/1 | 0/1 | 1/1 | 65.0 | 39.0 | 0.0 | 15.090 | 23,173 | 6.0 / 6.0 | 24.8 min | 55/65 |

Projection for 150 questions x 3 arms x 3 repeats = 1,350 runs, at the pilot's mean of 35.0 calls and 14.2 minutes per run: about 48,600 requests (judge included), so 6.1 days at the gateway's 8,000 requests a day (1.7 days at Purdue's 20 a minute if the cap allowed it); 13.3 days of trials run one at a time.
