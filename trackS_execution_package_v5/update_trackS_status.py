#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--classification", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--center-outcome")
    parser.add_argument("--operational-exit-code", type=int)
    args = parser.parse_args()

    path = args.root / "final_trackS_execution_status.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    amendments = list(data.get("package_amendments", []))
    for item in [
        "status-specific center exit codes",
        "JSON-driven scientific classification",
        "separate scientific and operational statuses",
    ]:
        if item not in amendments:
            amendments.append(item)
    data.update({
        "status": args.classification,
        "reason": args.reason,
        "center_outcome": args.center_outcome,
        "operational_exit_code": args.operational_exit_code,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "package_amendments": amendments,
        "interpretation": (
            "Scientific Track-S status and operational shell status are separate. "
            "A nonzero exit may represent a valid S-D result, screening-only execution, "
            "toolchain unavailability, or a runtime error."
        ),
    })
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(json.dumps(data, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
