#!/usr/bin/env python3
"""Validate and expose the JSON-defined center-stage scientific status.

The shell return code is an operational signal.  The scientific Track-S
classification is read from center_sdp_run_summary.json and is never inferred
from the Boolean distinction zero/nonzero.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

VALID_CODES = {0, 10, 11, 20, 21}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--observed-return", type=int)
    parser.add_argument("--field", choices=["json", "shell", "outcome", "classification", "exit_code"], default="json")
    args = parser.parse_args()

    data = json.loads(args.summary.read_text())
    exit_code = int(data["exit_code"])
    if exit_code not in VALID_CODES:
        raise SystemExit(f"Invalid status-specific exit code in summary: {exit_code}")
    if args.observed_return is not None and args.observed_return != exit_code:
        raise SystemExit(
            f"Shell/JSON center-status mismatch: observed={args.observed_return}, json={exit_code}"
        )

    if args.field == "shell":
        print(f"{exit_code}\t{data['outcome']}\t{data['trackS_classification']}")
    elif args.field == "outcome":
        print(data["outcome"])
    elif args.field == "classification":
        print(data["trackS_classification"])
    elif args.field == "exit_code":
        print(exit_code)
    else:
        print(json.dumps(data, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
