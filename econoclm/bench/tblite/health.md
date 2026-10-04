<!-- Copied from runs/2026-10-03-health-write/health.md (the --write run that produced tasks.txt, code 3e3c56b). The first run, runs/2026-10-03-health, gave the same results apart from times. Host: MacBook Air M2, task containers native linux/arm64. -->

# Task health check (oracle = reference solution)

| Task | Passed | Reward | Time (s) | Error |
|---|---|---|---|---|
| api-endpoint-permission-canonicalizer | yes | 1.0 | 27.6 |  |
| sales-data-csv-analysis | yes | 1.0 | 29.7 |  |
| acl-permissions-inheritance | yes | 1.0 | 34.4 |  |
| iris-dataset-classification | NO | 0.0 | 30.9 |  |
| chained-forensic-extraction_20260101_011957 | yes | 1.0 | 23.8 |  |
| pdf-table-parsing | NO | 0.0 | 22.3 |  |
| grid-pathfinding | NO | 0.0 | 29.4 |  |
| prediction-model-evaluation | NO | 0.0 | 31.7 |  |
| breast-cancer-mlflow | NO | 0.0 | 178.7 |  |
| scan-linux-persistence-artifacts | yes | 1.0 | 35.1 |  |
| maven-slf4j-conflict | yes | 1.0 | 98.0 |  |
| playing-card-recognition | NO | 0.0 | 24.0 |  |
| html-index-analysis | NO | 0.0 | 22.1 |  |
| bandit-delayed-feedback | yes | 1.0 | 33.9 |  |
| anomaly-detection-ranking | yes | 1.0 | 28.4 |  |
| pandas-etl | yes | 1.0 | 33.8 |  |
| malicious-package-forensics | yes | 1.0 | 26.8 |  |

Swaps: 7
- iris-dataset-classification → maven-slf4j-conflict
- pdf-table-parsing → playing-card-recognition
- grid-pathfinding → html-index-analysis
- prediction-model-evaluation → bandit-delayed-feedback
- breast-cancer-mlflow → anomaly-detection-ranking
- playing-card-recognition → pandas-etl
- html-index-analysis → malicious-package-forensics

Healthy tasks (10):
- api-endpoint-permission-canonicalizer
- sales-data-csv-analysis
- acl-permissions-inheritance
- maven-slf4j-conflict
- chained-forensic-extraction_20260101_011957
- pandas-etl
- malicious-package-forensics
- bandit-delayed-feedback
- anomaly-detection-ranking
- scan-linux-persistence-artifacts
