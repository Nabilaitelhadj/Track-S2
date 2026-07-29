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
  EXECUTED_NO_CANDIDATE)
    python update_trackS_status.py --root "$ROOT" \
      --classification "S-D. NO CENTER CERTIFICATE FOUND; FEASIBILITY REMAINS OPEN" \
      --reason "At least one approved primary solver completed both center searches without an accepted contraction candidate." \
      --center-outcome "$CENTER_OUTCOME" --operational-exit-code "$CENTER_RC"
    exit "$CENTER_RC"
    ;;
  SCREENING_ONLY)
    python update_trackS_status.py --root "$ROOT" \
      --classification "S-F. EXECUTION OR TOOLCHAIN FAILURE" \
      --reason "Only screening solvers completed; no primary center verdict is available." \
      --center-outcome "$CENTER_OUTCOME" --operational-exit-code "$CENTER_RC"
    exit "$CENTER_RC"
    ;;
  TOOLCHAIN_UNAVAILABLE)
    python update_trackS_status.py --root "$ROOT" \
      --classification "S-F. EXECUTION OR TOOLCHAIN FAILURE" \
      --reason "CVXPY or an approved SDP solver is unavailable; center execution did not occur." \
      --center-outcome "$CENTER_OUTCOME" --operational-exit-code "$CENTER_RC"
    exit "$CENTER_RC"
    ;;
  RUNTIME_ERROR)
    python update_trackS_status.py --root "$ROOT" \
      --classification "S-F. EXECUTION OR TOOLCHAIN FAILURE" \
      --reason "A runtime error prevented a scientifically valid center outcome." \
      --center-outcome "$CENTER_OUTCOME" --operational-exit-code "$CENTER_RC"
    exit "$CENTER_RC"
    ;;
  CANDIDATE_PRODUCED)
    # The center result is scientifically distinct from continuum execution.
    # If the Julia stage is unavailable, the scientific status remains S-C
    # while the operational exit code records the toolchain failure.
    if ! command -v julia >/dev/null 2>&1; then
      python update_trackS_status.py --root "$ROOT" \
        --classification "S-C. CENTER CERTIFICATE FOUND; CONTINUUM ROBUSTIFICATION OPEN" \
        --reason "An accepted center candidate exists, but Julia validated continuum verification is unavailable in this environment." \
        --center-outcome "$CENTER_OUTCOME" --operational-exit-code 20
      exit 20
    fi
    ;;
  *)
    echo "Unknown center outcome in JSON: $CENTER_OUTCOME" >&2
    exit 21
    ;;
esac

set +e
julia --project=julia julia/timing_family_enclosure.jl "$ROOT"
ENCLOSURE_RC=$?
set -e
if [[ "$ENCLOSURE_RC" -ne 0 ]]; then
  python update_trackS_status.py --root "$ROOT" \
    --classification "S-C. CENTER CERTIFICATE FOUND; CONTINUUM ROBUSTIFICATION OPEN" \
    --reason "An accepted center candidate exists, but validated continuum enclosure execution failed or remains incomplete." \
    --center-outcome "$CENTER_OUTCOME" --operational-exit-code 21
  exit 21
fi

python timing_family_enclosure.py --skip-run
python update_trackS_status.py --root "$ROOT" \
  --classification "S-C. CENTER CERTIFICATE FOUND; CONTINUUM ROBUSTIFICATION OPEN" \
  --reason "An accepted center candidate and validated enclosure data were produced; proof-grade robust contraction verification remains required." \
  --center-outcome "$CENTER_OUTCOME" --operational-exit-code 0

cat <<'EOM'
Center candidates must next be checked with:

  python high_precision_center_verification.py <candidate> --kind common|graph --digits 80
  python robust_certificate_verification.py --kind common|graph --candidate <candidate> --enclosure results/interval_timing_enclosures.npz
  julia --project=julia julia/verified_eigenvalue_bounds.jl . common|graph <candidate> results/interval_timing_enclosures.npz

Only strict proof-grade positive robust margins can advance the final status to S-A or S-B.
EOM
exit 0
