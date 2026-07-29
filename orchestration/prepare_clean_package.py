#!/usr/bin/env python3
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
import json
import shutil
from pathlib import Path

from integrity_policy import should_exclude


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    source = args.source.resolve()
    destination = args.destination.resolve()
    if not source.is_dir():
        raise SystemExit(f"source package does not exist: {source}")
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    copied: list[str] = []
    removed: list[str] = []
    for path in sorted(source.rglob("*"), key=lambda p: p.relative_to(source).as_posix()):
        rel = path.relative_to(source).as_posix()
        if path.is_symlink():
            raise SystemExit(f"symbolic links are not allowed in the clean package: {rel}")
        if path.is_dir():
            continue
        if should_exclude(rel, "package_source"):
            removed.append(rel)
            continue
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append(rel)

    # Run outputs are created only after the immutable clean-package manifest.
    (destination / "results").mkdir(exist_ok=True)
    (destination / "logs").mkdir(exist_ok=True)

    transient = []
    for path in destination.rglob("*"):
        if path.is_file():
            rel = path.relative_to(destination).as_posix()
            if "__pycache__" in path.parts or path.suffix.lower() in {".pyc", ".pyo"}:
                transient.append(rel)
    if transient:
        raise SystemExit(f"clean package contains transient bytecode: {transient}")

    report = {
        "status": "CLEAN_EXECUTION_TREE_CREATED_UNFINALIZED",
        "source": str(source),
        "destination": str(destination),
        "copied_file_count": len(copied),
        "removed_file_count": len(removed),
        "removed_entries": removed,
        "manifest_generated": False,
        "note": "Authoritative maps must be regenerated before the clean-package manifest is generated.",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
