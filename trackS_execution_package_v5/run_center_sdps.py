#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PRIMARY = ["MOSEK", "SDPA", "CLARABEL", "CVXOPT", "COPT"]
SCREENING = ["SCS"]
SCRIPTS = ["common_sdp.py", "graph_sdp.py"]

# Status-specific process exit codes.  These distinguish a scientific
# no-certificate result from an unavailable or broken execution toolchain.
EXIT_CANDIDATE_PRODUCED = 0
EXIT_EXECUTED_NO_CANDIDATE = 10
EXIT_SCREENING_ONLY = 11
EXIT_TOOLCHAIN_UNAVAILABLE = 20
EXIT_RUNTIME_ERROR = 21

# common_sdp.py and graph_sdp.py use 0 for an accepted contraction candidate
# and 3 for a completed solve with no accepted contraction candidate.
COMPLETED_CHILD_CODES = {0, 3}


def _write_summary(result: dict[str, Any]) -> None:
    path = ROOT / "results" / "center_sdp_run_summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": result.get("status"),
        "outcome": result.get("outcome"),
        "exit_code": result.get("exit_code"),
        "trackS_classification": result.get("trackS_classification"),
        "installed_solvers": result.get("installed_solvers", []),
    }, indent=2))


def _base_result() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "exit_code_contract": {
            str(EXIT_CANDIDATE_PRODUCED): "accepted primary center contraction candidate produced",
            str(EXIT_EXECUTED_NO_CANDIDATE): "primary solver execution completed; no accepted center candidate",
            str(EXIT_SCREENING_ONLY): "screening solver execution only; no primary center verdict",
            str(EXIT_TOOLCHAIN_UNAVAILABLE): "CVXPY or an approved solver/toolchain is unavailable",
            str(EXIT_RUNTIME_ERROR): "execution attempted but a runtime error prevented a valid scientific outcome",
        },
        "runs": [],
    }


def _solver_pair_completed(runs: list[dict[str, Any]], solver: str) -> bool:
    solver_runs = [run for run in runs if run["solver"] == solver]
    completed_scripts = {
        run["script"] for run in solver_runs if run.get("returncode") in COMPLETED_CHILD_CODES
    }
    return set(SCRIPTS).issubset(completed_scripts)


def _classify(installed: list[str], runs: list[dict[str, Any]]) -> dict[str, Any]:
    primary_runs = [run for run in runs if run["role"] == "primary"]
    screening_runs = [run for run in runs if run["role"] == "screening"]
    primary_candidates = [run for run in primary_runs if run.get("returncode") == 0]
    screening_candidates = [run for run in screening_runs if run.get("returncode") == 0]
    runtime_errors = [
        run for run in runs if run.get("returncode") not in COMPLETED_CHILD_CODES
    ]

    completed_primary_solvers = [
        solver for solver in PRIMARY if _solver_pair_completed(primary_runs, solver)
    ]
    completed_screening_solvers = [
        solver for solver in SCREENING if _solver_pair_completed(screening_runs, solver)
    ]

    details = {
        "primary_runs": len(primary_runs),
        "screening_runs": len(screening_runs),
        "primary_candidate_runs": len(primary_candidates),
        "screening_candidate_runs": len(screening_candidates),
        "completed_primary_solvers": completed_primary_solvers,
        "completed_screening_solvers": completed_screening_solvers,
        "runtime_error_runs": len(runtime_errors),
    }

    if primary_candidates:
        return {
            **details,
            "status": "PRIMARY_CENTER_CONTRACTION_CANDIDATE_PRODUCED_PENDING_CONTINUUM_VERIFICATION",
            "outcome": "CANDIDATE_PRODUCED",
            "exit_code": EXIT_CANDIDATE_PRODUCED,
            "trackS_classification": "CENTER CANDIDATE PRODUCED; PROCEED TO CONTINUUM VERIFICATION",
            "scientific_interpretation": (
                "At least one approved primary solver produced an accepted center contraction candidate. "
                "This is not yet a continuum Track-S certificate."
            ),
        }

    # S-D is scientifically valid only after at least one approved primary
    # solver completed both the common and graph searches.  Runtime failures
    # in other solver backends are preserved as warnings, not relabelled as
    # infeasibility.
    if completed_primary_solvers:
        return {
            **details,
            "status": "PRIMARY_SOLVER_EXECUTION_COMPLETED_NO_ACCEPTED_CENTER_CANDIDATE",
            "outcome": "EXECUTED_NO_CANDIDATE",
            "exit_code": EXIT_EXECUTED_NO_CANDIDATE,
            "trackS_classification": "S-D. NO CENTER CERTIFICATE FOUND; FEASIBILITY REMAINS OPEN",
            "scientific_interpretation": (
                "At least one approved primary solver completed both center searches without an accepted "
                "contraction candidate.  This is a valid S-D search result, not a toolchain failure and not "
                "a formal infeasibility proof."
            ),
        }

    if completed_screening_solvers and not primary_runs:
        return {
            **details,
            "status": "SCREENING_SOLVER_EXECUTION_ONLY_NO_PRIMARY_CENTER_VERDICT",
            "outcome": "SCREENING_ONLY",
            "exit_code": EXIT_SCREENING_ONLY,
            "trackS_classification": "S-F. EXECUTION OR TOOLCHAIN FAILURE — SCREENING ONLY",
            "scientific_interpretation": (
                "Only screening solvers completed.  Their output cannot establish a primary center certificate "
                "or an S-D primary-solver search result."
            ),
        }

    approved_available = [solver for solver in PRIMARY + SCREENING if solver in installed]
    if not approved_available:
        return {
            **details,
            "status": "NO_APPROVED_SDP_SOLVER_AVAILABLE",
            "outcome": "TOOLCHAIN_UNAVAILABLE",
            "exit_code": EXIT_TOOLCHAIN_UNAVAILABLE,
            "trackS_classification": "S-F. EXECUTION OR TOOLCHAIN FAILURE",
            "scientific_interpretation": (
                "CVXPY imported, but no approved primary or screening SDP solver is available."
            ),
        }

    return {
        **details,
        "status": "SDP_RUNTIME_ERROR_NO_VALID_CENTER_OUTCOME",
        "outcome": "RUNTIME_ERROR",
        "exit_code": EXIT_RUNTIME_ERROR,
        "trackS_classification": "S-F. EXECUTION OR TOOLCHAIN FAILURE",
        "scientific_interpretation": (
            "An approved solver was available, but execution did not complete enough primary or screening "
            "searches to assign a scientifically valid candidate or S-D outcome."
        ),
    }


def main() -> int:
    result = _base_result()
    try:
        import cvxpy as cp
    except Exception as exc:
        result.update({
            "status": "CVXPY_NOT_AVAILABLE",
            "outcome": "TOOLCHAIN_UNAVAILABLE",
            "exit_code": EXIT_TOOLCHAIN_UNAVAILABLE,
            "trackS_classification": "S-F. EXECUTION OR TOOLCHAIN FAILURE",
            "scientific_interpretation": (
                "The center SDPs were not executed because CVXPY could not be imported. "
                "No feasibility, infeasibility, or instability conclusion follows."
            ),
            "error": repr(exc),
            "installed_solvers": [],
        })
        _write_summary(result)
        return EXIT_TOOLCHAIN_UNAVAILABLE

    installed = list(cp.installed_solvers())
    available = [solver for solver in PRIMARY + SCREENING if solver in installed]
    runs: list[dict[str, Any]] = []
    for solver in available:
        for script in SCRIPTS:
            try:
                process = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / script),
                        "--root",
                        str(ROOT),
                        "--solver",
                        solver,
                        "--tag",
                        solver.lower(),
                    ],
                    text=True,
                    capture_output=True,
                )
                runs.append({
                    "solver": solver,
                    "role": "primary" if solver in PRIMARY else "screening",
                    "script": script,
                    "returncode": process.returncode,
                    "stdout": process.stdout,
                    "stderr": process.stderr,
                })
            except Exception as exc:
                runs.append({
                    "solver": solver,
                    "role": "primary" if solver in PRIMARY else "screening",
                    "script": script,
                    "returncode": None,
                    "stdout": "",
                    "stderr": repr(exc),
                })

    classification = _classify(installed, runs)
    result.update(classification)
    result["installed_solvers"] = installed
    result["runs"] = runs
    _write_summary(result)
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
