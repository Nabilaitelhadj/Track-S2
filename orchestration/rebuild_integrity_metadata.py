#!/usr/bin/env python3
"""Rebuild Track-S integrity metadata in dependency order.

Dependency chain (acyclic):
  package files -> package manifest -> status-code archive -> archive ledger
  -> BUNDLE_MANIFEST.json -> repository manifest.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from deterministic_archive import create_archive
from deterministic_manifest import sha256, verify_manifest, write_manifest
from integrity_policy import POLICY_VERSION

PACKAGE_NAME = "trackS_execution_package_v5"
STATUS_ARCHIVE = "queued_dvoc_trackS_execution_package_status_codes.zip"


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def file_record(repo: Path, relative: str) -> dict[str, Any]:
    path = repo / relative
    return {"path": relative, "sha256": sha256(path), "size": path.stat().st_size}


def purge_transients(root: Path) -> list[str]:
    removed: list[str] = []
    for path in sorted(root.rglob("*"), key=lambda p: p.as_posix(), reverse=True):
        if path.is_file() and (path.suffix.lower() in {".pyc", ".pyo"} or path.name.endswith(".log")):
            removed.append(path.relative_to(root).as_posix())
            path.unlink()
        elif path.is_dir() and path.name in {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}:
            removed.append(path.relative_to(root).as_posix() + "/")
            shutil.rmtree(path)
    return sorted(removed)


def run_validator(command: list[str], cwd: Path) -> dict[str, Any]:
    process = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if process.returncode != 0:
        raise RuntimeError(
            f"validator failed ({process.returncode}): {' '.join(command)}\n"
            f"STDOUT:\n{process.stdout}\nSTDERR:\n{process.stderr}"
        )
    return json.loads(Path(command[-1]).read_text(encoding="utf-8")) if command[-2] == "--report" else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    package = repo / PACKAGE_NAME
    source_dir = repo / "source_archives"

    if not package.is_dir():
        raise SystemExit(f"missing package: {package}")
    if not source_dir.is_dir():
        raise SystemExit(f"missing source archive directory: {source_dir}")

    removed = purge_transients(repo)
    (package / "results").mkdir(exist_ok=True)
    (package / "logs").mkdir(exist_ok=True)

    package_manifest = write_manifest(
        package,
        "package_source",
        package / "trackS_package_manifest.json",
        package / "trackS_package_checksums.txt",
    )
    package_verify = verify_manifest(
        package,
        package / "trackS_package_manifest.json",
        package / "trackS_package_checksums.txt",
    )
    if package_verify.get("status") != "PASS":
        raise SystemExit(f"package source manifest failed after generation: {package_verify}")

    status_archive = source_dir / STATUS_ARCHIVE
    create_archive(package, status_archive, PACKAGE_NAME)

    zip_files = sorted(source_dir.glob("*.zip"), key=lambda path: path.name)
    archive_rows: list[tuple[str, str]] = []
    for path in zip_files:
        digest = sha256(path)
        archive_rows.append((digest, path.name))
        (source_dir / f"{path.name}.sha256").write_text(
            f"{digest}  {path.name}\n",
            encoding="utf-8",
            newline="\n",
        )
    (source_dir / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for digest, name in archive_rows),
        encoding="utf-8",
        newline="\n",
    )

    workflow_relative = ".github/workflows/trackS_execute.yml"
    required_paths = [
        workflow_relative,
        "BUNDLE_MANIFEST.json",
        "BUNDLE_SHA256SUMS",
        "repository_source_manifest.json",
        "source_archives/SHA256SUMS",
        f"{PACKAGE_NAME}/trackS_package_manifest.json",
        f"{PACKAGE_NAME}/trackS_package_checksums.txt",
        "orchestration/run_integrity_stage.py",
        "orchestration/run_center_stage.py",
        "orchestration/run_continuum_stage.py",
        "orchestration/final_adjudicate.py",
    ]
    bundle = {
        "schema_version": 2,
        "bundle_name": "Track-S fully repaired execution repository",
        "status": "READY_FOR_WORKFLOW_DISPATCH",
        "integrity_policy_version": POLICY_VERSION,
        "package_name": PACKAGE_NAME,
        "workflow_entrypoint": workflow_relative,
        "workflow_jobs": ["integrity", "center_sdps", "continuum", "final_adjudication"],
        "components": {
            "workflow": file_record(repo, workflow_relative),
            "package_source_manifest": file_record(repo, f"{PACKAGE_NAME}/trackS_package_manifest.json"),
            "package_source_ledger": file_record(repo, f"{PACKAGE_NAME}/trackS_package_checksums.txt"),
            "source_archive_ledger": file_record(repo, "source_archives/SHA256SUMS"),
        },
        "source_archives": [
            {
                "path": f"source_archives/{name}",
                "sha256": digest,
                "size": (source_dir / name).stat().st_size,
            }
            for digest, name in archive_rows
        ],
        "required_repository_paths": required_paths,
        "expected_workflow_artifacts": [
            "trackS-integrity-<run_id>-<attempt>",
            "trackS-center-<run_id>-<attempt>",
            "trackS-continuum-<run_id>-<attempt> (candidate only)",
            "trackS-final-<run_id>-<attempt>",
        ],
        "metadata_dependency_order": [
            "package source files",
            "package source manifest and checksum ledger",
            "deterministic status-code package archive",
            "source archive checksum ledger and sidecars",
            "BUNDLE_MANIFEST.json",
            "repository source manifest and checksum ledger",
        ],
    }
    write_json(repo / "BUNDLE_MANIFEST.json", bundle)

    repository_manifest = write_manifest(
        repo,
        "repository",
        repo / "repository_source_manifest.json",
        repo / "BUNDLE_SHA256SUMS",
    )
    repository_verify = verify_manifest(
        repo,
        repo / "repository_source_manifest.json",
        repo / "BUNDLE_SHA256SUMS",
    )
    if repository_verify.get("status") != "PASS":
        raise SystemExit(f"repository source manifest failed after generation: {repository_verify}")

    with tempfile.TemporaryDirectory(prefix="trackS-finalize-") as tmp:
        tmp_path = Path(tmp)
        workflow_report = tmp_path / "workflow.json"
        bundle_report = tmp_path / "bundle.json"
        workflow_process = subprocess.run(
            [
                sys.executable,
                str(repo / "orchestration" / "validate_workflow.py"),
                str(repo / workflow_relative),
                "--repo-root",
                str(repo),
                "--report",
                str(workflow_report),
            ],
            cwd=repo,
            text=True,
            capture_output=True,
            env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        bundle_process = subprocess.run(
            [
                sys.executable,
                str(repo / "orchestration" / "validate_bundle_metadata.py"),
                "--repo-root",
                str(repo),
                "--report",
                str(bundle_report),
            ],
            cwd=repo,
            text=True,
            capture_output=True,
            env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if workflow_process.returncode != 0 or bundle_process.returncode != 0:
            raise SystemExit(
                "post-generation validation failed\n"
                f"workflow stdout:\n{workflow_process.stdout}\nworkflow stderr:\n{workflow_process.stderr}\n"
                f"bundle stdout:\n{bundle_process.stdout}\nbundle stderr:\n{bundle_process.stderr}"
            )
        workflow_result = json.loads(workflow_report.read_text(encoding="utf-8"))
        bundle_result = json.loads(bundle_report.read_text(encoding="utf-8"))

    result = {
        "status": "PASS",
        "removed_transients": removed,
        "package_entry_count": package_manifest["entry_count"],
        "repository_entry_count": repository_manifest["entry_count"],
        "source_archive_count": len(archive_rows),
        "workflow_validation": workflow_result["status"],
        "bundle_metadata_validation": bundle_result["status"],
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
