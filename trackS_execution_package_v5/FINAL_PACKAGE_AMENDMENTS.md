# Final execution-package amendments

This edition applies the last pre-execution amendments without changing the
canonical A1 model, the 13-cell partition, the 45-edge graph, the state scaling,
the SDP formulations, or the scientific decision logic.

## Dependency isolation

- `requirements-core.txt` is the only mandatory Python environment.
- `requirements.txt` is a backward-compatible alias to the core file.
- CVXOPT, SDPA, and MOSEK are isolated in separate optional files.
- `requirements-optional-all.txt` is a convenience bundle and is not required
  for the Clarabel-first execution path.
- `environment.yml` is now a core environment; the all-optional environment is
  separate.

## Status-specific master scripts

`run_center_and_verify.sh` and `run_full_external_trackS.sh` no longer collapse
all non-candidate outcomes into one generic failure code. The center runner now
uses status-specific process codes:

- `0`: accepted primary center candidate;
- `10`: completed primary search with no accepted candidate (**S-D**);
- `11`: screening only;
- `20`: unavailable toolchain (**S-F**);
- `21`: runtime error (**S-F**).

The master scripts read `results/center_sdp_run_summary.json`, validate that its
recorded code matches the observed process return, and assign the scientific
classification from the JSON outcome. This separates a scientifically valid
negative search from a failure to execute.

## Unchanged mathematical acceptance chain

A solver output advances only through:

1. accepted primary-solver status;
2. `gamma < 1`;
3. strict positive definiteness of every rounded metric;
4. independently recomputed `tau_cert > tau_accept`;
5. validated cell/edge continuum enclosure;
6. proof-grade positive eigenvalue bounds for every robust LMI margin.

The package remains fail-closed and Track B1 remains out of scope.
