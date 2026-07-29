#!/usr/bin/env python3
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PRIMARY = ("MOSEK", "SDPA", "CLARABEL", "CVXOPT", "COPT")
SCREENING = ("SCS",)
KINDS = ("common", "graph")
VALID_CHILD_CODES = {0, 3}
ALLOWED_TERMINAL_STATUSES = {"optimal", "infeasible"}
INVALID_TERMINAL_STATUSES = {
    "optimal_inaccurate", "infeasible_inaccurate", "unbounded", "unbounded_inaccurate",
    "user_limit", "solver_error", "infeasible_or_unbounded",
}
EXIT_CANDIDATE_PRODUCED = 0
EXIT_EXECUTED_NO_CANDIDATE = 10
EXIT_SCREENING_ONLY = 11
EXIT_TOOLCHAIN_UNAVAILABLE = 20
EXIT_RUNTIME_ERROR = 21


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def result_path(root: Path, kind: str, tag: str) -> Path:
    return root / "results" / f"{kind}_solver_results_{tag}.json"


def run_one(root: Path, solver: str, kind: str, timeout_s: int) -> dict[str, Any]:
    tag = solver.lower()
    script = root / f"{kind}_sdp.py"
    log = root / "logs" / f"{kind}_{tag}.log"
    cmd = [sys.executable, str(script), "--root", str(root), "--solver", solver, "--tag", tag]
    try:
        process = subprocess.run(cmd, cwd=root, text=True, capture_output=True, timeout=timeout_s)
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        process = subprocess.CompletedProcess(cmd, 124, exc.stdout or "", exc.stderr or "")
        timed_out = True
    log.write_text(
        f"COMMAND: {' '.join(cmd)}\nRETURN_CODE: {process.returncode}\nTIMEOUT_SECONDS: {timeout_s}\nTIMED_OUT: {timed_out}\n\nSTDOUT\n{process.stdout}\n\nSTDERR\n{process.stderr}\n"
    )
    path = result_path(root, kind, tag)
    record: dict[str, Any] = {
        "solver": solver,
        "role": "primary" if solver in PRIMARY else "screening",
        "kind": kind,
        "returncode": process.returncode,
        "timed_out": timed_out,
        "log": str(log.relative_to(root)),
        "result_file": str(path.relative_to(root)) if path.exists() else None,
        "result_integrity": False,
        "scientifically_interpretable_search": False,
        "accepted_candidate": False,
        "terminal_statuses": [],
        "invalid_terminal_statuses": [],
    }
    if not path.exists():
        record["error"] = "expected result JSON was not produced"
        return record
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        record["error"] = f"malformed result JSON: {exc!r}"
        return record
    record["result_integrity"] = True
    history = data.get("history", [])
    statuses = [str(item.get("status")) for item in history]
    record["terminal_statuses"] = sorted(set(statuses))
    record["invalid_terminal_statuses"] = sorted(
        status for status in set(statuses) if status not in ALLOWED_TERMINAL_STATUSES
    )
    no_bad_statuses = bool(statuses) and not record["invalid_terminal_statuses"]
    record["scientifically_interpretable_search"] = bool(
        process.returncode in VALID_CHILD_CODES and no_bad_statuses
    )

    candidate_key = "candidate_file"
    candidate_rel = data.get(candidate_key)
    candidate = root / candidate_rel if candidate_rel else None
    if candidate_rel:
        record["candidate_file"] = candidate_rel
        record["candidate_exists"] = bool(candidate and candidate.exists())
    gamma = data.get("gamma")
    tau = data.get("tau_cert_double")
    verification = data.get("verification") or {}
    eps_p = float(data.get("epsilon_P", 0.0))
    tau_accept = float(data.get("tau_accept", 0.0))
    record["gamma"] = gamma
    record["tau_cert_double"] = tau
    record["epsilon_P"] = eps_p
    record["tau_accept"] = tau_accept
    record["min_eig_P"] = verification.get("min_eig_P")
    record["solver_status"] = data.get("solver_status")
    record["reported_status"] = data.get("status")
    finite = all(
        value is not None and isinstance(value, (int, float)) and float("-inf") < float(value) < float("inf")
        for value in [gamma, tau, verification.get("min_eig_P")]
    ) if candidate_rel else False
    record["accepted_candidate"] = bool(
        record["role"] == "primary"
        and process.returncode == 0
        and record["scientifically_interpretable_search"]
        and data.get("solver_status") == "optimal"
        and finite
        and float(gamma) < 1.0
        and float(tau) > tau_accept
        and float(verification.get("min_eig_P")) > eps_p
        and candidate is not None
        and candidate.exists()
    )
    if process.returncode == 3 and any(status == "infeasible" for status in statuses):
        record["infeasibility_label"] = "SOLVER_REPORTED_INFEASIBLE_UNVERIFIED"
    return record


def set_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--solver-timeout-s", type=int, default=3000)
    args = parser.parse_args()
    root = args.root.resolve()
    (root / "logs").mkdir(exist_ok=True)
    (root / "results").mkdir(exist_ok=True)

    try:
        import cvxpy as cp
    except Exception as exc:
        summary = {
            "status": "CVXPY_NOT_AVAILABLE",
            "outcome": "TOOLCHAIN_UNAVAILABLE",
            "exit_code": EXIT_TOOLCHAIN_UNAVAILABLE,
            "trackS_classification": "S-F. EXECUTION OR TOOLCHAIN FAILURE",
            "error": repr(exc),
            "runs": [],
        }
        write_json(root / "results" / "center_stage_status.json", summary)
        for key, value in [("outcome", summary["outcome"]), ("scientific_status", summary["trackS_classification"]), ("center_exit_code", str(summary["exit_code"])), ("candidate_produced", "false")]:
            set_output(key, value)
        return EXIT_TOOLCHAIN_UNAVAILABLE

    installed = list(cp.installed_solvers())
    requested = [solver for solver in PRIMARY + SCREENING if solver in installed]
    if not requested:
        summary = {
            "status": "NO_APPROVED_SDP_SOLVER_AVAILABLE",
            "outcome": "TOOLCHAIN_UNAVAILABLE",
            "exit_code": EXIT_TOOLCHAIN_UNAVAILABLE,
            "trackS_classification": "S-F. EXECUTION OR TOOLCHAIN FAILURE",
            "installed_solvers": installed,
            "runs": [],
        }
        write_json(root / "results" / "center_stage_status.json", summary)
        for key, value in [("outcome", summary["outcome"]), ("scientific_status", summary["trackS_classification"]), ("center_exit_code", str(summary["exit_code"])), ("candidate_produced", "false")]:
            set_output(key, value)
        return EXIT_TOOLCHAIN_UNAVAILABLE

    runs = [run_one(root, solver, kind, args.solver_timeout_s) for solver in requested for kind in KINDS]
    primary_candidates = [record for record in runs if record["accepted_candidate"]]
    completed_primary = []
    for solver in PRIMARY:
        pair = [r for r in runs if r["solver"] == solver and r["role"] == "primary"]
        if len(pair) == 2 and all(r["scientifically_interpretable_search"] for r in pair):
            completed_primary.append(solver)
    completed_screening = []
    for solver in SCREENING:
        pair = [r for r in runs if r["solver"] == solver]
        if len(pair) == 2 and all(r["scientifically_interpretable_search"] for r in pair):
            completed_screening.append(solver)

    if primary_candidates:
        outcome, code, classification = (
            "CANDIDATE_PRODUCED", EXIT_CANDIDATE_PRODUCED,
            "CENTER CANDIDATE PRODUCED; PROCEED TO CONTINUUM VERIFICATION",
        )
    elif completed_primary:
        outcome, code, classification = (
            "EXECUTED_NO_CANDIDATE", EXIT_EXECUTED_NO_CANDIDATE,
            "S-D. NO CENTER CERTIFICATE FOUND; FEASIBILITY REMAINS OPEN",
        )
    elif completed_screening and not any(r["role"] == "primary" for r in runs):
        outcome, code, classification = (
            "SCREENING_ONLY", EXIT_SCREENING_ONLY, "NO_PRIMARY_SCIENTIFIC_VERDICT",
        )
    else:
        outcome, code, classification = (
            "RUNTIME_ERROR", EXIT_RUNTIME_ERROR, "S-F. EXECUTION OR TOOLCHAIN FAILURE",
        )

    for kind in KINDS:
        kind_runs = [record for record in runs if record["kind"] == kind]
        write_json(root / "results" / f"{kind}_center_results.json", {
            "kind": kind,
            "runs": kind_runs,
            "accepted_candidates": [record for record in kind_runs if record.get("accepted_candidate")],
        })
        combined_log = []
        for record in kind_runs:
            log_path = root / record["log"]
            combined_log.append(f"===== {record['solver']} / {kind} =====\n")
            if log_path.exists():
                combined_log.append(log_path.read_text())
            combined_log.append("\n")
        (root / "logs" / f"{kind}_solver_log.txt").write_text("".join(combined_log))

    accepted = [
        {
            "kind": r["kind"], "solver": r["solver"], "candidate_file": r.get("candidate_file"),
            "gamma": r.get("gamma"), "tau_cert_double": r.get("tau_cert_double"),
            "center_candidate_status": "NUMERICALLY_VERIFIED_ROUNDED_CANDIDATE",
            "center_verification_level": "DOUBLE_PRECISION_INDEPENDENT_RESIDUALS",
            "proof_grade_center_verified": False,
            "continuum_status": "NOT_EXECUTED",
        }
        for r in primary_candidates
    ]
    summary = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "installed_solvers": installed,
        "approved_primary_solvers": list(PRIMARY),
        "screening_solvers": list(SCREENING),
        "completed_primary_solvers": completed_primary,
        "completed_screening_solvers": completed_screening,
        "runs": runs,
        "accepted_candidates": accepted,
        "status": classification,
        "outcome": outcome,
        "exit_code": code,
        "trackS_classification": classification,
        "scientific_interpretation": {
            "CANDIDATE_PRODUCED": "A numerically verified center contraction candidate was obtained; proof-grade continuum robustification remains open.",
            "EXECUTED_NO_CANDIDATE": "Approved primary searches completed without an accepted candidate; feasibility remains open.",
            "SCREENING_ONLY": "Only screening solvers completed; no primary scientific verdict exists.",
            "RUNTIME_ERROR": "Execution failed before a scientifically interpretable primary outcome.",
        }[outcome],
    }
    write_json(root / "results" / "center_stage_status.json", summary)
    write_json(root / "results" / "accepted_center_candidates.json", {"accepted_candidates": accepted})
    for key, value in [("outcome", outcome), ("scientific_status", classification), ("center_exit_code", str(code)), ("candidate_produced", str(bool(primary_candidates)).lower())]:
        set_output(key, value)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
