# Track-S integrity policy

The implementation is defined in `orchestration/integrity_policy.py` and currently uses policy version `trackS-integrity-v2`.

## Integrity layers

### Repository source scope

`repository_source_manifest.json` and `BUNDLE_SHA256SUMS` cover immutable repository source files. They exclude themselves, Git metadata, transient caches, build outputs, run results, logs, and source-archive ZIP binaries that have their own outer ledger.

### Active package source scope

`trackS_execution_package_v5/trackS_package_manifest.json` and `trackS_execution_package_v5/trackS_package_checksums.txt` cover immutable package inputs. Package result directories, clean-package manifests, transient bytecode, and run-generated records are excluded.

### Source archive scope

`source_archives/SHA256SUMS` covers every committed source ZIP. Each archive also has a normalized `.sha256` sidecar. The active package archive is independently extracted and its internal source manifest is verified.

### Clean execution package scope

The integrity job copies only package-source inputs, regenerates authoritative scientific data, verifies the regenerated problem, and only then creates `clean_package_manifest.json` and `clean_package_checksums.txt`. It creates a deterministic stored ZIP and independently extracts and re-verifies the internal clean-package manifest.

### Result artifact scope

Generated results and logs are deliberately outside immutable source manifests. The final adjudication bundle receives its own `SHA256SUMS`, and the outer final ZIP receives a separate SHA-256 sidecar.

## Metadata dependency order

The chain is intentionally acyclic:

```text
package sources
  -> package source manifest
  -> deterministic active-package source archive
  -> source archive ledger and sidecars
  -> BUNDLE_MANIFEST.json
  -> repository source manifest
```

`BUNDLE_MANIFEST.json` does not hash the repository manifest, because doing so would create a checksum cycle. The repository manifest hashes `BUNDLE_MANIFEST.json` instead.

## Line-ending and transient-file policy

`.gitattributes` forces canonical LF text bytes on every platform. `.gitignore` and the Python integrity policy exclude bytecode, virtual environments, caches, build products, results, artifacts, and logs. Regression tests verify both rules.
