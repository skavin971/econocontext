#!/usr/bin/env bash
# As run for Study 2 (2026-10-25), copied unchanged except for this line. Paths are the cloud container's.
# Study 2, our arms M0-M5 for one question: 3 repeats, each in a fresh folder, arm order rotated by 2 per repeat.
# Usage: run_ours.sh <qid>
set -u
qid=$1
R=/home/user/skavin971/econocontext
cd "$R" || exit 9
arms=(full ranked_doc ranked_doc_search ranked_doc_search_labels strict_rank_search_labels strict_rank_search_reasoning)
for r in 1 2 3; do
  shift=$(( (r - 1) * 2 ))
  order=("${arms[@]:$shift}" "${arms[@]:0:$shift}")
  out="$R/econo/bcp/full-search/study2/q$qid-r$r"
  log="$R/econo/bcp/setup-logs/s2-q$qid-r$r"
  echo "== q$qid r$r start $(date -u +%T) order: ${order[*]}"
  BCP_SEARCH_OUTPUT="$out" bash "$R/econo/bcp/run_search.sh" --query-ids "$qid" --arms "${order[@]}" \
    --search-url http://127.0.0.1:8790 --protocol clm --rpm 8 > "$log.log" 2>&1
  echo "run exit $?"
  BCP_SEARCH_OUTPUT="$out" bash "$R/econo/bcp/run_search.sh" --report-only --measure-flops > "$log.report.log" 2>&1
  echo "report exit $?"
  grep -E "^  (answered|insufficient|context_limit|call_limit|provider_error|ranking_error|invalid|Failed)" "$log.log"
  cat "$out/errors.jsonl" 2>/dev/null | cut -c1-300
done
echo "all done $(date -u +%T)"
