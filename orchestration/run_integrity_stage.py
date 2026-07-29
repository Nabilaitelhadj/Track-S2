#!/usr/bin/env python3
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from deterministic_archive import create_archive, verify_archive
from deterministic_manifest import sha256, verify_manifest, write_manifest


def run(command: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged["PYTHONDONTWRITEBYTECODE"] = "1"
    if env:
        merged.update(env)
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, env=merged)


def parse_checksum_file(path: Path) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        digest, name = line.split(None, 1)
        records.append((digest, name.strip()))
    return records



def purge_bytecode(root: Path) -> list[str]:
    removed: list[str] = []
    for path in sorted(root.rglob("*"), key=lambda p: p.as_posix(), reverse=True):
        if path.is_file() and path.suffix.lower() in {".pyc", ".pyo"}:
            removed.append(path.relative_to(root).as_posix())
            path.unlink()
        elif path.is_dir() and path.name == "__pycache__":
            rel = path.relative_to(root).as_posix() + "/"
            shutil.rmtree(path)
            removed.append(rel)
    return sorted(set(removed))

def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    artifact = args.artifact_root.resolve()
    if artifact.exists():
        shutil.rmtree(artifact)
    artifact.mkdir(parents=True)
    reports = artifact / "integrity_reports"
    reports.mkdir()

    failures: list[str] = []
    records: dict[str, Any] = {}
    records["purged_transient_bytecode"] = purge_bytecode(repo)
    categories = {
        "workflow_configuration": "FAIL",
        "bundle_metadata": "FAIL",
        "repository_source_integrity": "FAIL",
        "source_archive_integrity": "FAIL",
        "authoritative_regeneration": "FAIL",
        "clean_package_integrity": "FAIL",
        "outer_archive_integrity": "FAIL",
        "result_artifact_integrity": "NOT_APPLICABLE",
    }

    # 0. Validate GitHub Actions syntax/contracts and the acyclic metadata
    # chain before operating on any scientific inputs.
    workflow_report = reports / "workflow_validation_report.json"
    workflow_process = run([
        sys.executable,
        str(repo / "orchestration" / "validate_workflow.py"),
        str(repo / ".github" / "workflows" / "trackS_execute.yml"),
        "--repo-root", str(repo),
        "--report", str(workflow_report),
    ], cwd=repo)
    (reports / "workflow_validation.log").write_text(
        workflow_process.stdout + "\n" + workflow_process.stderr,
        encoding="utf-8",
        newline="\n",
    )
    records["workflow_validation"] = (
        json.loads(workflow_report.read_text(encoding="utf-8"))
        if workflow_report.exists()
        else {"status": "FAIL", "error": "workflow validation report missing"}
    )
    if workflow_process.returncode == 0:
        categories["workflow_configuration"] = "PASS"
    else:
        failures.append("GitHub Actions workflow validation failed")

    bundle_report = reports / "bundle_metadata_validation.json"
    bundle_process = run([
        sys.executable,
        str(repo / "orchestration" / "validate_bundle_metadata.py"),
        "--repo-root", str(repo),
        "--report", str(bundle_report),
    ], cwd=repo)
    (reports / "bundle_metadata_validation.log").write_text(
        bundle_process.stdout + "\n" + bundle_process.stderr,
        encoding="utf-8",
        newline="\n",
    )
    records["bundle_metadata_validation"] = (
        json.loads(bundle_report.read_text(encoding="utf-8"))
        if bundle_report.exists()
        else {"status": "FAIL", "error": "bundle metadata report missing"}
    )
    if bundle_process.returncode == 0:
        categories["bundle_metadata"] = "PASS"
    else:
        failures.append("bundle metadata validation failed")

    # 1. Verify immutable repository and active package manifests.
    try:
        repo_manifest = verify_manifest(
            repo,
            repo / "repository_source_manifest.json",
            repo / "BUNDLE_SHA256SUMS",
        )
    except Exception as exc:
        repo_manifest = {"status": "FAIL", "error": repr(exc)}
    package = repo / "trackS_execution_package_v5"
    try:
        package_manifest = verify_manifest(
            package,
            package / "trackS_package_manifest.json",
            package / "trackS_package_checksums.txt",
        )
    except Exception as exc:
        package_manifest = {"status": "FAIL", "error": repr(exc)}
    records["repository_source_manifest"] = repo_manifest
    records["package_source_manifest"] = package_manifest
    if repo_manifest.get("status") == "PASS" and package_manifest.get("status") == "PASS":
        categories["repository_source_integrity"] = "PASS"
    else:
        failures.append("repository or package source manifest verification failed")

    # 2. Verify outer source-archive SHA-256 values and ZIP integrity.  The
    # active package archive must also verify its own internal source manifest.
    source_dir = repo / "source_archives"
    source_records = []
    source_ok = True
    for expected, name in parse_checksum_file(source_dir / "SHA256SUMS"):
        path = source_dir / name
        actual = sha256(path) if path.exists() else None
        zip_error = None
        internal_manifest = None
        if path.exists():
            try:
                with zipfile.ZipFile(path) as zf:
                    zip_error = zf.testzip()
                    if name == "queued_dvoc_trackS_execution_package_status_codes.zip" and zip_error is None:
                        with tempfile.TemporaryDirectory(prefix="trackS-source-archive-") as tmp:
                            zf.extractall(tmp)
                            root = Path(tmp) / "trackS_execution_package_v5"
                            internal_manifest = verify_manifest(
                                root,
                                root / "trackS_package_manifest.json",
                                root / "trackS_package_checksums.txt",
                            )
            except Exception as exc:
                zip_error = repr(exc)
        ok = actual == expected and zip_error is None and (
            name != "queued_dvoc_trackS_execution_package_status_codes.zip"
            or (internal_manifest and internal_manifest.get("status") == "PASS")
        )
        source_ok &= bool(ok)
        source_records.append({
            "file": name,
            "expected_sha256": expected,
            "actual_sha256": actual,
            "zip_test_error": zip_error,
            "internal_manifest": internal_manifest,
            "pass": bool(ok),
        })
    records["source_archives"] = source_records
    if source_ok:
        categories["source_archive_integrity"] = "PASS"
    else:
        failures.append("source archive integrity failed")

    # 3. Syntax and serialization checks do not write bytecode into the tree.
    python_failures = []
    python_count = 0
    for path in sorted(list(package.rglob("*.py")) + list((repo / "orchestration").rglob("*.py"))):
        python_count += 1
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except Exception as exc:
            python_failures.append({"file": str(path.relative_to(repo)), "error": repr(exc)})
    if python_failures:
        failures.append("Python AST validation failed")
    records["python_syntax"] = {"count": python_count, "failures": python_failures}

    json_failures = []
    json_count = 0
    for path in sorted(repo.rglob("*.json")):
        if any(part in {".git", "build", "artifacts", "results", "logs"} for part in path.parts):
            continue
        json_count += 1
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            json_failures.append({"file": str(path.relative_to(repo)), "error": repr(exc)})
    if json_failures:
        failures.append("JSON validation failed")
    records["json_validation"] = {"count": json_count, "failures": json_failures}

    shell_records = []
    for path in sorted(package.rglob("*.sh")):
        process = run(["bash", "-n", str(path)])
        shell_records.append({"file": str(path.relative_to(package)), "returncode": process.returncode, "stderr": process.stderr})
        if process.returncode != 0:
            failures.append(f"shell syntax failed: {path.relative_to(package)}")
    records["shell_syntax"] = shell_records

    # 4. Regression tests for the three historical integrity defects.
    regression_report = reports / "integrity_regression_tests.json"
    regression = run([
        sys.executable,
        str(repo / "orchestration" / "test_integrity_regressions.py"),
        "--repo-root", str(repo),
        "--output", str(regression_report),
    ], cwd=repo)
    (reports / "integrity_regression_tests.log").write_text(regression.stdout + "\n" + regression.stderr, encoding="utf-8", newline="\n")
    records["regression_tests"] = json.loads(regression_report.read_text()) if regression_report.exists() else {"status": "FAIL", "error": "result missing"}
    if regression.returncode != 0:
        failures.append("integrity regression tests failed")

    # 5. Create an unfinalized clean execution tree.  No checksum is written
    # until authoritative regeneration and all problem-data checks are done.
    clean = artifact / "trackS_execution_package_v5"
    clean_report = reports / "clean_package_copy_report.json"
    process = run([
        sys.executable,
        str(repo / "orchestration" / "prepare_clean_package.py"),
        "--source", str(package),
        "--destination", str(clean),
        "--report", str(clean_report),
    ])
    (reports / "prepare_clean_package.log").write_text(process.stdout + "\n" + process.stderr, encoding="utf-8", newline="\n")
    if process.returncode != 0:
        failures.append("clean package preparation failed")

    # 6. Regenerate and verify authoritative scientific inputs in the clean
    # tree before generating its checksum ledger.
    authoritative_ok = False
    problem: dict[str, Any] | None = None
    if clean.exists():
        for script, log_name in [
            ("build_edge_conditioned_data.py", "build_edge_conditioned_data.log"),
            ("verify_problem_data.py", "verify_problem_data.log"),
        ]:
            process = run([sys.executable, script], cwd=clean)
            (reports / log_name).write_text(process.stdout + "\n" + process.stderr, encoding="utf-8", newline="\n")
            if process.returncode != 0:
                failures.append(f"{script} failed")
        problem_path = clean / "problem_data_verification.json"
        problem = json.loads(problem_path.read_text()) if problem_path.exists() else None
        records["problem_data_verification"] = problem
        authoritative_ok = bool(
            problem
            and problem.get("status") == "AUTHORITATIVE_REGENERATED_PROBLEM_VERIFIED"
            and problem.get("exact_graph_order_matches_authoritative_edges")
            and problem.get("cell_count") == 13
            and problem.get("edge_count") == 45
            and problem.get("state_dimension") == 35
        )
        if not authoritative_ok:
            failures.append("authoritative problem-data verification failed")
        if problem_path.exists():
            shutil.copy2(problem_path, reports / "problem_data_verification.json")
            problem_path.unlink()  # result record, not immutable package input
        for name in ["authoritative_center_maps.npz", "edge_center_maps.npz", "authoritative_trackS_problem.mat"]:
            path = clean / "data" / name
            if path.exists():
                shutil.copy2(path, reports / name)
            else:
                failures.append(f"missing regenerated scientific input: {name}")
    if authoritative_ok:
        categories["authoritative_regeneration"] = "PASS"

    # 7. Generate the clean-package manifest only after regeneration is final.
    clean_manifest_result = None
    clean_manifest_verify = None
    scientific_hashes_before_archive = {}
    if clean.exists() and authoritative_ok:
        manifest_json = clean / "clean_package_manifest.json"
        manifest_text = clean / "clean_package_checksums.txt"
        clean_manifest_result = write_manifest(clean, "clean_package", manifest_json, manifest_text)
        clean_manifest_verify = verify_manifest(clean, manifest_json, manifest_text)
        for relative in [
            "data/authoritative_center_maps.npz",
            "data/edge_center_maps.npz",
            "data/authoritative_trackS_problem.mat",
            "data/edge_conditioned_domains.json",
        ]:
            scientific_hashes_before_archive[relative] = sha256(clean / relative)
        if clean_manifest_verify.get("status") != "PASS":
            failures.append("clean package manifest verification failed")
    records["clean_package_manifest"] = clean_manifest_verify

    # 8. Create a deterministic stored ZIP, independently extract it, and
    # verify the internal clean-package ledger again.
    archive = artifact / "trackS_clean_execution_package.zip"
    archive_verify = None
    if clean.exists() and clean_manifest_verify and clean_manifest_verify.get("status") == "PASS":
        create_archive(clean, archive, "trackS_execution_package_v5")
        archive_verify = verify_archive(
            archive,
            "trackS_execution_package_v5",
            "clean_package_manifest.json",
            "clean_package_checksums.txt",
        )
        (artifact / "trackS_clean_execution_package.zip.sha256").write_text(
            f"{sha256(archive)}  {archive.name}\n", encoding="utf-8", newline="\n"
        )
        scientific_hashes_after_archive = {
            relative: sha256(clean / relative) for relative in scientific_hashes_before_archive
        }
        records["post_manifest_scientific_input_unchanged"] = {
            "status": "PASS" if scientific_hashes_before_archive == scientific_hashes_after_archive else "FAIL",
            "before": scientific_hashes_before_archive,
            "after": scientific_hashes_after_archive,
        }
        if scientific_hashes_before_archive != scientific_hashes_after_archive:
            failures.append("scientific input changed after clean-package ledger generation")
        if archive_verify.get("status") != "PASS":
            failures.append("outer archive extraction or internal manifest verification failed")
    records["outer_archive_verification"] = archive_verify

    if clean_manifest_verify and clean_manifest_verify.get("status") == "PASS":
        categories["clean_package_integrity"] = "PASS"
    if archive_verify and archive_verify.get("status") == "PASS":
        categories["outer_archive_integrity"] = "PASS"

    required_pass = [
        categories["workflow_configuration"],
        categories["bundle_metadata"],
        categories["repository_source_integrity"],
        categories["source_archive_integrity"],
        categories["authoritative_regeneration"],
        categories["clean_package_integrity"],
        categories["outer_archive_integrity"],
    ]
    status = "PASS" if not failures and all(value == "PASS" for value in required_pass) else "FAIL"
    if status == "PASS":
        reader_message = "Workflow configuration, bundle metadata, repository source, authoritative regeneration, clean execution package, and outer archive all passed independent integrity checks."
    elif categories["authoritative_regeneration"] == "PASS":
        reader_message = "Repository-package checksum verification failed; authoritative problem-data regeneration passed."
    else:
        reader_message = "Repository-package integrity and/or authoritative problem-data regeneration failed; inspect the separate category records."

    report = {
        "schema_version": 3,
        "status": status,
        "integrity_categories": categories,
        "reader_facing_interpretation": reader_message,
        "failures": failures,
        "records": records,
        "scientific_scope": "Integrity and authoritative problem-data regeneration only; no Track-S stability conclusion.",
    }
    write_json(reports / "package_integrity_report.json", report)
    shutil.copy2(source_dir / "SHA256SUMS", reports / "source_archive_SHA256SUMS")
    print(json.dumps({"status": status, "failure_count": len(failures), "integrity_categories": categories}, indent=2))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
