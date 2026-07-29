#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.linalg import eigvals

from sdp_utils import *


def solve_margin(cp, F, gamma, solver, eps):
    n = F.shape[1]
    P = cp.Variable((n, n), symmetric=True)
    tau = cp.Variable()
    constraints = [P >> eps * np.eye(n), cp.trace(P) == 1]
    constraints += [
        gamma * gamma * P - Fi.T @ P @ Fi >> tau * np.eye(n)
        for Fi in F
    ]
    problem = cp.Problem(cp.Maximize(tau), constraints)
    problem.solve(solver=solver, **solver_options(solver))
    return P.value, tau.value, problem


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--solver")
    parser.add_argument("--lo", type=float)
    parser.add_argument("--hi", type=float, default=1.05)
    parser.add_argument("--iters", type=int, default=40)
    parser.add_argument("--eps-p", type=float, default=1e-10)
    parser.add_argument("--tau-accept", type=float, default=1e-9)
    parser.add_argument("--tag")
    args = parser.parse_args()

    F, _, _ = load_authoritative(args.root)
    import cvxpy as cp

    solver, installed, screening = choose_solver(cp, args.solver)
    rho = max(float(np.max(np.abs(eigvals(Fi)))) for Fi in F)
    lo = max(rho, args.lo if args.lo is not None else rho)
    hi = max(args.hi, lo + 1e-4)
    best_metric = None
    best_contraction = None
    history = []

    for iteration in range(args.iters):
        gamma = (lo + hi) / 2
        P, tau_solver, problem = solve_margin(cp, F, gamma, solver, args.eps_p)
        record = {
            "iteration": iteration,
            "gamma": gamma,
            "status": problem.status,
            "screening_solver": screening,
            **solver_stats_dict(problem),
        }
        metric_feasible = False
        contraction_candidate = False
        if P is not None and tau_solver is not None:
            verification = verify_common(P, F, gamma, float(tau_solver))
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

        # Feasibility at gamma is monotone and may be used for bisection even
        # when gamma >= 1.  Only gamma < 1 is a stability candidate.
        record["accepted_for_metric_bisection"] = metric_feasible
        if metric_feasible:
            hi = gamma
            best_metric = (gamma, P, float(tau_solver), verification, problem.status)
            if contraction_candidate:
                best_contraction = best_metric
        else:
            lo = gamma
        history.append(record)

    tag = args.tag or solver.lower()
    output = {
        "kind": "common_center_margin_sdp",
        "model_scope": "frozen rounded canonical A1 linearized hybrid model and declared timing representatives",
        "authoritative_maps": "regenerated canonical A1 cell-center maps",
        "solver": solver,
        "screening_only": screening,
        "installed_solvers": installed,
        "epsilon_P": args.eps_p,
        "tau_accept": args.tau_accept,
        "center_spectral_radius_lower_bound": rho,
        "gamma_lower": lo,
        "gamma_upper": hi,
        "history": history,
    }

    selected = best_contraction or best_metric
    if selected is not None:
        gamma, P, tau_solver, verification, solver_status = selected
        tau_cert = verification["tau_cert_double"]
        candidate_file = f"results/common_center_certificate_{tag}.npz"
        np.savez_compressed(
            args.root / candidate_file,
            P=P,
            gamma=gamma,
            tau=tau_cert,  # backward-compatible certified center margin
            tau_cert=tau_cert,
            tau_solver=tau_solver,
        )
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

    write_json(args.root / "results" / f"common_solver_results_{tag}.json", output)
    print(json.dumps({key: value for key, value in output.items() if key != "history"}, indent=2))
    return 0 if best_contraction is not None else 3


if __name__ == "__main__":
    raise SystemExit(main())
