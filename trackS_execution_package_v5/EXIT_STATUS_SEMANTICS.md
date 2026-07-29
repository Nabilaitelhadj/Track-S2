# Track-S center-stage status and exit-code contract

The scientific center-stage outcome is stored in
`results/center_sdp_run_summary.json`. Shell codes are operational signals and
must not be interpreted through a simple success/failure dichotomy.

| Code | JSON outcome | Scientific meaning |
|---:|---|---|
| 0 | `CANDIDATE_PRODUCED` | An approved primary solver produced an accepted center contraction candidate. Continue to continuum verification. |
| 10 | `EXECUTED_NO_CANDIDATE` | **S-D:** at least one primary solver completed both center searches without an accepted candidate. Feasibility remains open. |
| 11 | `SCREENING_ONLY` | Screening execution only. No primary center conclusion. |
| 20 | `TOOLCHAIN_UNAVAILABLE` | **S-F:** CVXPY or an approved SDP solver/toolchain is unavailable. |
| 21 | `RUNTIME_ERROR` | **S-F:** execution was attempted but did not complete sufficiently to assign a valid center result. |

`center_status.py` checks that the observed process code and JSON code agree.
The master scripts branch on the JSON `outcome` and update
`final_trackS_execution_status.json` with a separate scientific classification
and operational exit code.

A numerical solver's failure to produce a candidate is not a formal
infeasibility proof. An S-D result means only that the completed searches did
not produce an accepted center certificate.
