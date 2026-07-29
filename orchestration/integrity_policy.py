#!/usr/bin/env python3
"""Shared deterministic-integrity policy for the Track-S repository.

This module deliberately separates immutable source inputs from generated
scientific inputs and run results.  It contains no model or SDP equations.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

from pathlib import PurePosixPath

POLICY_VERSION = "trackS-integrity-v2"

TRANSIENT_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "dist",
    "artifacts",
}
TRANSIENT_SUFFIXES = {".pyc", ".pyo", ".log"}

# Generated run records are intentionally not immutable scientific inputs.
PACKAGE_RESULT_DIRS = {"results", "logs"}
PACKAGE_GENERATED_FILES = {
    "clean_package_checksums.txt",
    "clean_package_manifest.json",
    "execution_preflight.json",
    "environment_lock_status.json",
    "final_trackS_execution_report.md",
    "final_trackS_execution_status.json",
    "package_integrity_report.json",
    "patch_verification_results.json",
    "problem_data_verification.json",
    "status_exit_semantics_test_results.json",
    "trackS_final_report.md",
    "trackS_preflight.json",
}

REPOSITORY_MANIFEST_FILES = {
    "BUNDLE_SHA256SUMS",
    "repository_source_manifest.json",
}
PACKAGE_MANIFEST_FILES = {
    "trackS_package_checksums.txt",
    "trackS_package_manifest.json",
}
CLEAN_MANIFEST_FILES = {
    "clean_package_checksums.txt",
    "clean_package_manifest.json",
}

# Source archives have their own outer SHA-256 ledger and are kept separate
# from the repository source-tree manifest.
SOURCE_ARCHIVE_BINARY_PREFIX = "source_archives/"


def _parts(relative_path: str) -> tuple[str, ...]:
    return PurePosixPath(relative_path).parts


def is_transient(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    if any(part in TRANSIENT_DIRS for part in path.parts):
        return True
    return path.suffix.lower() in TRANSIENT_SUFFIXES


def should_exclude(relative_path: str, scope: str) -> bool:
    """Return True when a path is outside the immutable scope.

    Scopes:
      repository     repository source-tree ledger;
      package_source active Track-S source-package ledger;
      clean_package  post-regeneration execution package ledger;
      result         generated result artifact ledger.
    """
    rel = PurePosixPath(relative_path).as_posix()
    parts = _parts(rel)
    if is_transient(rel):
        return True

    if scope == "repository":
        if rel in REPOSITORY_MANIFEST_FILES:
            return True
        if rel.startswith(SOURCE_ARCHIVE_BINARY_PREFIX) and (
            rel.endswith(".zip") or rel.endswith(".zip.sha256")
        ):
            return True
        # Result artifacts are never repository source inputs.
        if any(part in PACKAGE_RESULT_DIRS for part in parts):
            return True
        if rel.startswith("trackS_execution_package_v5/"):
            package_rel = rel.removeprefix("trackS_execution_package_v5/")
            if should_exclude(package_rel, "package_source"):
                return True
        return False

    if scope == "package_source":
        if rel in PACKAGE_MANIFEST_FILES or rel in CLEAN_MANIFEST_FILES:
            return True
        if any(part in PACKAGE_RESULT_DIRS for part in parts):
            return True
        if len(parts) == 1 and rel in PACKAGE_GENERATED_FILES:
            return True
        return False

    if scope == "clean_package":
        if rel in CLEAN_MANIFEST_FILES or rel in PACKAGE_MANIFEST_FILES:
            return True
        if any(part in PACKAGE_RESULT_DIRS for part in parts):
            return True
        if len(parts) == 1 and rel in PACKAGE_GENERATED_FILES:
            return True
        return False

    if scope == "result":
        return rel in {"SHA256SUMS", "result_manifest.json"}

    raise ValueError(f"unknown manifest scope: {scope}")


def policy_description(scope: str) -> dict[str, object]:
    return {
        "policy_version": POLICY_VERSION,
        "scope": scope,
        "transient_directories": sorted(TRANSIENT_DIRS),
        "transient_suffixes": sorted(TRANSIENT_SUFFIXES),
        "package_result_directories": sorted(PACKAGE_RESULT_DIRS),
        "package_generated_files": sorted(PACKAGE_GENERATED_FILES),
        "raw_byte_hashing": True,
        "relative_path_format": "sorted POSIX",
        "manifest_self_exclusion": True,
    }
