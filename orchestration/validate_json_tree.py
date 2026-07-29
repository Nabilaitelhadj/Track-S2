#!/usr/bin/env python3
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    records = []
    failures = []
    for path in sorted(args.root.rglob("*.json")):
        try:
            value = json.loads(path.read_text())
            records.append({"path": str(path), "type": type(value).__name__})
        except Exception as exc:
            failures.append({"path": str(path), "error": repr(exc)})
    result = {
        "status": "PASS" if not failures else "FAIL",
        "json_files_checked": len(records) + len(failures),
        "valid_files": records,
        "failures": failures,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": result["status"], "json_files_checked": result["json_files_checked"], "failure_count": len(failures)}, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
