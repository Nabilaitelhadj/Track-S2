#!/usr/bin/env python3
"""Build authoritative Track-S cell maps, edge-conditioned domains, and maps.

Geometry is built with exact rational arithmetic from the authoritative
half-open timing partition.  The generated floating-point matrices are all
regenerated from one canonical A1 model and replace the retired exported
center-map dataset.  Exact rational vertices are retained for validated
range/enclosure generation.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import sys
from fractions import Fraction
from pathlib import Path

import numpy as np
from scipy.io import savemat
from scipy.linalg import expm, norm

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)
sys.path.insert(0, str(DATA))
from event_partition_proof import cell_constraints, next_domain, source_lift, target_pullback  # noqa: E402


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


MAT_V5_DESCRIPTION_BYTES = 116
MAT_V5_DETERMINISTIC_DESCRIPTION = (
    b"MATLAB 5.0 MAT-file, deterministic Track-S authoritative problem data"
)


def canonicalize_mat_v5_header(path: Path) -> None:
    """Replace SciPy's timestamped MAT-v5 description with fixed bytes.

    The numeric arrays and MAT directory remain unchanged.  Only the 116-byte
    human-readable description field is canonicalized so repeated clean builds
    produce byte-identical scientific-input containers.
    """
    data = bytearray(path.read_bytes())
    if len(data) < 128:
        raise ValueError(f"MAT-v5 file is unexpectedly short: {path}")
    description = MAT_V5_DETERMINISTIC_DESCRIPTION[:MAT_V5_DESCRIPTION_BYTES]
    data[:MAT_V5_DESCRIPTION_BYTES] = description.ljust(MAT_V5_DESCRIPTION_BYTES, b" ")
    path.write_bytes(data)


def fq(x) -> Fraction:
    return x if isinstance(x, Fraction) else Fraction(str(x))


def solve_fraction(A, b):
    n = len(b)
    M = [[fq(A[i][j]) for j in range(n)] + [fq(b[i])] for i in range(n)]
    for col in range(n):
        pivot = next((r for r in range(col, n) if M[r][col] != 0), None)
        if pivot is None:
            return None
        M[col], M[pivot] = M[pivot], M[col]
        z = M[col][col]
        M[col] = [v / z for v in M[col]]
        for r in range(n):
            if r == col:
                continue
            z = M[r][col]
            if z:
                M[r] = [M[r][j] - z * M[col][j] for j in range(n + 1)]
    return tuple(M[i][-1] for i in range(n))


def closure_vertices(constraints, dimension):
    """Vertices of the closure. Strict facets are closed for outer bounds."""
    vertices = set()
    for idx in itertools.combinations(range(len(constraints)), dimension):
        x = solve_fraction(
            [constraints[i][0] for i in idx], [constraints[i][1] for i in idx]
        )
        if x is None:
            continue
        if all(
            sum(fq(a[j]) * x[j] for j in range(dimension)) <= fq(b)
            for a, b, _strict, _text in constraints
        ):
            vertices.add(x)
    return sorted(vertices, key=lambda x: tuple(float(v) for v in x))


def fracstr(x) -> str:
    return str(x)


def affine_ranges(dwell_formulas, vertices, dimension=3):
    records = []
    for dwell in dwell_formulas:
        coeff = [fq(v) for v in dwell["coeff"]]
        constant = fq(dwell["constant_us"])
        vals = [sum(coeff[j] * x[j] for j in range(dimension)) + constant for x in vertices]
        records.append({"min_us": fracstr(min(vals)), "max_us": fracstr(max(vals))})
    return records


def reconstruct(theta_us, cell, a1, scales):
    nx, nq, nz = 32, 3, 35
    Az = np.zeros((nz, nz))
    Az[:nx, :nx] = a1["A_flow"]
    Az[:nx, nx:] = a1["B_q_flow"]
    M = np.eye(nz)
    for j, dwell in enumerate(cell["dwell_formulas"]):
        dt_us = float(np.dot(np.asarray(dwell["coeff"], float), theta_us) + dwell["constant_us"])
        if dt_us < -1e-12:
            raise ValueError((cell["id"], theta_us, dt_us))
        M = expm(Az * max(dt_us, 0.0) * 1e-6) @ M
        if j < len(cell["events"]):
            M = (
                a1["J_fast"] if cell["events"][j]["kind"] == "F" else a1["J_source"]
            ) @ M
    F = np.zeros((nz, nz))
    F[:nx, :] = M[:nx, :]
    F[nx:, :nx] = a1["K_nominal"]
    D = np.diag(scales)
    return np.diag(1.0 / scales) @ F @ D


def main():
    verified_cells = json.load((DATA / "verified_event_cells.json").open())["cells"]
    verified_graph = json.load((DATA / "verified_event_graph.json").open())
    event_cells = json.load((DATA / "event_cells.json").open())["cells"]
    a1 = np.load(DATA / "reference_apparatus_A1_matrices.npz")
    lyapunov = np.load(DATA / "timing_lyapunov_matrices.npz")
    scales = np.asarray(lyapunov["state_scales"], float)
    retired = np.load(DATA / "timing_center_sdp_problem.npz")["F_centers"]

    region = {"d<=0": 0, "0<d<=50": 1, "d>50": 2}
    verified_by_id = {int(c["id"]): c for c in verified_cells}
    event_by_id = {int(c["id"]): c for c in event_cells}

    # Exact cell domains, closure vertices, and authoritative center maps.
    cell_records = []
    F_cells = []
    for c in verified_cells:
        cid = int(c["id"])
        constraints = cell_constraints(c["N_fast"], c["N_source"], region[c["order_region"]])
        vertices = closure_vertices(constraints, 3)
        if not vertices:
            raise RuntimeError(f"no exact closure vertices for cell {cid}")
        domain_witness = np.asarray(c["interior_witness"]["x"], float)
        representative = np.asarray(event_by_id[cid]["interior_point_us"], float)
        F_cells.append(reconstruct(representative, event_by_id[cid], a1, scales))
        cell_records.append(
            {
                "cell": cid,
                "name": c["name"],
                "variables": ["T_us", "phi_f_us", "phi_s_us"],
                "H": [[fracstr(fq(v)) for v in a] for a, _b, _s, _t in constraints],
                "h": [fracstr(fq(b)) for _a, b, _s, _t in constraints],
                "strict_facets": [i for i, (_a, _b, strict, _text) in enumerate(constraints) if strict],
                "facet_text": [text for _a, _b, _strict, text in constraints],
                "map_representative_us": representative.tolist(),
                "verified_domain_witness_us": domain_witness.tolist(),
                "interior_common_slack": c["interior_witness"]["eps"],
                "event_signature": event_by_id[cid]["event_signature"],
                "phase_update": event_by_id[cid]["phase_update"],
                "closure_vertex_count": len(vertices),
                "closure_vertices_us": [[fracstr(v) for v in x] for x in vertices],
                "dwell_ranges_us": affine_ranges(event_by_id[cid]["dwell_formulas"], vertices),
            }
        )
    F_cells = np.stack(F_cells)

    # Exact joint edge domains (current timing/phase plus next hold duration).
    edge_records = []
    F_edges = []
    for edge_id, edge in enumerate(verified_graph["interior_edges"]):
        source = verified_by_id[int(edge["source"])]
        target = verified_by_id[int(edge["target"])]
        source_constraints = cell_constraints(
            source["N_fast"], source["N_source"], region[source["order_region"]]
        )
        target_constraints = cell_constraints(
            target["N_fast"], target["N_source"], region[target["order_region"]]
        )
        constraints = (
            [source_lift(q) for q in source_constraints]
            + next_domain()
            + [
                target_pullback(q, source["N_fast"], source["N_source"])
                for q in target_constraints
            ]
        )
        vertices = closure_vertices(constraints, 4)
        if not vertices:
            raise RuntimeError(f"no exact closure vertices for edge {source['id']}->{target['id']}")
        representative_joint = np.asarray(edge["witness"]["x"], float)
        theta = representative_joint[:3]
        F_edges.append(reconstruct(theta, event_by_id[int(source["id"])], a1, scales))
        edge_records.append(
            {
                "edge_id": edge_id,
                "source": int(source["id"]),
                "target": int(target["id"]),
                "joint_variables": ["T_us", "phi_f_us", "phi_s_us", "T_next_us"],
                "H_joint": [[fracstr(fq(v)) for v in a] for a, _b, _s, _t in constraints],
                "h_joint": [fracstr(fq(b)) for _a, b, _s, _t in constraints],
                "strict_facets": [i for i, (_a, _b, strict, _text) in enumerate(constraints) if strict],
                "facet_text": [text for _a, _b, _strict, text in constraints],
                "interior_representative_joint_us": representative_joint.tolist(),
                "interior_common_slack": edge["witness"]["eps"],
                "source_theta_us": theta.tolist(),
                "source_event_signature": event_by_id[int(source["id"])]["event_signature"],
                "source_phase_update": event_by_id[int(source["id"])]["phase_update"],
                "closure_vertex_count": len(vertices),
                "closure_vertices_joint_us": [[fracstr(v) for v in x] for x in vertices],
                "dwell_ranges_us": affine_ranges(
                    event_by_id[int(source["id"])]["dwell_formulas"], vertices, 3
                ),
            }
        )
    F_edges = np.stack(F_edges)
    edges = np.asarray([[r["source"], r["target"]] for r in edge_records], int)

    discrepancy = F_cells - retired
    inf_by_cell = np.max(np.abs(discrepancy), axis=(1, 2))
    two_by_cell = np.asarray([norm(D, 2) for D in discrepancy])

    np.savez_compressed(
        DATA / "authoritative_center_maps.npz",
        F_cell_centers=F_cells,
        F_edge_centers=F_edges,
        edges=edges,
        state_scales=scales,
        retired_stored_minus_regenerated_inf_by_cell=inf_by_cell,
        retired_stored_minus_regenerated_2_by_cell=two_by_cell,
    )
    savemat(
        DATA / "authoritative_trackS_problem.mat",
        {
            "F_cell_centers": np.transpose(F_cells, (1, 2, 0)),
            "F_edge_centers": np.transpose(F_edges, (1, 2, 0)),
            "edges_zero_based": edges,
            "edges_one_based": edges + 1,
            "state_scales": scales,
        },
        do_compression=True,
    )
    canonicalize_mat_v5_header(DATA / "authoritative_trackS_problem.mat")

    data_hashes_path = ROOT / "data_hashes.json"
    data_hashes = json.loads(data_hashes_path.read_text(encoding="utf-8"))
    for name in [
        "authoritative_center_maps.npz",
        "authoritative_trackS_problem.mat",
        "edge_conditioned_domains.json",
    ]:
        generated_path = DATA / name
        data_hashes[name] = {
            "sha256": sha256(generated_path),
            "bytes": generated_path.stat().st_size,
        }
    data_hashes_path.write_text(
        json.dumps(data_hashes, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    json.dump(
        {
            "semantics": {
                "cell_maps": "Regenerated from the canonical A1 model at verified positive-slack cell representatives; these replace the retired stored center maps.",
                "edge_maps": "Regenerated at verified positive-slack representatives of each exact joint edge domain.",
                "cell_domain": "Exact rational closure geometry in (T, phi_f, phi_s).",
                "edge_domain": "Exact rational joint geometry in current timing/phase and next hold duration; the sampled map depends on current timing/phase only.",
                "vertices": "Exact rational vertices of the closed domains, used only for rigorous affine dwell-range over-approximation.",
            },
            "cells": cell_records,
            "edge_count": len(edge_records),
            "edges": edge_records,
        },
        (DATA / "edge_conditioned_domains.json").open("w"),
        indent=2,
    )

    result = {
        "status": "AUTHORITATIVE_REGENERATION_COMPLETE",
        "cell_count": 13,
        "edge_count": 45,
        "shape_cell_maps": list(F_cells.shape),
        "shape_edge_maps": list(F_edges.shape),
        "retired_stored_center_max_inf_discrepancy": float(inf_by_cell.max()),
        "retired_stored_center_max_2_discrepancy": float(two_by_cell.max()),
        "discrepancy_treatment": "Retired maps are not used in any SDP or continuum verification; all calculations use the regenerated authoritative maps.",
        "source_hashes": {
            name: sha256(DATA / name)
            for name in [
                "reference_apparatus_A1_matrices.npz",
                "timing_lyapunov_matrices.npz",
                "verified_event_cells.json",
                "verified_event_graph.json",
                "event_cells.json",
            ]
        },
        "outputs": {
            "maps_npz": "data/authoritative_center_maps.npz",
            "problem_mat": "data/authoritative_trackS_problem.mat",
            "domains": "data/edge_conditioned_domains.json",
        },
    }
    json.dump(result, (RESULTS / "authoritative_map_regeneration.json").open("w"), indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
