# Track-S remote execution handoff

This repository contains the complete repaired Track-S workflow and scientific execution package. The repair is operational and provenance-focused; it does not change the underlying Track-S model or scientific acceptance criteria.

## Previous failure

The prior run stopped in `run_integrity_stage.py` because committed source bytes and stale repository checksum records disagreed. Authoritative scientific-data regeneration and clean-package construction had passed, but the top-level repository manifest and one regression test correctly failed.

## Corrected execution order

1. strict workflow and bundle-metadata validation;
2. repository, active-package, and source-archive integrity;
3. clean execution-tree creation;
4. authoritative 13-cell/45-edge regeneration and verification;
5. clean-package manifest generation;
6. deterministic ZIP creation and independent extraction verification;
7. common and graph-dependent center SDPs;
8. conditional continuum verification;
9. artifact-driven final adjudication and final ZIP checksum verification.

See `README.md`, `INTEGRITY_POLICY.md`, and `UPLOAD_AND_RUN.md`.
