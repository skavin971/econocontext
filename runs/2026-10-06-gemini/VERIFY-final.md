# Verification of gx1, gx1b, gx23, gx4, gx4b

Checks per trial: cost re-priced from raw usage (Gemini card: input $0.75/M, cached $0.075/M, output $3.75/M incl. reasoning), gateway calls = agent calls, token totals match, reward = grader file, summary = gateway, Jev tokens session = summary.

**Result: 10 trial(s) with a failed check or an exception.**

- gx1:econo+jev:acl-permissions-inheritance:r1: cost $0.083150 (re-priced $0.083150; reasoning-inside $0.078714), calls 22/22, reward 1.0 (file 1.0), jev 0  ok
- gx1:econo+jev:anomaly-detection-ranking:r1: cost $0.157194 (re-priced $0.157194; reasoning-inside $0.142408), calls 27/27, reward 1.0 (file 1.0), jev 9952  ok
- gx1:econo+jev:api-endpoint-permission-canonicalizer:r1: cost $0.467479 (re-priced $0.467480; reasoning-inside $0.435410), calls 28/28, reward 1.0 (file 1.0), jev 67598  ok
- gx1:econo+jev:bandit-delayed-feedback:r1: cost $0.755145 (re-priced $0.755145; reasoning-inside $0.688928), calls 60/60, reward 0.0 (file 0.0), jev 59905  ok
- gx1:econo+jev:chained-forensic-extraction_20260101_011957:r1: cost $0.136145 (re-priced $0.136145; reasoning-inside $0.116908), calls 22/22, reward 1.0 (file 1.0), jev 0  ok
- gx1:econo+jev:malicious-package-forensics:r1: cost $0.706100 (re-priced $0.706100; reasoning-inside $0.667640), calls 44/44, reward 1.0 (file 1.0), jev 214506  ok
- gx1:econo+jev:maven-slf4j-conflict:r1: cost $0.274778 (re-priced $0.274778; reasoning-inside $0.267087), calls 38/38, reward 1.0 (file 1.0), jev 309659  ok
- gx1:econo+jev:pandas-etl:r1: cost $0.089821 (re-priced $0.089821; reasoning-inside $0.084466), calls 13/13, reward 1.0 (file 1.0), jev 15605  ok
- gx1:econo+jev:sales-data-csv-analysis:r1: cost $0.284812 (re-priced $0.284812; reasoning-inside $0.261719), calls 20/20, reward 1.0 (file 1.0), jev 30216  ok
- gx1:econo+jev:scan-linux-persistence-artifacts:r1: cost $0.542481 (re-priced $0.542481; reasoning-inside $0.512259), calls 50/50, reward 1.0 (file 1.0), jev 179388  ok
- gx1:raw:acl-permissions-inheritance:r1: cost $0.051399 (re-priced $0.051399; reasoning-inside $0.047409), calls 16/16, reward 1.0 (file 1.0), jev 0  ok
- gx1:raw:anomaly-detection-ranking:r1: cost $0.158113 (re-priced $0.158113; reasoning-inside $0.142055), calls 28/28, reward 1.0 (file 1.0), jev 0  ok
- gx1:raw:api-endpoint-permission-canonicalizer:r1: cost $0.479664 (re-priced $0.479664; reasoning-inside $0.463367), calls 26/26, reward 1.0 (file 1.0), jev 0  ok
- gx1:raw:bandit-delayed-feedback:r1: cost $0.752186 (re-priced $0.752186; reasoning-inside $0.702998), calls 60/60, reward 0.0 (file 0.0), jev 0  ok
- gx1:raw:chained-forensic-extraction_20260101_011957:r1: cost $0.171991 (re-priced $0.171991; reasoning-inside $0.149604), calls 21/21, reward 1.0 (file 1.0), jev 0  ok
- gx1:raw:malicious-package-forensics:r1: cost $0.180346 (re-priced $0.214858; reasoning-inside $0.210515), calls 15/15, reward None (file None), jev 0, EXCEPTION AttributeError  FAIL: ['cost', 'cost_complete', 'tokens']
- gx1:raw:maven-slf4j-conflict:r1: cost $0.633973 (re-priced $0.633973; reasoning-inside $0.631322), calls 30/30, reward 1.0 (file 1.0), jev 0  ok
- gx1:raw:pandas-etl:r1: cost $0.073534 (re-priced $0.073534; reasoning-inside $0.068336), calls 12/12, reward 1.0 (file 1.0), jev 0  ok
- gx1:raw:sales-data-csv-analysis:r1: cost $0.448649 (re-priced $0.448649; reasoning-inside $0.402134), calls 25/25, reward 0.0 (file 0.0), jev 0  ok
- gx1:raw:scan-linux-persistence-artifacts:r1: cost $0.646225 (re-priced $0.646225; reasoning-inside $0.605241), calls 39/39, reward 1.0 (file 1.0), jev 0  ok
- gx1b:raw:malicious-package-forensics:r1: cost $0.740336 (re-priced $0.740336; reasoning-inside $0.712147), calls 28/28, reward 1.0 (file 1.0), jev 0  ok
- gx23:econo+jev:acl-permissions-inheritance:r2: cost $0.041493 (re-priced $0.041493; reasoning-inside $0.038891), calls 15/15, reward 1.0 (file 1.0), jev 0  ok
- gx23:econo+jev:acl-permissions-inheritance:r3: cost $0.048663 (re-priced $0.048663; reasoning-inside $0.046425), calls 17/17, reward 1.0 (file 1.0), jev 0  ok
- gx23:econo+jev:anomaly-detection-ranking:r2: cost $0.152091 (re-priced $0.152091; reasoning-inside $0.135714), calls 23/23, reward 1.0 (file 1.0), jev 0  ok
- gx23:econo+jev:anomaly-detection-ranking:r3: cost $0.157558 (re-priced $0.157558; reasoning-inside $0.143353), calls 27/27, reward 1.0 (file 1.0), jev 15420  ok
- gx23:econo+jev:api-endpoint-permission-canonicalizer:r2: cost $0.402214 (re-priced $0.402214; reasoning-inside $0.384612), calls 23/23, reward 1.0 (file 1.0), jev 57952  ok
- gx23:econo+jev:api-endpoint-permission-canonicalizer:r3: cost $0.498082 (re-priced $0.498082; reasoning-inside $0.479677), calls 27/27, reward 1.0 (file 1.0), jev 75543  ok
- gx23:econo+jev:bandit-delayed-feedback:r2: cost $0.538521 (re-priced $0.538521; reasoning-inside $0.493540), calls 44/44, reward 0.0 (file 0.0), jev 100001  ok
- gx23:econo+jev:bandit-delayed-feedback:r3: cost $0.696354 (re-priced $0.696354; reasoning-inside $0.632938), calls 57/57, reward 0.0 (file 0.0), jev 94311  ok
- gx23:econo+jev:chained-forensic-extraction_20260101_011957:r2: cost $0.131396 (re-priced $0.131396; reasoning-inside $0.104100), calls 18/18, reward 1.0 (file 1.0), jev 0  ok
- gx23:econo+jev:chained-forensic-extraction_20260101_011957:r3: cost $0.148443 (re-priced $0.148443; reasoning-inside $0.119943), calls 21/21, reward 1.0 (file 1.0), jev 0  ok
- gx23:econo+jev:malicious-package-forensics:r2: cost $0.644635 (re-priced $0.644635; reasoning-inside $0.624737), calls 39/39, reward 0.0 (file 0.0), jev 192113  ok
- gx23:econo+jev:malicious-package-forensics:r3: cost $0.385746 (re-priced $0.385746; reasoning-inside $0.356901), calls 25/25, reward 1.0 (file 1.0), jev 193003  ok
- gx23:econo+jev:maven-slf4j-conflict:r2: cost $0.322543 (re-priced $0.322543; reasoning-inside $0.315261), calls 36/36, reward 1.0 (file 1.0), jev 179955  ok
- gx23:econo+jev:maven-slf4j-conflict:r3: cost $0.254620 (re-priced $0.254620; reasoning-inside $0.247000), calls 36/36, reward 1.0 (file 1.0), jev 258616  ok
- gx23:econo+jev:pandas-etl:r2: cost $0.092603 (re-priced $0.092603; reasoning-inside $0.087900), calls 15/15, reward 1.0 (file 1.0), jev 0  ok
- gx23:econo+jev:pandas-etl:r3: cost $0.065527 (re-priced $0.065527; reasoning-inside $0.059534), calls 13/13, reward 1.0 (file 1.0), jev 0  ok
- gx23:econo+jev:sales-data-csv-analysis:r2: cost $0.230968 (re-priced $0.230968; reasoning-inside $0.203319), calls 18/18, reward 1.0 (file 1.0), jev 18795  ok
- gx23:econo+jev:sales-data-csv-analysis:r3: cost $0.309074 (re-priced $0.309073; reasoning-inside $0.281781), calls 30/30, reward 1.0 (file 1.0), jev 43217  ok
- gx23:econo+jev:scan-linux-persistence-artifacts:r2: cost $0.590424 (re-priced $0.590424; reasoning-inside $0.558058), calls 40/40, reward 1.0 (file 1.0), jev 168593  ok
- gx23:econo+jev:scan-linux-persistence-artifacts:r3: cost $0.577084 (re-priced $0.577084; reasoning-inside $0.542757), calls 48/48, reward 1.0 (file 1.0), jev 200542  ok
- gx23:raw:acl-permissions-inheritance:r2: cost $0.067561 (re-priced $0.067561; reasoning-inside $0.063418), calls 20/20, reward 1.0 (file 1.0), jev 0  ok
- gx23:raw:acl-permissions-inheritance:r3: cost $0.079675 (re-priced $0.079675; reasoning-inside $0.073473), calls 22/22, reward 1.0 (file 1.0), jev 0  ok
- gx23:raw:anomaly-detection-ranking:r2: cost $0.170318 (re-priced $0.170318; reasoning-inside $0.157350), calls 31/31, reward 1.0 (file 1.0), jev 0  ok
- gx23:raw:anomaly-detection-ranking:r3: cost $0.134813 (re-priced $0.134813; reasoning-inside $0.123938), calls 24/24, reward 1.0 (file 1.0), jev 0  ok
- gx23:raw:api-endpoint-permission-canonicalizer:r2: cost $0.516163 (re-priced $0.516163; reasoning-inside $0.495737), calls 26/26, reward 1.0 (file 1.0), jev 0  ok
- gx23:raw:api-endpoint-permission-canonicalizer:r3: cost $0.467242 (re-priced $0.467242; reasoning-inside $0.439593), calls 22/22, reward 1.0 (file 1.0), jev 0  ok
- gx23:raw:bandit-delayed-feedback:r2: cost $0.772609 (re-priced $0.772609; reasoning-inside $0.716801), calls 54/54, reward 0.0 (file 0.0), jev 0  ok
- gx23:raw:bandit-delayed-feedback:r3: cost $0.612318 (re-priced $0.612318; reasoning-inside $0.563115), calls 60/60, reward 0.0 (file 0.0), jev 0  ok
- gx23:raw:chained-forensic-extraction_20260101_011957:r2: cost $0.148385 (re-priced $0.148385; reasoning-inside $0.127160), calls 19/19, reward 1.0 (file 1.0), jev 0  ok
- gx23:raw:chained-forensic-extraction_20260101_011957:r3: cost $0.098721 (re-priced $0.098721; reasoning-inside $0.085086), calls 14/14, reward 1.0 (file 1.0), jev 0  ok
- gx23:raw:malicious-package-forensics:r2: cost $0.328104 (re-priced $0.328104; reasoning-inside $0.314765), calls 18/18, reward 1.0 (file 1.0), jev 0  ok
- gx23:raw:malicious-package-forensics:r3: cost $0.335560 (re-priced $0.335560; reasoning-inside $0.321037), calls 15/15, reward 1.0 (file 1.0), jev 0  ok
- gx23:raw:maven-slf4j-conflict:r2: cost $0.394015 (re-priced $0.394015; reasoning-inside $0.387978), calls 26/26, reward 1.0 (file 1.0), jev 0  ok
- gx23:raw:maven-slf4j-conflict:r3: cost $0.476713 (re-priced $0.476713; reasoning-inside $0.470375), calls 24/24, reward 1.0 (file 1.0), jev 0  ok
- gx23:raw:pandas-etl:r2: cost $0.069625 (re-priced $0.069625; reasoning-inside $0.064611), calls 15/15, reward 1.0 (file 1.0), jev 0  ok
- gx23:raw:pandas-etl:r3: cost $0.072515 (re-priced $0.072515; reasoning-inside $0.066125), calls 15/15, reward 1.0 (file 1.0), jev 0  ok
- gx23:raw:sales-data-csv-analysis:r2: cost $0.304359 (re-priced $0.304359; reasoning-inside $0.278038), calls 21/21, reward 0.0 (file 0.0), jev 0  ok
- gx23:raw:sales-data-csv-analysis:r3: cost $0.455277 (re-priced $0.455277; reasoning-inside $0.422754), calls 27/27, reward 0.0 (file 0.0), jev 0  ok
- gx23:raw:scan-linux-persistence-artifacts:r2: cost $0.708072 (re-priced $0.708072; reasoning-inside $0.655486), calls 38/38, reward 1.0 (file 1.0), jev 0  ok
- gx23:raw:scan-linux-persistence-artifacts:r3: cost $0.796840 (re-priced $0.796840; reasoning-inside $0.762663), calls 44/44, reward 1.0 (file 1.0), jev 0  ok
- gx4:econo+jev-nocache:api-endpoint-permission-canonicalizer:r1: cost $0.000000 (re-priced $0.000000; reasoning-inside $0.000000), calls 0/0, reward None (file None), jev 0, EXCEPTION RateLimitError  ok
- gx4:econo+jev-nocache:api-endpoint-permission-canonicalizer:r2: cost $0.000000 (re-priced $0.000000; reasoning-inside $0.000000), calls 0/0, reward None (file None), jev 0, EXCEPTION RateLimitError  ok
- gx4:econo+jev-nocache:bandit-delayed-feedback:r1: cost $0.436442 (re-priced $0.436442; reasoning-inside $0.396613), calls 40/40, reward None (file None), jev 85507, EXCEPTION RateLimitError  ok
- gx4:econo+jev-nocache:bandit-delayed-feedback:r2: cost $0.000000 (re-priced $0.000000; reasoning-inside $0.000000), calls 0/0, reward None (file None), jev 0, EXCEPTION RateLimitError  ok
- gx4:econo+jev-nocache:malicious-package-forensics:r1: cost $0.440111 (re-priced $0.440111; reasoning-inside $0.418050), calls 31/31, reward 1.0 (file 1.0), jev 228130  ok
- gx4:econo+jev-nocache:malicious-package-forensics:r2: cost $0.000000 (re-priced $0.000000; reasoning-inside $0.000000), calls 0/0, reward None (file None), jev 0, EXCEPTION RateLimitError  ok
- gx4:econo+jev-nocache:maven-slf4j-conflict:r1: cost $0.069405 (re-priced $0.069405; reasoning-inside $0.067343), calls 22/22, reward None (file None), jev 120679, EXCEPTION RateLimitError  ok
- gx4:econo+jev-nocache:maven-slf4j-conflict:r2: cost $0.000000 (re-priced $0.000000; reasoning-inside $0.000000), calls 0/0, reward None (file None), jev 0, EXCEPTION RateLimitError  ok
- gx4:econo+jev-nocache:scan-linux-persistence-artifacts:r1: cost $0.000000 (re-priced $0.000000; reasoning-inside $0.000000), calls 0/0, reward None (file None), jev 0, EXCEPTION RateLimitError  ok
- gx4:econo+jev-nocache:scan-linux-persistence-artifacts:r2: cost $0.000000 (re-priced $0.000000; reasoning-inside $0.000000), calls 0/0, reward None (file None), jev 0, EXCEPTION RateLimitError  ok
- gx4b:econo+jev-nocache:api-endpoint-permission-canonicalizer:r1: cost $0.378979 (re-priced $0.378979; reasoning-inside $0.358118), calls 13/13, reward 1.0 (file 1.0), jev 18030  ok
- gx4b:econo+jev-nocache:api-endpoint-permission-canonicalizer:r2: cost $0.394061 (re-priced $0.394061; reasoning-inside $0.371373), calls 27/27, reward 1.0 (file 1.0), jev 69988  ok
- gx4b:econo+jev-nocache:bandit-delayed-feedback:r1: cost $0.727948 (re-priced $0.727948; reasoning-inside $0.671076), calls 59/59, reward 1.0 (file 1.0), jev 87322  ok
- gx4b:econo+jev-nocache:bandit-delayed-feedback:r2: cost $0.622258 (re-priced $0.622258; reasoning-inside $0.569949), calls 50/50, reward 1.0 (file 1.0), jev 54597  ok
- gx4b:econo+jev-nocache:malicious-package-forensics:r2: cost $0.382588 (re-priced $0.382588; reasoning-inside $0.350938), calls 31/31, reward 1.0 (file 1.0), jev 188121  ok
- gx4b:econo+jev-nocache:maven-slf4j-conflict:r1: cost $0.259177 (re-priced $0.259177; reasoning-inside $0.251884), calls 40/40, reward 1.0 (file 1.0), jev 341370  ok
- gx4b:econo+jev-nocache:maven-slf4j-conflict:r2: cost $0.260379 (re-priced $0.260379; reasoning-inside $0.253831), calls 36/36, reward 1.0 (file 1.0), jev 247195  ok
- gx4b:econo+jev-nocache:scan-linux-persistence-artifacts:r1: cost $0.653203 (re-priced $0.653203; reasoning-inside $0.615834), calls 44/44, reward 1.0 (file 1.0), jev 227170  ok
- gx4b:econo+jev-nocache:scan-linux-persistence-artifacts:r2: cost $0.509051 (re-priced $0.509051; reasoning-inside $0.471791), calls 39/39, reward 1.0 (file 1.0), jev 258053  ok

## Totals (rebuilt from raw rows; valid trials only)

Kept out (crashed or aborted, not graded): 10 -- gx1 raw malicious-package-forensics r1 (AttributeError), gx4 econo+jev-nocache api-endpoint-permission-canonicalizer r1 (RateLimitError), gx4 econo+jev-nocache api-endpoint-permission-canonicalizer r2 (RateLimitError), gx4 econo+jev-nocache bandit-delayed-feedback r1 (RateLimitError), gx4 econo+jev-nocache bandit-delayed-feedback r2 (RateLimitError), gx4 econo+jev-nocache malicious-package-forensics r2 (RateLimitError), gx4 econo+jev-nocache maven-slf4j-conflict r1 (RateLimitError), gx4 econo+jev-nocache maven-slf4j-conflict r2 (RateLimitError), gx4 econo+jev-nocache scan-linux-persistence-artifacts r1 (RateLimitError), gx4 econo+jev-nocache scan-linux-persistence-artifacts r2 (RateLimitError)

- raw: 30 trials, passed 24, cost $11.1650, Jev tokens 0, exceptions 0
- econo+jev: 30 trials, passed 26, cost $9.7851, Jev tokens 2,484,890, exceptions 0
- econo+jev-nocache: 10 trials, passed 10, cost $4.6278, Jev tokens 1,719,976, exceptions 0
- econo+jev vs raw, 30 same-repeat pairs: raw $11.1650, econo+jev $9.7851 (-12.4%), mean diff $-0.0460, sd $0.1224, paired t -2.06 (df 29), cheaper in 20/30
  two-sided p: paired t 0.049; exact sign test 0.099 (no normality assumption)
- econo+jev-nocache vs raw, 10 same-repeat pairs: raw $5.9713, econo+jev-nocache $4.6278 (-22.5%), mean diff $-0.1344, sd $0.1328, paired t -3.20 (df 9), cheaper in 8/10
  two-sided p: paired t 0.011; exact sign test 0.109 (no normality assumption)

Gateway rows exported to gateway_outcomes.csv.
