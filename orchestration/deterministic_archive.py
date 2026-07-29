#!/usr/bin/env python3
"""Create and verify deterministic ZIP archives using stored entries."""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

from deterministic_manifest import verify_manifest

FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def create_archive(source: Path, archive: Path, archive_root: str | None = None) -> None:
    source = source.resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        archive.unlink()
    prefix = Path(archive_root) if archive_root else Path()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for path in sorted((p for p in source.rglob("*") if p.is_file()), key=lambda p: p.relative_to(source).as_posix()):
            rel = prefix / path.relative_to(source)
            info = zipfile.ZipInfo(rel.as_posix(), date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (0o100644 & 0xFFFF) << 16
            zf.writestr(info, path.read_bytes())


def verify_archive(archive: Path, root_inside_archive: str, manifest_json: str, manifest_text: str) -> dict[str, object]:
    with zipfile.ZipFile(archive) as zf:
        corrupt = zf.testzip()
        if corrupt:
            return {"status": "FAIL", "zip_error": f"corrupt member: {corrupt}"}
        with tempfile.TemporaryDirectory(prefix="trackS-archive-verify-") as tmp:
            zf.extractall(tmp)
            root = Path(tmp) / root_inside_archive
            result = verify_manifest(root, root / manifest_json, root / manifest_text)
            return {"status": result["status"], "zip_error": None, "manifest_verification": result}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--source", type=Path, required=True)
    create.add_argument("--archive", type=Path, required=True)
    create.add_argument("--archive-root")
    verify = sub.add_parser("verify")
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--root-inside-archive", required=True)
    verify.add_argument("--manifest-json", required=True)
    verify.add_argument("--manifest-text", required=True)
    args = parser.parse_args()
    if args.command == "create":
        create_archive(args.source, args.archive, args.archive_root)
        print(json.dumps({"status": "CREATED", "archive": str(args.archive), "sha256": sha256(args.archive)}, indent=2))
        return 0
    result = verify_archive(args.archive, args.root_inside_archive, args.manifest_json, args.manifest_text)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
