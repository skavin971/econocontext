# Verification of gx1, gx1b, gx23

Checks per trial: cost re-priced from raw usage (Gemini card: input $0.75/M, cached $0.075/M, output $3.75/M incl. reasoning), gateway calls = agent calls, token totals match, reward = grader file, summary = gateway, Jev tokens session = summary.

**Result: 1 trial(s) with a failed check or an exception.**

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
- gx23:econo+jev:anomaly-detection-ranking:r2: cost $0.152091 (re-priced $0.152091; reasoning-inside $0.135714), calls 23/23, reward 1.0 (file 1.0), jev 0  ok
- gx23:econo+jev:api-endpoint-permission-canonicalizer:r2: cost $0.402214 (re-priced $0.402214; reasoning-inside $0.384612), calls 23/23, reward 1.0 (file 1.0), jev 57952  ok
- gx23:econo+jev:bandit-delayed-feedback:r2: cost $0.538521 (re-priced $0.538521; reasoning-inside $0.493540), calls 44/44, reward 0.0 (file 0.0), jev 100001  ok
- gx23:econo+jev:chained-forensic-extraction_20260101_011957:r2: cost $0.131396 (re-priced $0.131396; reasoning-inside $0.104100), calls 18/18, reward 1.0 (file 1.0), jev 0  ok
- gx23:econo+jev:malicious-package-forensics:r2: cost $0.644635 (re-priced $0.644635; reasoning-inside $0.624737), calls 39/39, reward 0.0 (file 0.0), jev 192113  ok
- gx23:econo+jev:maven-slf4j-conflict:r2: cost $0.322543 (re-priced $0.322543; reasoning-inside $0.315261), calls 36/36, reward 1.0 (file 1.0), jev 179955  ok
- gx23:econo+jev:pandas-etl:r2: cost $0.092603 (re-priced $0.092603; reasoning-inside $0.087900), calls 15/15, reward 1.0 (file 1.0), jev 0  ok
- gx23:econo+jev:sales-data-csv-analysis:r2: cost $0.230968 (re-priced $0.230968; reasoning-inside $0.203319), calls 18/18, reward 1.0 (file 1.0), jev 18795  ok
- gx23:econo+jev:scan-linux-persistence-artifacts:r2: cost $0.590424 (re-priced $0.590424; reasoning-inside $0.558058), calls 40/40, reward 1.0 (file 1.0), jev 168593  ok
- gx23:raw:acl-permissions-inheritance:r2: cost $0.067561 (re-priced $0.067561; reasoning-inside $0.063418), calls 20/20, reward 1.0 (file 1.0), jev 0  ok
- gx23:raw:acl-permissions-inheritance:r3: cost $0.079675 (re-priced $0.079675; reasoning-inside $0.073473), calls 22/22, reward 1.0 (file 1.0), jev 0  ok
- gx23:raw:anomaly-detection-ranking:r2: cost $0.170318 (re-priced $0.170318; reasoning-inside $0.157350), calls 31/31, reward 1.0 (file 1.0), jev 0  ok
- gx23:raw:anomaly-detection-ranking:r3: cost $0.134813 (re-priced $0.134813; reasoning-inside $0.123938), calls 24/24, reward 1.0 (file 1.0), jev 0  ok
- gx23:raw:api-endpoint-permission-canonicalizer:r2: cost $0.516163 (re-priced $0.516163; reasoning-inside $0.495737), calls 26/26, reward 1.0 (file 1.0), jev 0  ok
- gx23:raw:api-endpoint-permission-canonicalizer:r3: cost $0.467242 (re-priced $0.467242; reasoning-inside $0.439593), calls 22/22, reward 1.0 (file 1.0), jev 0  ok
- gx23:raw:bandit-delayed-feedback:r2: cost $0.772609 (re-priced $0.772609; reasoning-inside $0.716801), calls 54/54, reward 0.0 (file 0.0), jev 0  ok
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

## Totals (rebuilt from raw rows)

- raw: 29 trials, passed 24, cost $10.5526, Jev tokens 0, exceptions 0
- econo+jev: 20 trials, passed 17, cost $6.6440, Jev tokens 1,604,238, exceptions 0
- econo+jev vs raw, 20 same-repeat pairs: raw $7.6353, econo+jev $6.6440 (-13.0%), mean diff $-0.0496, sd $0.1289, paired t -1.72 (df 19), cheaper in 15/20
  two-sided p: paired t 0.102; exact sign test 0.041 (no normality assumption)

Gateway rows exported to gateway_outcomes.csv.
