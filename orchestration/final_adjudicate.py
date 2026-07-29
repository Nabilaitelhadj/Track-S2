#!/usr/bin/env python3
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text()) if path.exists() else None
    except Exception:
        return None


def find_one(root: Path, name: str) -> Path | None:
    values = list(root.rglob(name))
    return values[0] if values else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--retrieved", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-repo", type=Path)
    args = parser.parse_args()
    retrieved = args.retrieved.resolve()
    output = args.output.resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    integrity_path = find_one(retrieved, "package_integrity_report.json")
    center_path = find_one(retrieved, "center_stage_status.json")
    continuum_path = find_one(retrieved, "continuum_stage_status.json")
    integrity = read_json(integrity_path) if integrity_path else None
    center = read_json(center_path) if center_path else None
    continuum = read_json(continuum_path) if continuum_path else None

    if not integrity or integrity.get("status") not in {"PASS", "PACKAGE_INTEGRITY_PASS"}:
        status = "S-F. EXECUTION OR TOOLCHAIN FAILURE"
        categories = (integrity or {}).get("integrity_categories", {})
        if categories.get("authoritative_regeneration") == "PASS":
            interpretation = "Repository-package checksum verification failed; authoritative problem-data regeneration passed."
        else:
            interpretation = "Repository-package integrity and/or authoritative problem-data regeneration failed; inspect the separate integrity-category records."
        subtype = "PACKAGE_INTEGRITY_FAILURE"
    elif not center:
        status = "S-F. EXECUTION OR TOOLCHAIN FAILURE"
        interpretation = "The center-stage machine record is missing or malformed."
        subtype = "CENTER_STAGE_RECORD_MISSING"
    else:
        outcome = center.get("outcome")
        subtype = outcome
        if outcome == "CANDIDATE_PRODUCED":
            if continuum and continuum.get("scientific_status") in {
                "S-A. CONTINUUM COMMON-QUADRATIC CERTIFICATE FOUND",
                "S-B. CONTINUUM GRAPH-DEPENDENT CERTIFICATE FOUND",
            }:
                status = continuum["scientific_status"]
                interpretation = "A center contraction candidate and proof-grade continuum certificate were retrieved and recorded."
            else:
                status = "S-C. CENTER CERTIFICATE FOUND; CONTINUUM ROBUSTIFICATION OPEN"
                interpretation = "A numerically accepted rounded center contraction candidate was retrieved; proof-grade continuum robustification remains open or failed closed."
        elif outcome == "EXECUTED_NO_CANDIDATE":
            status = "S-D. NO CENTER CERTIFICATE FOUND; FEASIBILITY REMAINS OPEN"
            interpretation = "At least one approved primary solver completed both center searches without an accepted candidate; this is not a formal infeasibility theorem."
        elif outcome == "SCREENING_ONLY":
            status = "S-F. EXECUTION OR TOOLCHAIN FAILURE"
            interpretation = "Only screening solvers completed, so no primary scientific verdict exists."
        else:
            status = "S-F. EXECUTION OR TOOLCHAIN FAILURE"
            interpretation = "The approved execution stack was unavailable or a runtime error prevented a scientifically interpretable outcome."

    run_url = f"{args.server_url}/{args.repository}/actions/runs/{args.run_id}"
    record = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "final_trackS_status": status,
        "scientific_interpretation": interpretation,
        "subtype_or_center_outcome": subtype,
        "remote_execution_platform": "GitHub-hosted Actions runner (ubuntu-latest)",
        "repository": args.repository,
        "workflow_run_id": args.run_id,
        "workflow_run_attempt": args.run_attempt,
        "workflow_run_url": run_url,
        "integrity_record": str(integrity_path) if integrity_path else None,
        "integrity_categories": (integrity or {}).get("integrity_categories"),
        "center_record": str(center_path) if center_path else None,
        "continuum_record": str(continuum_path) if continuum_path else None,
        "prohibited_inferences": [
            "No certificate produced does not imply no certificate exists.",
            "A numerical solver infeasibility status is not a formal class-level infeasibility theorem without a verified dual certificate.",
            "Pointwise spectral radii below one do not prove uniform stability of admissible products.",
            "A failed norm-ball continuum robustification does not prove timing-family instability.",
            "Track B1 is outside this workflow and is not adjudicated here.",
        ],
    }
    (output / "final_trackS_execution_status.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")

    report = f"""# FINAL TRACK-S STATUS\n\n## {status}\n\n**Remote platform:** GitHub-hosted Actions (`ubuntu-latest`)  \n**Repository:** `{args.repository}`  \n**Run ID:** `{args.run_id}`  \n**Run attempt:** `{args.run_attempt}`  \n**Run URL:** {run_url}\n\n## Scientific interpretation\n\n{interpretation}\n\n## Retrieved machine records\n\n- Package integrity: `{integrity_path}`\n- Center stage: `{center_path}`\n- Continuum stage: `{continuum_path}`\n\n## Prohibited inferences\n\n- Toolchain or runtime failure does not imply SDP infeasibility.\n- No accepted candidate does not imply that no certificate exists.\n- Numerical infeasibility is not a formal impossibility result without a verified dual certificate.\n- Pointwise Schur stability does not prove uniform stability of products.\n- Failed continuum robustification does not prove instability.\n- Track B1 was not started.\n"""
    (output / "final_trackS_execution_report.md").write_text(report)

    artifact_copy = output / "retrieved_artifacts"
    shutil.copytree(retrieved, artifact_copy)
    if args.source_repo:
        source_repo = args.source_repo.resolve()
        supporting = output / "handoff_source"
        supporting.mkdir()
        for relative in ["source_archives", ".github/workflows/trackS_execute.yml", "REMOTE_EXECUTION_README.md", "orchestration"]:
            src = source_repo / relative
            if not src.exists():
                continue
            dst = supporting / relative
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
    files = sorted(path for path in output.rglob("*") if path.is_file())
    ledger = output / "SHA256SUMS"
    ledger.write_text("".join(f"{sha256(path)}  {path.relative_to(output).as_posix()}\n" for path in files))

    archive = output.parent / "trackS_final_remote_execution_bundle.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(output.parent))
    (output.parent / "trackS_final_remote_execution_bundle.zip.sha256").write_text(f"{sha256(archive)}  {archive.name}\n")
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
