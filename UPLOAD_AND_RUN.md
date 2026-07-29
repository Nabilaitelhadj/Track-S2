# Upload and dispatch instructions

No source editing or checksum repair is required after using this package.

1. Replace the contents of the target repository with the contents of this repaired repository, preserving `.github/`, `.gitattributes`, and `.gitignore`.
2. Commit all files together on the default branch. Do not omit the package manifests, repository manifests, source archives, or SHA-256 sidecars.
3. Open **Actions -> Track-S provisioned execution -> Run workflow** and dispatch the new commit.
4. Download the `trackS-final-<run_id>-<attempt>` artifact after completion.

A re-run of an old failed workflow run still uses that run's original commit. Start a new `workflow_dispatch` execution from the repaired commit.

Before pushing, the same integrity gate can be reproduced locally:

```bash
python -m pip install -r orchestration/requirements-integrity.txt
python orchestration/run_integrity_stage.py \
  --repo-root "$PWD" \
  --artifact-root "$PWD/build/integrity_artifact"
```

Expected integrity result:

```json
{
  "status": "PASS",
  "integrity_categories": {
    "workflow_configuration": "PASS",
    "bundle_metadata": "PASS",
    "repository_source_integrity": "PASS",
    "source_archive_integrity": "PASS",
    "authoritative_regeneration": "PASS",
    "clean_package_integrity": "PASS",
    "outer_archive_integrity": "PASS"
  }
}
```
