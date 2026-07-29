#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
python build_edge_conditioned_data.py
python preflight.py
python verify_problem_data.py

set +e
python run_center_sdps.py
OBSERVED_CENTER_RC=$?
set -e

SUMMARY="$ROOT/results/center_sdp_run_summary.json"
STATUS_LINE="$(python center_status.py --summary "$SUMMARY" --observed-return "$OBSERVED_CENTER_RC" --field shell)"
IFS=$'\t' read -r CENTER_RC CENTER_OUTCOME CENTER_CLASSIFICATION <<< "$STATUS_LINE"

case "$CENTER_OUTCOME" in
  CANDIDATE_PRODUCED)
    REASON="An accepted primary center contraction candidate was produced; continuum verification is required."
    ;;
  EXECUTED_NO_CANDIDATE)
    REASON="Primary solvers completed the common and graph center searches without an accepted candidate; feasibility remains open."
    ;;
  SCREENING_ONLY)
    REASON="Only screening solvers completed; no primary center verdict is available."
    ;;
  TOOLCHAIN_UNAVAILABLE)
    REASON="CVXPY or an approved SDP solver is unavailable; the center SDPs were not executed."
    ;;
  RUNTIME_ERROR)
    REASON="Execution was attempted, but a runtime error prevented a scientifically valid center outcome."
    ;;
  *)
    echo "Unknown center outcome in JSON: $CENTER_OUTCOME" >&2
    exit 21
    ;;
esac

python update_trackS_status.py \
  --root "$ROOT" \
  --classification "$CENTER_CLASSIFICATION" \
  --reason "$REASON" \
  --center-outcome "$CENTER_OUTCOME" \
  --operational-exit-code "$CENTER_RC"

printf '\nCenter SDP execution summary: %s\n' "$SUMMARY"
printf 'Scientific classification: %s\n' "$CENTER_CLASSIFICATION"
printf 'Operational exit code: %s\n' "$CENTER_RC"
exit "$CENTER_RC"
