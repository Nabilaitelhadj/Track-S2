# Queued-action dVOC Track-S execution package — status-specific execution edition

## Scope and status

This package addresses **Track S only**:

1. common-quadratic center-map certification;
2. graph-dependent multiple-Lyapunov certification on the exact constrained timing-transition graph;
3. validated continuum matrix enclosures;
4. proof-grade residual verification.

It does not execute present-hold or queued-hold nonlinear reachability and does not start Track B1.

Current status in the construction runtime:

> **S-F. EXECUTION OR TOOLCHAIN FAILURE**

The package is ready for external execution, but the local runtime contains no CVXPY/SDP backend and no Julia validated-numerics toolchain. No feasibility, infeasibility, continuum-stability, or instability claim has been fabricated.

## Corrections incorporated

### Authoritative matrices

All center maps are regenerated from one canonical A1 source. The retired stored center maps differ from the regenerated maps by about `1.9346e-6` in maximum normalized infinity norm, but they are no longer used in an SDP or continuum calculation.

The generated problem contains:

- 13 cell-representative maps, each 35 by 35;
- 45 edge-conditioned representative maps, each 35 by 35;
- exact rational cell and joint edge-domain geometry;
- the exact 45 ordinary-interior transitions.

### Common SDP

At fixed `gamma`, solve

```text
maximize tau
subject to
    P >= epsilon_P I
    trace(P) = 1
    gamma^2 P - F_c' P F_c >= tau I,  c=0,...,12.
```

### Graph-dependent SDP

At fixed `gamma`, solve

```text
maximize tau
subject to
    P_c >= epsilon_P I
    sum_c trace(P_c) = 1
    gamma^2 P_c - F_cd' P_d F_cd >= tau I
    for all 45 exact edges c -> d.
```

The single global normalization preserves the relative scale among the Lyapunov pieces. The graph problem uses an edge-conditioned map `F_cd` at a verified interior representative of the exact domain `Theta_cd`; it does not reuse one source-cell map on every outgoing edge.

### Solver acceptance

- MOSEK, SDPA, CLARABEL, CVXOPT, or COPT are primary SDP candidate generators.
- SCS is screening only.
- `optimal_inaccurate` is never accepted.
- A solver status alone is never a certificate.
- Every rounded candidate uses `tau_cert = min lambda_min(gamma^2 P-F^T P F)` (or its graph analogue); the solver-reported `tau_solver` is diagnostic only.
- A center contraction candidate additionally requires `gamma < 1`.
- Every candidate must have `min_eig(P) > epsilon_P` and `tau_cert > tau_accept`. The prior permissive shifted-LMI tolerance is retired.

### Continuum and proof verification

The Julia enclosure script uses exact rational domain vertices, `IntervalArithmetic.jl`, and `IntervalMatrices.jl` to construct outward cell and edge enclosures. It exits fail-closed unless every authoritative center is contained, every uncertainty radius is finite and nonnegative, and every edge uses its exact `Theta_cd` domain. Its first implementation bounds each dwell independently and is therefore rigorous but potentially conservative.

`mpmath` verification is diagnostic only. Final acceptance requires `IntervalLinearAlgebra.verify_eigen` plus outward norm bounds on every robust LMI margin. Reports distinguish `robust_contraction_lower_bound` from the stronger `robust_margin_after_tau`.

The 45-edge object is called the **exact constrained timing-transition graph**. The package does not call it path-complete unless a separate labelled-language coverage argument is supplied.

## Integrity/data generation

```bash
python build_edge_conditioned_data.py
python preflight.py
python verify_problem_data.py
```

Expected generated data:

- `data/regenerated_center_maps.npz`
- `data/edge_center_maps.npz`
- `data/edge_conditioned_domains.json`
- `data/authoritative_trackS_problem.mat`
- `package_integrity_report.json`

## Python/CVXPY execution

Open-source environment:

```bash
conda env create -f environment.yml
conda activate queued-dvoc-tracks-core
# or
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements-core.txt
```

Optional solver environments are isolated so a missing wheel or license cannot block the core Clarabel run:

```bash
python -m pip install -r requirements-cvxopt.txt
python -m pip install -r requirements-sdpa.txt
python -m pip install -r requirements-mosek.txt  # license required at solve time
# Convenience only; not needed for first execution:
python -m pip install -r requirements-optional-all.txt
```

Run all available solvers:

```bash
python run_center_sdps.py
```

Or explicitly:

```bash
python common_sdp.py --solver MOSEK
python graph_sdp.py --solver MOSEK
python common_sdp.py --solver SDPA
python graph_sdp.py --solver SDPA
python common_sdp.py --solver CLARABEL
python graph_sdp.py --solver CLARABEL
python common_sdp.py --solver CVXOPT
python graph_sdp.py --solver CVXOPT
python common_sdp.py --solver SCS   # screening only
python graph_sdp.py --solver SCS    # screening only
```

## Scientific status and process exit semantics

`run_center_sdps.py` writes the scientific center-stage outcome to
`results/center_sdp_run_summary.json`. The master scripts read that JSON and do
**not** infer the Track-S classification from the Boolean distinction
zero/nonzero.

| Exit code | Center-stage outcome | Scientific interpretation |
|---:|---|---|
| `0` | `CANDIDATE_PRODUCED` | An accepted primary center contraction candidate exists; proceed to continuum verification. |
| `10` | `EXECUTED_NO_CANDIDATE` | **S-D:** at least one primary solver completed both center searches with no accepted candidate; feasibility remains open. |
| `11` | `SCREENING_ONLY` | Screening evidence only; no primary center verdict. |
| `20` | `TOOLCHAIN_UNAVAILABLE` | **S-F:** CVXPY or an approved solver/toolchain is unavailable. |
| `21` | `RUNTIME_ERROR` | **S-F:** execution was attempted but did not produce a valid scientific outcome. |

A nonzero process code therefore does not have a single scientific meaning.
In particular, exit `10` is a valid completed S-D search result, whereas exits
`20` and `21` are execution failures. `center_status.py` verifies that the shell
return code matches the JSON record before the master scripts update the final
Track-S status.

## MATLAB/YALMIP

See `matlab/README.md`. The MATLAB formulations use the authoritative maps, margin maximization, and global graph normalization.

## Julia/JuMP and validated continuum enclosure

```bash
julia --project=julia julia/setup.jl
julia --project=julia julia/common_sdp_jump.jl . MOSEK
julia --project=julia julia/graph_sdp_jump.jl . MOSEK
julia --project=julia julia/timing_family_enclosure.jl .
python timing_family_enclosure.py --skip-run
```

## Candidate verification sequence

For a common candidate:

```bash
python high_precision_center_verification.py \
  results/common_center_certificate_mosek.npz --kind common --digits 80

python robust_certificate_verification.py \
  --kind common \
  --candidate results/common_center_certificate_mosek.npz \
  --enclosure results/interval_timing_enclosures.npz

julia --project=julia julia/verified_eigenvalue_bounds.jl \
  . common \
  results/common_center_certificate_mosek.npz \
  results/interval_timing_enclosures.npz
```

Use the analogous `graph` command for graph-dependent candidates.

## Decision logic

- **S-A:** continuum common-quadratic certificate, only after strict verified margins.
- **S-B:** continuum graph-dependent certificate, only after strict verified edge margins.
- **S-C:** center certificate found but continuum robustification remains open.
- **S-D:** no center certificate found; feasibility remains open.
- **S-E:** explicit graph-compatible unstable product found.
- **S-F:** execution or toolchain failure.

A failed primary solve, a failed common-P search, or a failed conservative independent-dwell robustification does not establish instability.


## Certified model boundary

Any Track-S certificate produced by this package applies only to the frozen rounded canonical A1 linearized hybrid model and the declared timing continuum. Decimal-to-binary parameter conversion beyond the frozen matrices, physical component tolerances, controller-coefficient quantization, and model-identification error require a separate matrix-uncertainty enclosure.
