#!/usr/bin/env python3
"""Generate and verify deterministic SHA-256 manifests.

The JSON manifest is authoritative; the text ledger is a human-readable
companion.  Both are generated without timestamps so two clean worktrees
produce byte-identical manifests.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable

from integrity_policy import POLICY_VERSION, policy_description, should_exclude

SCHEMA_VERSION = 1


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_files(root: Path, scope: str) -> list[Path]:
    root = root.resolve()
    files: list[Path] = []
    seen: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"symbolic links are not permitted in immutable manifests: {path}")
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if should_exclude(rel, scope):
            continue
        if rel in seen:
            raise ValueError(f"duplicate relative path: {rel}")
        seen.add(rel)
        files.append(path)
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def build_manifest(root: Path, scope: str) -> dict[str, object]:
    root = root.resolve()
    entries = []
    for path in collect_files(root, scope):
        rel = path.relative_to(root).as_posix()
        entries.append({"path": rel, "sha256": sha256(path), "size": path.stat().st_size})
    return {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "scope": scope,
        "entry_count": len(entries),
        "entries": entries,
        "policy": policy_description(scope),
    }


def render_text(manifest: dict[str, object]) -> str:
    entries = manifest["entries"]
    assert isinstance(entries, list)
    return "".join(
        f"{entry['sha256']}  {entry['path']}\n" for entry in entries  # type: ignore[index]
    )


def write_manifest(root: Path, scope: str, json_path: Path, text_path: Path) -> dict[str, object]:
    manifest = build_manifest(root, scope)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    text_path.write_text(render_text(manifest), encoding="utf-8", newline="\n")
    return manifest


def verify_manifest(root: Path, json_path: Path, text_path: Path | None = None) -> dict[str, object]:
    root = root.resolve()
    expected = json.loads(json_path.read_text(encoding="utf-8"))
    scope = expected.get("scope")
    if expected.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported manifest schema")
    if expected.get("policy_version") != POLICY_VERSION:
        raise ValueError("manifest policy version mismatch")
    if not isinstance(scope, str):
        raise ValueError("manifest scope missing")
    actual = build_manifest(root, scope)
    expected_entries = {entry["path"]: entry for entry in expected["entries"]}
    actual_entries = {entry["path"]: entry for entry in actual["entries"]}
    missing = sorted(set(expected_entries) - set(actual_entries))
    unexpected = sorted(set(actual_entries) - set(expected_entries))
    mismatches = []
    for rel in sorted(set(expected_entries) & set(actual_entries)):
        e, a = expected_entries[rel], actual_entries[rel]
        if e["sha256"] != a["sha256"] or e["size"] != a["size"]:
            mismatches.append({"path": rel, "expected": e, "actual": a})
    text_matches = None
    if text_path is not None:
        text_matches = text_path.read_text(encoding="utf-8") == render_text(expected)
    passed = not missing and not unexpected and not mismatches and text_matches is not False
    return {
        "status": "PASS" if passed else "FAIL",
        "scope": scope,
        "expected_count": len(expected_entries),
        "actual_count": len(actual_entries),
        "missing": missing,
        "unexpected": unexpected,
        "mismatches": mismatches,
        "text_ledger_matches_json": text_matches,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--root", type=Path, required=True)
    gen.add_argument("--scope", required=True, choices=["repository", "package_source", "clean_package", "result"])
    gen.add_argument("--json", type=Path, required=True)
    gen.add_argument("--text", type=Path, required=True)
    ver = sub.add_parser("verify")
    ver.add_argument("--root", type=Path, required=True)
    ver.add_argument("--json", type=Path, required=True)
    ver.add_argument("--text", type=Path)
    args = parser.parse_args()
    if args.command == "generate":
        manifest = write_manifest(args.root, args.scope, args.json, args.text)
        print(json.dumps({"status": "GENERATED", "scope": args.scope, "entry_count": manifest["entry_count"]}, indent=2))
        return 0
    result = verify_manifest(args.root, args.json, args.text)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
