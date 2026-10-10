"""Our benchmark: the 10 frozen TBLite tasks and where the task folders live.

Why it exists: every experiment runs the same tasks, so the list is defined once, here. It was chosen
by EconoCLM's seed 20261003, and every task's oracle solution was health-checked. The tasks
themselves are OpenThoughts-TBLite, outside this repo; TBLITE_DIR overrides the default location.
(The constants are verbatim from the old Claude Code runner, archive/claude_code/benchmarks/run.py.)
"""

import os
from pathlib import Path

TBLITE = Path(os.environ.get("TBLITE_DIR", Path.home() / "econo" / "OpenThoughts-TBLite"))
FROZEN = ["api-endpoint-permission-canonicalizer", "sales-data-csv-analysis", "acl-permissions-inheritance",
          "maven-slf4j-conflict", "chained-forensic-extraction_20260101_011957", "pandas-etl",
          "malicious-package-forensics", "bandit-delayed-feedback", "anomaly-detection-ranking",
          "scan-linux-persistence-artifacts"]  # EconoCLM seed 20261003, oracle health-checked
