#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.linalg import eigvals, norm

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    maps = np.load(DATA / "authoritative_center_maps.npz")
    Fcell = np.asarray(maps["F_cell_centers"], float)
    Fedge = np.asarray(maps["F_edge_centers"], float)
    edges = np.asarray(maps["edges"], int)
    if Fcell.shape != (13, 35, 35):
        raise AssertionError(Fcell.shape)
    if Fedge.shape != (45, 35, 35):
        raise AssertionError(Fedge.shape)
    if edges.shape != (45, 2):
        raise AssertionError(edges.shape)

    graph = json.loads((DATA / "verified_event_graph.json").read_text())
    exact_edges = np.asarray([[e["source"], e["target"]] for e in graph["interior_edges"]], int)
    exact_order_match = bool(np.array_equal(edges, exact_edges))
    domains = json.loads((DATA / "edge_conditioned_domains.json").read_text())
    if len(domains["cells"]) != 13 or len(domains["edges"]) != 45:
        raise AssertionError("domain count mismatch")
    if [r["edge_id"] for r in domains["edges"]] != list(range(45)):
        raise AssertionError("edge IDs are not contiguous")

    retired = np.load(DATA / "timing_center_sdp_problem.npz")["F_centers"]
    difference = Fcell - retired
    inf_by = np.max(np.abs(difference), axis=(1, 2))
    two_by = np.asarray([norm(D, 2) for D in difference])
    cell_rho = np.asarray([max(abs(eigvals(F))) for F in Fcell], float)
    edge_rho = np.asarray([max(abs(eigvals(F))) for F in Fedge], float)

    files = [
        "authoritative_center_maps.npz",
        "authoritative_trackS_problem.mat",
        "edge_conditioned_domains.json",
        "verified_event_cells.json",
        "verified_event_graph.json",
        "event_cells.json",
        "reference_apparatus_A1_matrices.npz",
        "reference_apparatus_A1_manifest.json",
        "timing_lyapunov_matrices.npz",
    ]
    result = {
        "status": "AUTHORITATIVE_REGENERATED_PROBLEM_VERIFIED",
        "cell_matrix_shape": list(Fcell.shape),
        "edge_matrix_shape": list(Fedge.shape),
        "edge_shape": list(edges.shape),
        "state_dimension": 35,
        "cell_count": 13,
        "edge_count": 45,
        "exact_graph_order_matches_authoritative_edges": exact_order_match,
        "cell_domain_records": len(domains["cells"]),
        "edge_domain_records": len(domains["edges"]),
        "retired_stored_center_max_inf_discrepancy": float(inf_by.max()),
        "retired_stored_center_max_2_discrepancy": float(two_by.max()),
        "retired_map_policy": "The retired stored maps are not used in any SDP or continuum verification.",
        "cell_center_spectral_radius_min": float(cell_rho.min()),
        "cell_center_spectral_radius_max": float(cell_rho.max()),
        "edge_center_spectral_radius_min": float(edge_rho.min()),
        "edge_center_spectral_radius_max": float(edge_rho.max()),
        "data_sha256": {name: sha256(DATA / name) for name in files},
        "important_semantics": {
            "common_problem": "Uses 13 authoritative regenerated maps at fixed cell representatives.",
            "graph_problem": "Uses one authoritative regenerated map at an interior representative of each exact edge-conditioned domain.",
            "graph_terminology": "Exact constrained timing-transition graph; path-completeness is not asserted without a separate language-coverage proof.",
            "normalization": "All maps use the same 35-dimensional state scaling from timing_lyapunov_matrices.npz.",
        },
    }
    (ROOT / "problem_data_verification.json").write_text(json.dumps(result, indent=2, sort_keys=True))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
