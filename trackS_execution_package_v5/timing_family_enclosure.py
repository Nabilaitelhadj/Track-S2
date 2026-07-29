#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent


def scalar_bool(value) -> bool:
    return bool(np.asarray(value).reshape(-1)[0])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--julia", default="julia")
    parser.add_argument("--skip-run", action="store_true")
    args = parser.parse_args()
    julia = shutil.which(args.julia)
    out_file = ROOT / "results" / "interval_timing_enclosures.npz"
    report_file = ROOT / "results" / "interval_enclosure_report.json"

    if not args.skip_run:
        if julia is None:
            result = {
                "status": "JULIA_NOT_AVAILABLE_EXTERNAL_EXECUTION_REQUIRED",
                "expected_output": str(out_file.relative_to(ROOT)),
            }
            (ROOT / "results" / "timing_family_enclosure_results.json").write_text(json.dumps(result, indent=2))
            print(json.dumps(result, indent=2))
            return 2
        process = subprocess.run(
            [julia, "--project=" + str(ROOT / "julia"), str(ROOT / "julia" / "timing_family_enclosure.jl"), str(ROOT)],
            text=True,
            capture_output=True,
        )
        if process.returncode:
            result = {
                "status": "JULIA_ENCLOSURE_FAILED",
                "returncode": process.returncode,
                "stdout": process.stdout,
                "stderr": process.stderr,
            }
            (ROOT / "results" / "timing_family_enclosure_results.json").write_text(json.dumps(result, indent=2))
            print(json.dumps(result, indent=2))
            return process.returncode

    if not out_file.exists():
        result = {"status": "OUTPUT_NOT_FOUND", "path": str(out_file)}
        print(json.dumps(result, indent=2))
        return 2

    z = np.load(out_file, allow_pickle=False)
    required = [
        "F_cell_center",
        "F_cell_rad",
        "delta_cell_2_bound",
        "F_edge_center",
        "F_edge_rad",
        "delta_edge_2_bound",
        "edges",
        "cell_center_contained",
        "edge_center_contained",
        "edge_exact_domain_used",
        "all_authoritative_centers_contained",
    ]
    missing = [key for key in required if key not in z]
    if missing:
        result = {"status": "ENCLOSURE_OUTPUT_INCOMPLETE_FAIL_CLOSED", "missing": missing}
        print(json.dumps(result, indent=2))
        return 3

    cell_delta = np.asarray(z["delta_cell_2_bound"], float)
    edge_delta = np.asarray(z["delta_edge_2_bound"], float)
    centers_contained = (
        scalar_bool(z["all_authoritative_centers_contained"])
        and bool(np.all(np.asarray(z["cell_center_contained"], bool)))
        and bool(np.all(np.asarray(z["edge_center_contained"], bool)))
    )
    exact_edges = bool(np.all(np.asarray(z["edge_exact_domain_used"], bool)))
    finite_nonnegative = bool(
        np.all(np.isfinite(cell_delta))
        and np.all(np.isfinite(edge_delta))
        and np.all(cell_delta >= 0)
        and np.all(edge_delta >= 0)
    )
    matrices_finite = all(
        np.all(np.isfinite(np.asarray(z[key], float)))
        for key in ["F_cell_center", "F_cell_rad", "F_edge_center", "F_edge_rad"]
    )

    report = json.loads(report_file.read_text()) if report_file.exists() else {}
    report_centers = report.get("all_authoritative_centers_contained", centers_contained)
    report_exact_edges = report.get("all_edge_enclosures_use_exact_Theta_cd", exact_edges)
    pass_checks = bool(
        centers_contained
        and exact_edges
        and finite_nonnegative
        and matrices_finite
        and report_centers
        and report_exact_edges
    )

    result = {
        "status": "ENCLOSURE_LOADED_FAIL_CLOSED_CHECKS_PASSED" if pass_checks else "ENCLOSURE_FAIL_CLOSED_CHECK_FAILED",
        "model_scope": "frozen rounded canonical A1 linearized hybrid model and declared timing continuum",
        "cell_shape": list(z["F_cell_center"].shape),
        "edge_shape": list(z["F_edge_center"].shape),
        "delta_cell_2_bound_min": float(np.min(cell_delta)),
        "delta_cell_2_bound_max": float(np.max(cell_delta)),
        "delta_edge_2_bound_min": float(np.min(edge_delta)),
        "delta_edge_2_bound_max": float(np.max(edge_delta)),
        "all_authoritative_centers_contained": centers_contained,
        "all_edge_enclosures_use_exact_Theta_cd": exact_edges,
        "all_deltas_finite_and_nonnegative": finite_nonnegative,
        "all_matrix_interval_arrays_finite": matrices_finite,
        "verification_level": "validated Julia interval output; robust Lyapunov verification remains separate",
    }
    (ROOT / "results" / "timing_family_enclosure_results.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return 0 if pass_checks else 4


if __name__ == "__main__":
    raise SystemExit(main())
