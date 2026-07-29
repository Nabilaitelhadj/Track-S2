# Repair notes

## Root cause confirmed from the attached run artifacts

The most recent integrity artifact reported two failures:

- repository or package source manifest verification failed;
- integrity regression tests failed.

The package source manifest passed. The failing regression was `fresh_checkout_source_manifests`, caused by stale expected records for exactly three top-level files:

- `.gitattributes`;
- `.gitignore`;
- `.github/workflows/trackS_execute.yml`.

The same artifact showed successful authoritative scientific-data regeneration, clean-package manifest verification, deterministic archive extraction, source-archive verification, syntax checks, and all other integrity regression checks.

## Corrective actions

- regenerated package and repository ledgers from final raw bytes;
- rebuilt the active-package source archive deterministically;
- normalized and verified every source-archive SHA-256 sidecar;
- added an acyclic `BUNDLE_MANIFEST.json` contract;
- added strict duplicate-key YAML parsing and workflow contract checks;
- added bundle metadata validation to the integrity job and regression suite;
- upgraded first-party GitHub actions to the current major versions used by this repaired workflow;
- changed recognized scientific center codes from job-failing exits into validated machine-record outputs;
- changed all required artifact uploads to `if-no-files-found: error`;
- added final ZIP sidecar verification before upload.
