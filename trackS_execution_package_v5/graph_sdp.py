#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from sdp_utils import *


def solve_margin(cp, Fedge, edges, gamma, solver, eps):
    cell_count = 13
    n = Fedge.shape[1]
    Ps = [cp.Variable((n, n), symmetric=True, name=f"P_{cell:02d}") for cell in range(cell_count)]
    tau = cp.Variable()
    constraints = [P >> eps * np.eye(n) for P in Ps]
    constraints += [sum(cp.trace(P) for P in Ps) == 1]
    constraints += [
        gamma * gamma * Ps[int(source)]
        - Fedge[edge_index].T @ Ps[int(target)] @ Fedge[edge_index]
        >> tau * np.eye(n)
        for edge_index, (source, target) in enumerate(edges)
    ]
    problem = cp.Problem(cp.Maximize(tau), constraints)
    problem.solve(solver=solver, **solver_options(solver))
    values = [P.value for P in Ps]
    return (values if all(value is not None for value in values) else None, tau.value, problem)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--solver")
    parser.add_argument("--lo", type=float, default=0.0)
    parser.add_argument("--hi", type=float, default=1.05)
    parser.add_argument("--iters", type=int, default=40)
    parser.add_argument("--eps-p", type=float, default=1e-11)
    parser.add_argument("--tau-accept", type=float, default=1e-10)
    parser.add_argument("--tag")
    args = parser.parse_args()

    _, Fedge, edges = load_authoritative(args.root)
    import cvxpy as cp

    solver, installed, screening = choose_solver(cp, args.solver)
    lo, hi = args.lo, args.hi
    best_metric = None
    best_contraction = None
    history = []

    for iteration in range(args.iters):
        gamma = (lo + hi) / 2
        Ps, tau_solver, problem = solve_margin(cp, Fedge, edges, gamma, solver, args.eps_p)
        record = {
            "iteration": iteration,
            "gamma": gamma,
            "status": problem.status,
            "screening_solver": screening,
            **solver_stats_dict(problem),
        }
        metric_feasible = False
        contraction_candidate = False
        if Ps is not None and tau_solver is not None:
            verification = verify_graph(Ps, Fedge, edges, gamma, float(tau_solver))
            record.update(verification)
            tau_cert = verification["tau_cert_double"]
            metric_feasible = bool(
                accepted_status(problem.status, screening)
                and verification["min_eig_P"] > args.eps_p
                and tau_cert > args.tau_accept
            )
            contraction_candidate = bool(metric_feasible and gamma < 1.0)
            record["center_metric_feasible"] = metric_feasible
            record["center_contraction_candidate"] = contraction_candidate
        else:
            verification = None
            record["center_metric_feasible"] = False
            record["center_contraction_candidate"] = False

        record["accepted_for_metric_bisection"] = metric_feasible
        if metric_feasible:
            hi = gamma
            best_metric = (gamma, Ps, float(tau_solver), verification, problem.status)
            if contraction_candidate:
                best_contraction = best_metric
        else:
            lo = gamma
        history.append(record)

    tag = args.tag or solver.lower()
    output = {
        "kind": "edge_conditioned_graph_margin_sdp",
        "model_scope": "frozen rounded canonical A1 linearized hybrid model and exact edge-conditioned timing representatives",
        "authoritative_maps": "45 regenerated edge-conditioned canonical A1 maps",
        "normalization": "sum_c trace(P_c)=1",
        "solver": solver,
        "screening_only": screening,
        "installed_solvers": installed,
        "epsilon_P": args.eps_p,
        "tau_accept": args.tau_accept,
        "edge_count": 45,
        "gamma_lower": lo,
        "gamma_upper": hi,
        "history": history,
    }

    selected = best_contraction or best_metric
    if selected is not None:
        gamma, Ps, tau_solver, verification, solver_status = selected
        tau_cert = verification["tau_cert_double"]
        candidate_file = f"results/graph_center_certificates_{tag}.npz"
        payload = {
            "gamma": gamma,
            "tau": tau_cert,
            "tau_cert": tau_cert,
            "tau_solver": tau_solver,
            "edges": edges,
        }
        payload.update({f"P_{index:02d}": P for index, P in enumerate(Ps)})
        np.savez_compressed(args.root / candidate_file, **payload)
        is_contraction = gamma < 1.0
        output.update(
            {
                "status": (
                    "CENTER_CONTRACTION_CANDIDATE_PENDING_PROOF_GRADE_VERIFICATION"
                    if is_contraction
                    else "CENTER_METRIC_ONLY_GAMMA_NOT_BELOW_ONE"
                ),
                "gamma": gamma,
                "gamma_is_contractive": is_contraction,
                "tau_solver": tau_solver,
                "tau_cert_double": tau_cert,
                "solver_status": solver_status,
                "verification": verification,
                "candidate_file": candidate_file,
            }
        )
    else:
        output["status"] = (
            "NO_ACCEPTED_PRIMARY_SOLVER_CENTER_METRIC_FROM_THIS_RUN"
            if not screening
            else "SCREENING_RUN_ONLY_NO_CERTIFICATE"
        )

    write_json(args.root / "results" / f"graph_solver_results_{tag}.json", output)
    print(json.dumps({key: value for key, value in output.items() if key != "history"}, indent=2))
    return 0 if best_contraction is not None else 3


if __name__ == "__main__":
    raise SystemExit(main())
