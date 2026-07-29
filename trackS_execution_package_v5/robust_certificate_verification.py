#!/usr/bin/env python3
"""Double-precision diagnostic robustification over validated enclosures.

This is not the proof-grade verifier. It fails closed on malformed enclosure
artifacts and reports continuum contraction separately from preservation of
the optimized center margin. Final acceptance requires the Julia interval and
verified-eigenvalue stage.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.linalg import eigvalsh, svdvals

ROOT = Path(__file__).resolve().parent
MODEL_SCOPE = (
    "frozen rounded canonical A1 linearized hybrid model and declared timing continuum; "
    "physical component tolerances and model-identification error are not included"
)


def sym(A):
    A = np.asarray(A)
    return (A + A.T) / 2


def _scalar_bool(array) -> bool:
    return bool(np.asarray(array).reshape(-1)[0])


def load_enclosure(path: Path):
    z = np.load(path, allow_pickle=False)
    required = {
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
    }
    missing = sorted(required.difference(z.files))
    if missing:
        raise ValueError(f"enclosure artifact is missing fail-closed fields: {missing}")

    cell_delta = np.asarray(z["delta_cell_2_bound"], float)
    edge_delta = np.asarray(z["delta_edge_2_bound"], float)
    cell_contained = np.asarray(z["cell_center_contained"], bool)
    edge_contained = np.asarray(z["edge_center_contained"], bool)
    edge_exact = np.asarray(z["edge_exact_domain_used"], bool)
    all_contained = _scalar_bool(z["all_authoritative_centers_contained"])

    if not all_contained or not np.all(cell_contained) or not np.all(edge_contained):
        raise ValueError("authoritative reference maps are not all contained in their enclosures")
    if not np.all(edge_exact):
        raise ValueError("at least one edge enclosure was not built over its exact Theta_cd domain")
    if not np.all(np.isfinite(cell_delta)) or not np.all(np.isfinite(edge_delta)):
        raise ValueError("non-finite continuum uncertainty radius")
    if np.any(cell_delta < 0) or np.any(edge_delta < 0):
        raise ValueError("negative continuum uncertainty radius")

    arrays_to_check = [
        z["F_cell_center"], z["F_cell_rad"], z["F_edge_center"], z["F_edge_rad"]
    ]
    if not all(np.all(np.isfinite(np.asarray(array, float))) for array in arrays_to_check):
        raise ValueError("NaN or infinite matrix interval data")

    return {
        "cell_center": np.asarray(z["F_cell_center"], float),
        "cell_rad": np.asarray(z["F_cell_rad"], float),
        "cell_delta": cell_delta,
        "edge_center": np.asarray(z["F_edge_center"], float),
        "edge_rad": np.asarray(z["F_edge_rad"], float),
        "edge_delta": edge_delta,
        "edges": np.asarray(z["edges"], int),
        "all_authoritative_centers_contained": all_contained,
        "edge_exact_domain_used": bool(np.all(edge_exact)),
    }


def candidate_tau(z) -> tuple[float, float]:
    tau_cert = float(z["tau_cert"]) if "tau_cert" in z else float(z["tau"]) if "tau" in z else 0.0
    tau_solver = float(z["tau_solver"]) if "tau_solver" in z else float("nan")
    return tau_cert, tau_solver


def common(candidate, enclosure):
    z = np.load(candidate)
    P = sym(z["P"])
    gamma = float(z["gamma"])
    tau_cert, tau_solver = candidate_tau(z)
    enc = load_enclosure(enclosure)
    records = []
    pmax = float(eigvalsh(P)[-1])
    for cell, center in enumerate(enc["cell_center"]):
        nominal_matrix = sym(gamma * gamma * P - center.T @ P @ center)
        nominal = float(eigvalsh(nominal_matrix)[0])
        delta = float(enc["cell_delta"][cell])
        penalty = 2 * float(svdvals(P @ center)[0]) * delta + pmax * delta**2
        robust_lower = nominal - penalty
        records.append(
            {
                "cell": cell,
                "center_raw_min_eig": nominal,
                "tau_cert_double": tau_cert,
                "tau_solver": tau_solver,
                "center_margin_after_tau_cert": nominal - tau_cert,
                "delta_2_bound": delta,
                "robust_penalty": penalty,
                "robust_contraction_lower_bound": robust_lower,
                "robust_margin_after_tau": robust_lower - tau_cert,
                "contraction_certified_diagnostic": bool(gamma < 1.0 and robust_lower > 0),
                "center_tau_preserved_diagnostic": bool(robust_lower - tau_cert > 0),
            }
        )
    robust_min = min(record["robust_contraction_lower_bound"] for record in records)
    robust_after_tau_min = min(record["robust_margin_after_tau"] for record in records)
    return {
        "kind": "common",
        "model_scope": MODEL_SCOPE,
        "verification_level": "double_precision_diagnostic_only",
        "gamma": gamma,
        "gamma_is_contractive": gamma < 1.0,
        "tau_cert_double": tau_cert,
        "tau_solver": tau_solver,
        "enclosure_fail_closed_checks_passed": True,
        "records": records,
        "robust_contraction_lower_bound": robust_min,
        "robust_margin_after_tau": robust_after_tau_min,
        "contraction_certified_diagnostic": bool(gamma < 1.0 and robust_min > 0),
        "center_tau_preserved_diagnostic": bool(robust_after_tau_min > 0),
    }


def graph(candidate, enclosure):
    z = np.load(candidate)
    gamma = float(z["gamma"])
    tau_cert, tau_solver = candidate_tau(z)
    Ps = [sym(z[f"P_{index:02d}"]) for index in range(13)]
    enc = load_enclosure(enclosure)
    records = []
    for edge_index, (source, target) in enumerate(enc["edges"]):
        source = int(source)
        target = int(target)
        center = enc["edge_center"][edge_index]
        nominal_matrix = sym(gamma * gamma * Ps[source] - center.T @ Ps[target] @ center)
        nominal = float(eigvalsh(nominal_matrix)[0])
        delta = float(enc["edge_delta"][edge_index])
        penalty = (
            2 * float(svdvals(Ps[target] @ center)[0]) * delta
            + float(eigvalsh(Ps[target])[-1]) * delta**2
        )
        robust_lower = nominal - penalty
        records.append(
            {
                "edge_index": edge_index,
                "source": source,
                "target": target,
                "center_raw_min_eig": nominal,
                "tau_cert_double": tau_cert,
                "tau_solver": tau_solver,
                "center_margin_after_tau_cert": nominal - tau_cert,
                "delta_2_bound": delta,
                "robust_penalty": penalty,
                "robust_contraction_lower_bound": robust_lower,
                "robust_margin_after_tau": robust_lower - tau_cert,
                "contraction_certified_diagnostic": bool(gamma < 1.0 and robust_lower > 0),
                "center_tau_preserved_diagnostic": bool(robust_lower - tau_cert > 0),
            }
        )
    robust_values = [record["robust_contraction_lower_bound"] for record in records]
    after_tau_values = [record["robust_margin_after_tau"] for record in records]
    worst_edge = int(np.argmin(robust_values))
    return {
        "kind": "graph",
        "model_scope": MODEL_SCOPE,
        "verification_level": "double_precision_diagnostic_only",
        "gamma": gamma,
        "gamma_is_contractive": gamma < 1.0,
        "tau_cert_double": tau_cert,
        "tau_solver": tau_solver,
        "enclosure_fail_closed_checks_passed": True,
        "records": records,
        "robust_contraction_lower_bound": min(robust_values),
        "robust_margin_after_tau": min(after_tau_values),
        "worst_edge_index": worst_edge,
        "contraction_certified_diagnostic": bool(gamma < 1.0 and min(robust_values) > 0),
        "center_tau_preserved_diagnostic": bool(min(after_tau_values) > 0),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=["common", "graph"], required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--enclosure", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = common(args.candidate, args.enclosure) if args.kind == "common" else graph(args.candidate, args.enclosure)
    except Exception as exc:
        result = {
            "kind": args.kind,
            "model_scope": MODEL_SCOPE,
            "verification_level": "double_precision_diagnostic_only",
            "status": "FAIL_CLOSED_ENCLOSURE_VALIDATION_ERROR",
            "error": repr(exc),
            "contraction_certified_diagnostic": False,
        }
        out = args.output or ROOT / "results" / f"robust_{args.kind}_verification_diagnostic.json"
        out.write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
        raise SystemExit(4)

    out = args.output or ROOT / "results" / f"robust_{args.kind}_verification_diagnostic.json"
    out.write_text(json.dumps(result, indent=2))
    print(json.dumps({key: value for key, value in result.items() if key != "records"}, indent=2))
    raise SystemExit(0 if result["contraction_certified_diagnostic"] else 3)


if __name__ == "__main__":
    main()
