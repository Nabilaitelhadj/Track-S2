# Julia Track-S execution and validated verification

Initialize the project:

```bash
julia --project=julia julia/setup.jl
```

The setup installs JuMP, NPZ, JSON3, SCS, IntervalArithmetic, IntervalMatrices, and IntervalLinearAlgebra. Set `INSTALL_MOSEKTOOLS=1` when a MOSEK license is available.

## Center SDPs

Primary MOSEK run:

```bash
julia --project=julia julia/common_sdp_jump.jl . MOSEK
julia --project=julia julia/graph_sdp_jump.jl . MOSEK
```

SCS may be used only for screening:

```bash
julia --project=julia julia/common_sdp_jump.jl . SCS
julia --project=julia julia/graph_sdp_jump.jl . SCS
```

The common problem maximizes `tau` with `trace(P)=1`. The graph problem uses 45 edge-conditioned maps and the single global normalization `sum_c trace(P_c)=1`.

## Validated continuum enclosures

```bash
julia --project=julia julia/timing_family_enclosure.jl .
python timing_family_enclosure.py --skip-run
```

This produces separate cell and edge enclosures in `results/interval_timing_enclosures.npz`. Exact rational cell/edge vertices define the outward dwell ranges. The independent-dwell representation loses shared timing dependence and may be conservative.

## Proof-grade PSD and robust-margin verification

```bash
julia --project=julia julia/verified_eigenvalue_bounds.jl \
  . common|graph <candidate.npz> results/interval_timing_enclosures.npz
```

The script uses `IntervalLinearAlgebra.verify_eigen` and outward norm bounds. A candidate is accepted only if all positive-definiteness and robust LMI lower bounds are strictly positive and verified.
