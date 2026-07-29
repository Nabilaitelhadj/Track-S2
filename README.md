# Track-S repaired execution repository

This repository is a complete integrity and workflow repair for the Track-S execution package. It preserves the scientific model and acceptance semantics while correcting the repository-level checksum, archive, workflow, and artifact-handling defects that stopped the previous GitHub Actions run during the integrity job.

## What was repaired

The failed run used top-level manifests that no longer matched the committed bytes of `.gitattributes`, `.gitignore`, and `.github/workflows/trackS_execute.yml`. The integrity stage therefore correctly rejected the checkout before the center SDP jobs could begin. The repaired repository removes that stale-metadata condition and makes the metadata dependency chain explicit and reproducible:

1. immutable package source files;
2. package source manifest and checksum ledger;
3. deterministic status-code source archive;
4. source-archive checksum ledger and sidecars;
5. bundle manifest;
6. repository source manifest and checksum ledger.

The workflow was also corrected so Track-S scientific outcome codes are not confused with GitHub Actions infrastructure failures. Codes `0`, `10`, `11`, `20`, and `21` are validated against the machine-readable center record and then exposed as job outputs. An unknown code, missing record, malformed JSON, checksum mismatch, missing artifact, or YAML contract violation still fails closed.

## Repository entry points

- Workflow: `.github/workflows/trackS_execute.yml`
- Integrity orchestrator: `orchestration/run_integrity_stage.py`
- Metadata finalizer: `orchestration/rebuild_integrity_metadata.py`
- Workflow validator: `orchestration/validate_workflow.py`
- Bundle validator: `orchestration/validate_bundle_metadata.py`
- Scientific package: `trackS_execution_package_v5/`
- Source archives and outer SHA-256 ledger: `source_archives/`

## Local verification

Run the complete integrity stage from the repository root:

```bash
python -m pip install -r orchestration/requirements-integrity.txt
python orchestration/run_integrity_stage.py \
  --repo-root "$PWD" \
  --artifact-root "$PWD/build/integrity_artifact"
```

The command verifies workflow YAML and job contracts, bundle metadata, repository and package source manifests, source archives, Python/JSON/shell syntax, integrity regression tests, authoritative scientific-data regeneration, the clean-package manifest, and independent extraction of the deterministic clean package ZIP.

To rebuild metadata after an intentional source edit, run this only after all source changes are final:

```bash
python orchestration/rebuild_integrity_metadata.py --repo-root "$PWD"
```

The finalizer writes metadata in dependency order and immediately re-verifies it. Do not hand-edit generated manifests or checksum ledgers.

## GitHub Actions behavior

A `workflow_dispatch` run performs four jobs:

1. **integrity**: fail-closed source checks, authoritative regeneration, clean package, and deterministic archive;
2. **center_sdps**: provisioned CVXPY execution and validated center outcome record;
3. **continuum**: Julia-backed continuum verification only when a center candidate exists;
4. **final_adjudication**: artifact-driven final status and a checksummed final bundle, even when an earlier scientific stage records a non-candidate or toolchain outcome.

Every required upload uses `if-no-files-found: error`. The final ZIP is checked against its SHA-256 sidecar before publication.

## Scientific scope

This repair changes repository integrity and execution orchestration only. It does not alter the canonical A1 apparatus, 13 timing cells, 45 edge-conditioned transitions, 35-state scaling, SDP equations, acceptance thresholds, or the meanings of Track-S classifications.
