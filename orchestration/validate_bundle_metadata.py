#!/usr/bin/env python3
"""Validate the acyclic Track-S bundle, package, and archive metadata chain."""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from deterministic_manifest import verify_manifest
from integrity_policy import POLICY_VERSION

PACKAGE_NAME = "trackS_execution_package_v5"
STATUS_ARCHIVE = "queued_dvoc_trackS_execution_package_status_codes.zip"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_ledger(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            raise ValueError(f"invalid ledger row {number}: {raw!r}")
        rows.append((parts[0], parts[1].strip()))
    return rows


def expected_file_record(repo: Path, relative: str) -> dict[str, Any]:
    path = repo / relative
    return {"path": relative, "sha256": sha256(path), "size": path.stat().st_size}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    repo = args.repo_root.resolve()
    failures: list[str] = []
    records: dict[str, Any] = {}

    bundle_path = repo / "BUNDLE_MANIFEST.json"
    try:
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    except Exception as exc:
        bundle = {}
        failures.append(f"BUNDLE_MANIFEST.json is missing or malformed: {exc!r}")

    if bundle.get("schema_version") != 2:
        failures.append("unsupported BUNDLE_MANIFEST schema_version")
    if bundle.get("integrity_policy_version") != POLICY_VERSION:
        failures.append("BUNDLE_MANIFEST integrity policy mismatch")
    if bundle.get("package_name") != PACKAGE_NAME:
        failures.append("BUNDLE_MANIFEST package name mismatch")
    if bundle.get("status") != "READY_FOR_WORKFLOW_DISPATCH":
        failures.append("BUNDLE_MANIFEST readiness status is not final")

    package = repo / PACKAGE_NAME
    package_verify: dict[str, Any]
    try:
        package_verify = verify_manifest(
            package,
            package / "trackS_package_manifest.json",
            package / "trackS_package_checksums.txt",
        )
    except Exception as exc:
        package_verify = {"status": "FAIL", "error": repr(exc)}
    records["package_source_manifest"] = package_verify
    if package_verify.get("status") != "PASS":
        failures.append("active package source manifest verification failed")

    expected_components = {
        "workflow": expected_file_record(repo, ".github/workflows/trackS_execute.yml"),
        "package_source_manifest": expected_file_record(repo, f"{PACKAGE_NAME}/trackS_package_manifest.json"),
        "package_source_ledger": expected_file_record(repo, f"{PACKAGE_NAME}/trackS_package_checksums.txt"),
        "source_archive_ledger": expected_file_record(repo, "source_archives/SHA256SUMS"),
    }
    component_results: dict[str, Any] = {}
    for key, expected in expected_components.items():
        actual = bundle.get("components", {}).get(key) if isinstance(bundle.get("components"), dict) else None
        match = actual == expected
        component_results[key] = {"expected": expected, "actual": actual, "match": match}
        if not match:
            failures.append(f"BUNDLE_MANIFEST component mismatch: {key}")
    records["bundle_components"] = component_results

    archive_dir = repo / "source_archives"
    try:
        ledger_rows = parse_ledger(archive_dir / "SHA256SUMS")
    except Exception as exc:
        ledger_rows = []
        failures.append(f"source archive ledger could not be parsed: {exc!r}")

    archive_records: list[dict[str, Any]] = []
    for expected_digest, name in ledger_rows:
        path = archive_dir / name
        actual_digest = sha256(path) if path.is_file() else None
        sidecar = archive_dir / f"{name}.sha256"
        expected_sidecar = f"{expected_digest}  {name}\n"
        sidecar_text = sidecar.read_text(encoding="utf-8") if sidecar.is_file() else None
        zip_error = None
        internal_manifest: dict[str, Any] | None = None
        if path.is_file():
            try:
                with zipfile.ZipFile(path) as archive:
                    zip_error = archive.testzip()
                    if name == STATUS_ARCHIVE and zip_error is None:
                        with tempfile.TemporaryDirectory(prefix="trackS-bundle-check-") as tmp:
                            archive.extractall(tmp)
                            root = Path(tmp) / PACKAGE_NAME
                            internal_manifest = verify_manifest(
                                root,
                                root / "trackS_package_manifest.json",
                                root / "trackS_package_checksums.txt",
                            )
            except Exception as exc:
                zip_error = repr(exc)
        passed = (
            actual_digest == expected_digest
            and sidecar_text == expected_sidecar
            and zip_error is None
            and (
                name != STATUS_ARCHIVE
                or (internal_manifest is not None and internal_manifest.get("status") == "PASS")
            )
        )
        if not passed:
            failures.append(f"source archive metadata or ZIP verification failed: {name}")
        archive_records.append(
            {
                "file": name,
                "expected_sha256": expected_digest,
                "actual_sha256": actual_digest,
                "sidecar_matches": sidecar_text == expected_sidecar,
                "zip_test_error": zip_error,
                "internal_manifest": internal_manifest,
                "status": "PASS" if passed else "FAIL",
            }
        )
    records["source_archives"] = archive_records

    bundle_archives = bundle.get("source_archives")
    expected_bundle_archives = [
        {"path": f"source_archives/{name}", "sha256": digest, "size": (archive_dir / name).stat().st_size}
        for digest, name in ledger_rows
        if (archive_dir / name).is_file()
    ]
    if bundle_archives != expected_bundle_archives:
        failures.append("BUNDLE_MANIFEST source_archives list does not match SHA256SUMS")

    required_paths = bundle.get("required_repository_paths")
    if not isinstance(required_paths, list) or not required_paths:
        failures.append("BUNDLE_MANIFEST required_repository_paths is missing")
    else:
        missing = [str(path) for path in required_paths if not (repo / str(path)).exists()]
        if missing:
            failures.append(f"required repository paths are missing: {missing}")
        records["required_repository_paths"] = {"count": len(required_paths), "missing": missing}

    result = {
        "schema_version": 1,
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "records": records,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"status": result["status"], "failure_count": len(failures)}, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
