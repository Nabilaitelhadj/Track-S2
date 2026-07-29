#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import scipy.linalg as la

PRIMARY_SDP_SOLVERS = ("MOSEK", "SDPA", "CLARABEL", "CVXOPT", "COPT")
SCREENING_SOLVERS = ("SCS",)
ALL_SDP_SOLVERS = PRIMARY_SDP_SOLVERS + SCREENING_SOLVERS


def load_authoritative(root: Path):
    z = np.load(root / "data" / "authoritative_center_maps.npz")
    Fc = np.asarray(z["F_cell_centers"], float)
    Fe = np.asarray(z["F_edge_centers"], float)
    edges = np.asarray(z["edges"], int)
    if Fc.shape != (13, 35, 35):
        raise ValueError(f"unexpected cell-map shape {Fc.shape}")
    if Fe.shape != (45, 35, 35):
        raise ValueError(f"unexpected edge-map shape {Fe.shape}")
    if edges.shape != (45, 2):
        raise ValueError(f"unexpected edge shape {edges.shape}")
    return Fc, Fe, edges


def solver_options(name: str, high_accuracy: bool = True):
    if name == "SCS":
        return dict(
            eps=1e-8 if high_accuracy else 1e-6,
            max_iters=1_000_000,
            alpha=1.5,
            normalize=True,
            acceleration_lookback=20,
            verbose=False,
        )
    if name == "CLARABEL":
        return dict(
            max_iter=500,
            tol_gap_abs=1e-10,
            tol_gap_rel=1e-10,
            tol_feas=1e-10,
            tol_infeas_abs=1e-10,
            tol_infeas_rel=1e-10,
            verbose=False,
        )
    if name == "CVXOPT":
        return dict(
            abstol=1e-10,
            reltol=1e-10,
            feastol=1e-10,
            max_iters=1000,
            refinement=3,
            verbose=False,
        )
    if name == "MOSEK":
        return {
            "mosek_params": {
                "MSK_DPAR_INTPNT_CO_TOL_PFEAS": 1e-11,
                "MSK_DPAR_INTPNT_CO_TOL_DFEAS": 1e-11,
                "MSK_DPAR_INTPNT_CO_TOL_REL_GAP": 1e-11,
                "MSK_DPAR_INTPNT_CO_TOL_INFEAS": 1e-12,
            }
        }
    if name == "SDPA":
        return dict(epsilonStar=1e-10, epsilonDash=1e-10, maxIteration=1000)
    return {}


def choose_solver(cp, requested=None):
    installed = list(cp.installed_solvers())
    if requested:
        requested = requested.upper()
        if requested not in installed:
            raise RuntimeError(f"requested solver {requested} not installed; installed={installed}")
        if requested not in ALL_SDP_SOLVERS:
            raise RuntimeError(f"{requested} is not approved by this package for SDP execution")
        return requested, installed, requested in SCREENING_SOLVERS
    for solver in PRIMARY_SDP_SOLVERS:
        if solver in installed:
            return solver, installed, False
    for solver in SCREENING_SOLVERS:
        if solver in installed:
            return solver, installed, True
    raise RuntimeError(f"no supported SDP solver installed; installed={installed}")


def sym(A):
    A = np.asarray(A)
    return (A + A.T) / 2


def solver_stats_dict(prob):
    stats = getattr(prob, "solver_stats", None)
    if stats is None:
        return {}
    return {
        "solver_name": getattr(stats, "solver_name", None),
        "solve_time_s": getattr(stats, "solve_time", None),
        "setup_time_s": getattr(stats, "setup_time", None),
        "num_iters": getattr(stats, "num_iters", None),
        "extra_stats_repr": repr(getattr(stats, "extra_stats", None)),
    }


def verify_common(P, F, gamma, tau_solver):
    P = sym(P)
    eig_P = la.eigvalsh(P, check_finite=False)
    raw = []
    shifted_solver = []
    for Fi in F:
        M = sym(gamma * gamma * P - Fi.T @ P @ Fi)
        slack = float(la.eigvalsh(M, check_finite=False)[0])
        raw.append(slack)
        shifted_solver.append(slack - float(tau_solver))
    tau_cert = float(min(raw))
    return {
        "min_eig_P": float(eig_P[0]),
        "max_eig_P": float(eig_P[-1]),
        "condition_P": float(eig_P[-1] / eig_P[0]),
        "trace_P": float(np.trace(P)),
        "tau_solver": float(tau_solver),
        "tau_cert_double": tau_cert,
        "min_raw_lmi_slack": tau_cert,
        "min_shifted_by_solver_tau_slack": float(min(shifted_solver)),
        "cell_raw_slacks": raw,
        "cell_shifted_by_solver_tau_slacks": shifted_solver,
    }


def verify_graph(Ps, Fedge, edges, gamma, tau_solver):
    Ps = [sym(P) for P in Ps]
    eig_min = [float(la.eigvalsh(P, check_finite=False)[0]) for P in Ps]
    eig_max = [float(la.eigvalsh(P, check_finite=False)[-1]) for P in Ps]
    raw = []
    shifted_solver = []
    for edge_index, (source, target) in enumerate(edges):
        M = sym(
            gamma * gamma * Ps[int(source)]
            - Fedge[edge_index].T @ Ps[int(target)] @ Fedge[edge_index]
        )
        slack = float(la.eigvalsh(M, check_finite=False)[0])
        raw.append(slack)
        shifted_solver.append(slack - float(tau_solver))
    tau_cert = float(min(raw))
    return {
        "min_eig_P": min(eig_min),
        "max_eig_P": max(eig_max),
        "condition_P_by_cell": [upper / lower for lower, upper in zip(eig_min, eig_max)],
        "max_condition_P": max(upper / lower for lower, upper in zip(eig_min, eig_max)),
        "sum_trace_P": float(sum(np.trace(P) for P in Ps)),
        "tau_solver": float(tau_solver),
        "tau_cert_double": tau_cert,
        "min_raw_lmi_slack": tau_cert,
        "min_shifted_by_solver_tau_slack": float(min(shifted_solver)),
        "edge_raw_slacks": raw,
        "edge_shifted_by_solver_tau_slacks": shifted_solver,
        "worst_edge_index": int(np.argmin(raw)),
    }


def accepted_status(prob_status, screening):
    # Screening solvers never produce a final accepted certificate.
    return prob_status == "optimal" and not screening


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True))
