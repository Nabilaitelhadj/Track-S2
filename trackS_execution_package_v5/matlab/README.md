# MATLAB/YALMIP Track-S fallback

Requirements:

- MATLAB with YALMIP;
- a primary SDP solver such as MOSEK, SDPA, SDPT3, or SeDuMi.

Run:

```matlab
addpath(genpath('/path/to/yalmip'));
common_sdp_yalmip('/path/to/package','mosek');
graph_sdp_yalmip('/path/to/package','mosek');
```

The common problem maximizes `tau` with `trace(P)=1`. The graph problem uses the 45 authoritative edge-conditioned maps and the global normalization

```text
sum_c trace(P_c)=1.
```

The scripts save every candidate and recompute the rounded PSD/LMI residuals. This double-precision check is diagnostic; final proof-grade verification must be performed with the Julia verified-eigenvalue stage.
