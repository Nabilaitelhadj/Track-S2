#!/usr/bin/env python3
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def run(cmd: list[str], cwd: Path, log: Path, timeout_s: int = 14400) -> int:
    try:
        process = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, timeout=timeout_s)
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        process = subprocess.CompletedProcess(cmd, 124, exc.stdout or "", exc.stderr or "")
        timed_out = True
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        f"COMMAND: {' '.join(cmd)}\nRETURN_CODE: {process.returncode}\nTIMEOUT_SECONDS: {timeout_s}\nTIMED_OUT: {timed_out}\n\nSTDOUT\n{process.stdout}\n\nSTDERR\n{process.stderr}\n"
    )
    return process.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    results = root / "results"
    logs = root / "logs"
    accepted_path = results / "accepted_center_candidates.json"
    if not accepted_path.exists():
        summary = {"status": "NO_ACCEPTED_CENTER_CANDIDATE", "scientific_status": "S-C_NOT_APPLICABLE", "records": []}
        write_json(results / "continuum_stage_status.json", summary)
        return 2
    accepted = json.loads(accepted_path.read_text()).get("accepted_candidates", [])
    if not accepted:
        summary = {"status": "NO_ACCEPTED_CENTER_CANDIDATE", "scientific_status": "S-C_NOT_APPLICABLE", "records": []}
        write_json(results / "continuum_stage_status.json", summary)
        return 2

    records: list[dict[str, Any]] = []
    high_precision_records: list[dict[str, Any]] = []
    diagnostic_records: list[dict[str, Any]] = []
    proof_records: list[dict[str, Any]] = []

    # High-precision floating-point checks are performed before the Julia stage.
    # They are diagnostic, not proof-grade.
    for item in accepted:
        kind = item["kind"]
        solver = item["solver"].lower()
        candidate = root / item["candidate_file"]
        hp = results / f"high_precision_{kind}_{solver}.json"
        hp_rc = run(
            [sys.executable, "high_precision_center_verification.py", str(candidate), "--kind", kind, "--digits", "80", "--output", str(hp)],
            root,
            logs / f"high_precision_{kind}_{solver}.log",
            timeout_s=1800,
        )
        hp_data = json.loads(hp.read_text()) if hp.exists() else {"status": "NOT_PRODUCED"}
        high_precision_records.append({"kind": kind, "solver": item["solver"], "returncode": hp_rc, "data": hp_data})

    write_json(
        results / "high_precision_center_verification.json",
        {
            "records": high_precision_records,
            "verification_level": "diagnostic_80_digit_floating_point",
            "proof_grade": False,
        },
    )

    julia_setup_rc = run(["julia", "--project=julia", "julia/setup.jl"], root, logs / "julia_setup.log", timeout_s=7200)
    if julia_setup_rc != 0:
        summary = {
            "status": "JULIA_SETUP_FAILED",
            "scientific_status": "S-C. CENTER CERTIFICATE FOUND; CONTINUUM ROBUSTIFICATION OPEN",
            "center_candidate_status": "NUMERICALLY_VERIFIED_ROUNDED_CANDIDATE",
            "center_verification_level": "DOUBLE_AND_HIGH_PRECISION_INDEPENDENT_RESIDUALS",
            "proof_grade_center_verified": False,
            "proof_grade_continuum_verified": False,
            "continuum_status": "NOT_EXECUTED",
            "records": [],
            "high_precision_records": high_precision_records,
            "julia_setup_returncode": julia_setup_rc,
        }
        write_json(results / "continuum_stage_status.json", summary)
        return 0

    enclosure_rc = run(
        ["julia", "--project=julia", "julia/timing_family_enclosure.jl", str(root)],
        root,
        logs / "timing_family_enclosure.log",
        timeout_s=10800,
    )
    enclosure = results / "interval_timing_enclosures.npz"
    enclosure_report = results / "interval_enclosure_report.json"
    if enclosure_rc != 0 or not enclosure.exists() or not enclosure_report.exists():
        summary = {
            "status": "VALIDATED_CONTINUUM_ENCLOSURE_FAILED_OR_INCOMPLETE",
            "scientific_status": "S-C. CENTER CERTIFICATE FOUND; CONTINUUM ROBUSTIFICATION OPEN",
            "center_candidate_status": "NUMERICALLY_VERIFIED_ROUNDED_CANDIDATE",
            "center_verification_level": "DOUBLE_AND_HIGH_PRECISION_INDEPENDENT_RESIDUALS",
            "proof_grade_center_verified": False,
            "proof_grade_continuum_verified": False,
            "continuum_status": "FAILED_CLOSED_OR_INCOMPLETE",
            "records": [],
            "high_precision_records": high_precision_records,
            "enclosure_returncode": enclosure_rc,
        }
        write_json(results / "continuum_stage_status.json", summary)
        return 0

    for item in accepted:
        kind = item["kind"]
        solver = item["solver"].lower()
        candidate = root / item["candidate_file"]
        diag = results / f"robust_{kind}_{solver}_diagnostic.json"
        proof_generated = results / f"verified_eigenvalue_bounds_{kind}.json"
        proof = results / f"verified_eigenvalue_bounds_{kind}_{solver}.json"
        diag_rc = run(
            [sys.executable, "robust_certificate_verification.py", "--kind", kind, "--candidate", str(candidate), "--enclosure", str(enclosure), "--output", str(diag)],
            root,
            logs / f"robust_{kind}_{solver}_diagnostic.log",
            timeout_s=1800,
        )
        proof_rc = run(
            ["julia", "--project=julia", "julia/verified_eigenvalue_bounds.jl", str(root), kind, str(candidate), str(enclosure)],
            root,
            logs / f"verified_{kind}_{solver}.log",
            timeout_s=7200,
        )
        if proof_generated.exists():
            shutil.copy2(proof_generated, proof)
        proof_data = json.loads(proof.read_text()) if proof.exists() else {}
        diag_data = json.loads(diag.read_text()) if diag.exists() else {"status": "NOT_PRODUCED"}
        diagnostic_records.append({"kind": kind, "solver": item["solver"], "data": diag_data})
        proof_records.append({"kind": kind, "solver": item["solver"], "data": proof_data})
        records.append({
            "kind": kind,
            "solver": item["solver"],
            "candidate_file": item["candidate_file"],
            "diagnostic_returncode": diag_rc,
            "proof_returncode": proof_rc,
            "proof_file": str(proof.relative_to(root)) if proof.exists() else None,
            "proof_grade_center_verified": bool(proof_data.get("P_positive_verified") or proof_data.get("all_P_positive_verified")),
            "proof_grade_continuum_verified": bool(proof_data.get("certificate_verified", False)),
            "robust_contraction_lower_bound": proof_data.get("robust_contraction_lower_bound"),
            "robust_margin_after_tau": proof_data.get("robust_margin_after_tau"),
        })

    common_verified = any(r["kind"] == "common" and r["proof_grade_continuum_verified"] for r in records)
    graph_verified = any(r["kind"] == "graph" and r["proof_grade_continuum_verified"] for r in records)
    if common_verified:
        scientific = "S-A. CONTINUUM COMMON-QUADRATIC CERTIFICATE FOUND"
    elif graph_verified:
        scientific = "S-B. CONTINUUM GRAPH-DEPENDENT CERTIFICATE FOUND"
    else:
        scientific = "S-C. CENTER CERTIFICATE FOUND; CONTINUUM ROBUSTIFICATION OPEN"

    common_proofs = [record for record in proof_records if record["kind"] == "common"]
    graph_proofs = [record for record in proof_records if record["kind"] == "graph"]
    write_json(results / "robust_common_verification.json", {
        "records": common_proofs,
        "diagnostic_records": [r for r in diagnostic_records if r["kind"] == "common"],
        "proof_grade": True,
        "certificate_verified": any(bool(r["data"].get("certificate_verified")) for r in common_proofs),
    })
    write_json(results / "robust_graph_verification.json", {
        "records": graph_proofs,
        "diagnostic_records": [r for r in diagnostic_records if r["kind"] == "graph"],
        "proof_grade": True,
        "certificate_verified": any(bool(r["data"].get("certificate_verified")) for r in graph_proofs),
    })
    write_json(results / "verified_positive_definiteness.json", {
        "records": proof_records,
        "all_reported_bounds_require_successful_interval_certification_flags": True,
        "proof_grade_center_verified": any(
            bool(r["data"].get("P_positive_verified") or r["data"].get("all_P_positive_verified"))
            for r in proof_records
        ),
        "proof_grade_continuum_verified": any(bool(r["data"].get("certificate_verified")) for r in proof_records),
    })

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "CONTINUUM_STAGE_COMPLETED",
        "scientific_status": scientific,
        "center_candidate_status": "NUMERICALLY_VERIFIED_ROUNDED_CANDIDATE",
        "center_verification_level": "DOUBLE_AND_HIGH_PRECISION_INDEPENDENT_RESIDUALS",
        "proof_grade_center_verified": any(r["proof_grade_center_verified"] for r in records),
        "continuum_status": "PROOF_GRADE_VERIFICATION_COMPLETED" if records else "NOT_COMPLETED",
        "enclosure_returncode": enclosure_rc,
        "proof_grade_continuum_verified": bool(common_verified or graph_verified),
        "high_precision_records": high_precision_records,
        "records": records,
    }
    write_json(results / "continuum_stage_status.json", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
