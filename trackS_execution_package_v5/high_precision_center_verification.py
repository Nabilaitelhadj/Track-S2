#!/usr/bin/env python3
"""High-precision diagnostic verification of rounded center candidates.

This script is deliberately NOT proof-grade.  It detects ordinary floating-
point failures before the candidate is passed to the Julia interval/verified-
eigenvalue verifier.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import mpmath as mp
import numpy as np

ROOT = Path(__file__).resolve().parent


def mp_matrix(A):
    return mp.matrix([[mp.mpf(str(float(x))) for x in row] for row in np.asarray(A)])


def eigmin_sym(A):
    vals, _ = mp.eigsy((A + A.T) / 2)
    return vals[0], vals[-1]


def load_maps():
    z = np.load(ROOT / "data" / "authoritative_center_maps.npz")
    return z["F_cell_centers"], z["F_edge_centers"], z["edges"]


def verify_common(candidate: Path, digits: int):
    mp.mp.dps = digits
    z = np.load(candidate)
    P = mp_matrix(z["P"])
    gamma = mp.mpf(str(float(z["gamma"])))
    tau = mp.mpf(str(float(z["tau_cert"]))) if "tau_cert" in z else (mp.mpf(str(float(z["tau"]))) if "tau" in z else mp.mpf("0"))
    F, _Fedge, _edges = load_maps()
    pmin, pmax = eigmin_sym(P)
    raw = []
    shifted = []
    for Fi in F:
        C = mp_matrix(Fi)
        M = gamma**2 * P - C.T * P * C
        a = eigmin_sym(M)[0]
        raw.append(a)
        shifted.append(a - tau)
    return {
        "kind": "common",
        "verification_level": "high_precision_diagnostic_not_rigorous_enclosure",
        "digits": digits,
        "gamma": str(gamma),
        "tau_cert": str(tau),
        "min_eig_P": str(pmin),
        "max_eig_P": str(pmax),
        "min_raw_lmi_slack": str(min(raw)),
        "min_shifted_lmi_slack": str(min(shifted)),
        "cell_raw_slacks": [str(x) for x in raw],
    }


def verify_graph(candidate: Path, digits: int):
    mp.mp.dps = digits
    z = np.load(candidate)
    gamma = mp.mpf(str(float(z["gamma"])))
    tau = mp.mpf(str(float(z["tau_cert"]))) if "tau_cert" in z else (mp.mpf(str(float(z["tau"]))) if "tau" in z else mp.mpf("0"))
    Ps = [mp_matrix(z[f"P_{i:02d}"]) for i in range(13)]
    _F, Fedge, edges = load_maps()
    pmins, pmaxs = [], []
    for P in Ps:
        a, b = eigmin_sym(P)
        pmins.append(a)
        pmaxs.append(b)
    raw, shifted = [], []
    for e, (c, d) in enumerate(edges):
        C = mp_matrix(Fedge[e])
        M = gamma**2 * Ps[int(c)] - C.T * Ps[int(d)] * C
        a = eigmin_sym(M)[0]
        raw.append(a)
        shifted.append(a - tau)
    return {
        "kind": "graph",
        "verification_level": "high_precision_diagnostic_not_rigorous_enclosure",
        "digits": digits,
        "gamma": str(gamma),
        "tau_cert": str(tau),
        "min_eig_P": str(min(pmins)),
        "max_eig_P": str(max(pmaxs)),
        "min_raw_lmi_slack": str(min(raw)),
        "min_shifted_lmi_slack": str(min(shifted)),
        "edge_raw_slacks": [str(x) for x in raw],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--kind", choices=["common", "graph"], required=True)
    parser.add_argument("--digits", type=int, default=80)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify_common(args.candidate, args.digits) if args.kind == "common" else verify_graph(args.candidate, args.digits)
    out = args.output or ROOT / "results" / f"high_precision_{args.kind}_verification.json"
    out.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
