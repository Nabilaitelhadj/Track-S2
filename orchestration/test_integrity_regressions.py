#!/usr/bin/env python3
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from deterministic_archive import create_archive, verify_archive
from deterministic_manifest import verify_manifest, write_manifest

TEXT_SUFFIXES = {".py", ".sh", ".md", ".txt", ".json", ".yml", ".yaml", ".csv", ".toml", ".jl", ".m"}


def run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"})


def result(name: str, passed: bool, detail: object = None) -> dict[str, object]:
    return {"name": name, "status": "PASS" if passed else "FAIL", "detail": detail}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    package = repo / "trackS_execution_package_v5"
    tests: list[dict[str, object]] = []

    removed_bytecode = []
    for path in sorted(repo.rglob("*"), key=lambda p: p.as_posix(), reverse=True):
        if path.is_file() and path.suffix.lower() in {".pyc", ".pyo"}:
            removed_bytecode.append(path.relative_to(repo).as_posix())
            path.unlink()
        elif path.is_dir() and path.name == "__pycache__":
            shutil.rmtree(path)

    # 1. Current committed-style manifests verify from raw bytes.
    repo_verify = verify_manifest(repo, repo / "repository_source_manifest.json", repo / "BUNDLE_SHA256SUMS")
    package_verify = verify_manifest(package, package / "trackS_package_manifest.json", package / "trackS_package_checksums.txt")
    tests.append(result("fresh_checkout_source_manifests", repo_verify["status"] == "PASS" and package_verify["status"] == "PASS", {"repository": repo_verify, "package": package_verify}))

    # 2. LF policy and canonical text bytes.
    attributes = (repo / ".gitattributes").read_text(encoding="utf-8")
    crlf_files = []
    for path in repo.rglob("*"):
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES and not any(part in {".git", "build", "artifacts"} for part in path.parts):
            if b"\r" in path.read_bytes():
                crlf_files.append(path.relative_to(repo).as_posix())
    tests.append(result("crlf_lf_normalization", "*.md   text eol=lf" in attributes and not crlf_files, {"files_with_cr": crlf_files}))

    # 3. No transient bytecode in source or immutable manifests.
    bytecode = [p.relative_to(repo).as_posix() for p in repo.rglob("*") if p.is_file() and (p.suffix in {".pyc", ".pyo"} or "__pycache__" in p.parts)]
    manifest_text = (package / "trackS_package_checksums.txt").read_text(encoding="utf-8")
    tests.append(result("no_bytecode_in_manifest", not bytecode and "__pycache__" not in manifest_text and ".pyc" not in manifest_text, {"bytecode": bytecode, "purged_before_check": removed_bytecode}))

    with tempfile.TemporaryDirectory(prefix="trackS-integrity-tests-") as tmp:
        tmp = Path(tmp)
        synthetic = tmp / "synthetic"
        synthetic.mkdir()
        (synthetic / "a.txt").write_text("alpha\n", encoding="utf-8", newline="\n")
        (synthetic / "b.json").write_text('{"b": 1}\n', encoding="utf-8", newline="\n")
        manifest_json = synthetic / "clean_package_manifest.json"
        manifest_text = synthetic / "clean_package_checksums.txt"
        write_manifest(synthetic, "clean_package", manifest_json, manifest_text)
        first_json, first_text = manifest_json.read_bytes(), manifest_text.read_bytes()
        manifest_json.unlink(); manifest_text.unlink()
        write_manifest(synthetic, "clean_package", manifest_json, manifest_text)
        deterministic = first_json == manifest_json.read_bytes() and first_text == manifest_text.read_bytes()
        tests.append(result("manifest_determinism", deterministic))
        manifest_data = json.loads(manifest_json.read_text())
        paths = {e["path"] for e in manifest_data["entries"]}
        tests.append(result("manifest_self_exclusion", "clean_package_manifest.json" not in paths and "clean_package_checksums.txt" not in paths))

        # 4. Unexpected untracked immutable file must invalidate verification.
        (synthetic / "unexpected.dat").write_bytes(b"unexpected")
        unexpected_check = verify_manifest(synthetic, manifest_json, manifest_text)
        tests.append(result("no_untracked_file_dependency", unexpected_check["status"] == "FAIL" and "unexpected.dat" in unexpected_check["unexpected"], unexpected_check))
        (synthetic / "unexpected.dat").unlink()

        # 5. Byte-for-byte mutation after ledger generation must be detected.
        (synthetic / "a.txt").write_text("mutated\n", encoding="utf-8", newline="\n")
        mutation_check = verify_manifest(synthetic, manifest_json, manifest_text)
        tests.append(result("post_ledger_mutation_detection", mutation_check["status"] == "FAIL" and any(x["path"] == "a.txt" for x in mutation_check["mismatches"]), mutation_check))

    # 6. Clean copy strips stale results and bytecode.
    with tempfile.TemporaryDirectory(prefix="trackS-clean-copy-") as tmp:
        clean = Path(tmp) / "trackS_execution_package_v5"
        report_path = Path(tmp) / "copy_report.json"
        proc = run([sys.executable, str(repo / "orchestration" / "prepare_clean_package.py"), "--source", str(package), "--destination", str(clean), "--report", str(report_path)], cwd=repo)
        stale = [p.relative_to(clean).as_posix() for p in clean.rglob("*") if p.is_file() and ("results" in p.parts or "logs" in p.parts or "__pycache__" in p.parts or p.suffix in {".pyc", ".pyo"})]
        tests.append(result("stale_result_isolation", proc.returncode == 0 and not stale, {"returncode": proc.returncode, "stale": stale, "stderr": proc.stderr}))
        tests.append(result("source_result_artifact_separation", (clean / "results").is_dir() and not any((clean / "results").iterdir()) and (clean / "logs").is_dir() and not any((clean / "logs").iterdir())))

        # 7. Generate ledger after a generated-file mutation, verify, then
        # prove another mutation invalidates it.
        generated = clean / "data" / "ordering_test.bin"
        generated.write_bytes(b"final-generated-content")
        manifest_json = clean / "clean_package_manifest.json"
        manifest_text = clean / "clean_package_checksums.txt"
        write_manifest(clean, "clean_package", manifest_json, manifest_text)
        ordering_initial = verify_manifest(clean, manifest_json, manifest_text)
        generated.write_bytes(b"changed-after-ledger")
        ordering_mutated = verify_manifest(clean, manifest_json, manifest_text)
        tests.append(result("generated_file_ordering", ordering_initial["status"] == "PASS" and ordering_mutated["status"] == "FAIL", {"before": ordering_initial, "after": ordering_mutated}))
        generated.write_bytes(b"final-generated-content")
        write_manifest(clean, "clean_package", manifest_json, manifest_text)

        # 8. Deterministic ZIP extraction and internal verification.
        archive = Path(tmp) / "clean.zip"
        create_archive(clean, archive, "trackS_execution_package_v5")
        zip_result = verify_archive(archive, "trackS_execution_package_v5", "clean_package_manifest.json", "clean_package_checksums.txt")
        with zipfile.ZipFile(archive) as zf:
            zip_test = zf.testzip()
        tests.append(result("zip_extraction_independent_reverification", zip_result["status"] == "PASS" and zip_test is None, zip_result))

    # 9. Fresh-copy authoritative regeneration contract.
    with tempfile.TemporaryDirectory(prefix="trackS-regeneration-") as tmp:
        clean = Path(tmp) / "trackS_execution_package_v5"
        shutil.copytree(package, clean, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "results", "logs"))
        (clean / "results").mkdir(exist_ok=True)
        p1 = run([sys.executable, "build_edge_conditioned_data.py"], cwd=clean)
        p2 = run([sys.executable, "verify_problem_data.py"], cwd=clean)
        verification_path = clean / "problem_data_verification.json"
        verification = json.loads(verification_path.read_text()) if verification_path.exists() else {}
        regen_ok = p1.returncode == 0 and p2.returncode == 0 and verification.get("status") == "AUTHORITATIVE_REGENERATED_PROBLEM_VERIFIED" and verification.get("cell_count") == 13 and verification.get("edge_count") == 45 and verification.get("state_dimension") == 35
        tests.append(result("clean_checkout_authoritative_map_regeneration", regen_ok, {"build_rc": p1.returncode, "verify_rc": p2.returncode, "verification": verification}))

    # 10. The exact NPZ/MAT container mechanisms used by authoritative
    # regeneration are deterministic without rerunning the expensive geometry
    # construction twice.  The full builder itself is exercised by the fresh
    # regeneration test above and again by the outer integrity stage.
    with tempfile.TemporaryDirectory(prefix="trackS-deterministic-containers-") as tmp:
        from importlib.util import module_from_spec, spec_from_file_location
        import hashlib
        import numpy as np
        from scipy.io import savemat, loadmat

        module_spec = spec_from_file_location("trackS_builder", package / "build_edge_conditioned_data.py")
        assert module_spec and module_spec.loader
        builder = module_from_spec(module_spec)
        module_spec.loader.exec_module(builder)
        tmp = Path(tmp)
        hashes = []
        arrays = {"F": np.arange(24, dtype=float).reshape(2, 3, 4), "edges": np.asarray([[0, 1], [1, 0]], dtype=int)}
        for index in range(2):
            npz = tmp / f"sample-{index}.npz"
            mat = tmp / f"sample-{index}.mat"
            np.savez_compressed(npz, **arrays)
            savemat(mat, arrays, do_compression=True)
            builder.canonicalize_mat_v5_header(mat)
            hashes.append({
                "npz": hashlib.sha256(npz.read_bytes()).hexdigest(),
                "mat": hashlib.sha256(mat.read_bytes()).hexdigest(),
            })
            loaded = loadmat(mat)
            assert np.array_equal(loaded["F"], arrays["F"])
            assert np.array_equal(loaded["edges"], arrays["edges"])
        tests.append(result("deterministic_authoritative_serialization", hashes[0] == hashes[1], {"hashes": hashes}))

    # 11. Existing scientific exit semantics remain unchanged.
    exit_test = run([sys.executable, "test_exit_semantics.py"], cwd=package)
    tests.append(result("workflow_json_process_exit_consistency", exit_test.returncode == 0, {"returncode": exit_test.returncode, "stdout": exit_test.stdout, "stderr": exit_test.stderr}))

    # 12. Gitignore includes required transient patterns.
    gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
    required_patterns = ["__pycache__/", "*.py[cod]", ".venv/", "venv/", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/", "build/", "dist/", "results/", "artifacts/", "*.log"]
    tests.append(result("transient_ignore_policy", all(pattern in gitignore for pattern in required_patterns), {"missing": [p for p in required_patterns if p not in gitignore]}))

    # 13. Workflow YAML, job dependencies, action versions, and artifact
    # contracts must remain valid after any repository update.
    with tempfile.TemporaryDirectory(prefix="trackS-workflow-validation-") as tmp:
        workflow_report = Path(tmp) / "workflow.json"
        workflow_test = run([
            sys.executable,
            str(repo / "orchestration" / "validate_workflow.py"),
            str(repo / ".github" / "workflows" / "trackS_execute.yml"),
            "--repo-root", str(repo),
            "--report", str(workflow_report),
        ], cwd=repo)
        workflow_data = json.loads(workflow_report.read_text()) if workflow_report.exists() else {}
        tests.append(result(
            "workflow_yaml_and_contract_validation",
            workflow_test.returncode == 0 and workflow_data.get("status") == "PASS",
            {"returncode": workflow_test.returncode, "report": workflow_data, "stderr": workflow_test.stderr},
        ))

    # 14. Bundle metadata must match the workflow, package ledgers, source
    # archives, and archive sidecars without a checksum cycle.
    with tempfile.TemporaryDirectory(prefix="trackS-bundle-validation-") as tmp:
        bundle_report = Path(tmp) / "bundle.json"
        bundle_test = run([
            sys.executable,
            str(repo / "orchestration" / "validate_bundle_metadata.py"),
            "--repo-root", str(repo),
            "--report", str(bundle_report),
        ], cwd=repo)
        bundle_data = json.loads(bundle_report.read_text()) if bundle_report.exists() else {}
        tests.append(result(
            "bundle_metadata_chain_validation",
            bundle_test.returncode == 0 and bundle_data.get("status") == "PASS",
            {"returncode": bundle_test.returncode, "report": bundle_data, "stderr": bundle_test.stderr},
        ))

    failed = [test for test in tests if test["status"] != "PASS"]
    report = {"schema_version": 1, "status": "PASS" if not failed else "FAIL", "test_count": len(tests), "passed_count": len(tests) - len(failed), "failed_count": len(failed), "tests": tests}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"status": report["status"], "test_count": report["test_count"], "failed_count": report["failed_count"]}, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
