#!/usr/bin/env python3
"""Strict static validation for the Track-S GitHub Actions workflow."""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

EXPECTED_JOBS = {"integrity", "center_sdps", "continuum", "final_adjudication"}
EXPECTED_ACTIONS = {
    "actions/checkout@v7",
    "actions/setup-python@v7",
    "julia-actions/setup-julia@v3",
    "actions/upload-artifact@v7",
    "actions/download-artifact@v8",
}
EXPECTED_NEEDS = {
    "center_sdps": {"integrity"},
    "continuum": {"center_sdps"},
    "final_adjudication": {"integrity", "center_sdps", "continuum"},
}
REQUIRED_PATHS = [
    "trackS_execution_package_v5",
    "source_archives/SHA256SUMS",
    "orchestration/requirements-integrity.txt",
    "orchestration/prepare_clean_package.py",
    "orchestration/run_center_stage.py",
    "orchestration/run_continuum_stage.py",
    "orchestration/final_adjudicate.py",
    "orchestration/validate_bundle_metadata.py",
]


class UniqueKeyBaseLoader(yaml.BaseLoader):
    """BaseLoader variant that rejects duplicate mapping keys."""


def _construct_unique_mapping(loader: UniqueKeyBaseLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyBaseLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _needs_set(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {str(item) for item in value}
    return set()


def _steps(job: object) -> list[dict[str, Any]]:
    if not isinstance(job, dict):
        return []
    raw = job.get("steps", [])
    if not isinstance(raw, list):
        return []
    return [step for step in raw if isinstance(step, dict)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workflow", type=Path)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    repo = args.repo_root.resolve()
    workflow = args.workflow.resolve()
    failures: list[str] = []
    text = workflow.read_text(encoding="utf-8")

    try:
        data = yaml.load(text, Loader=UniqueKeyBaseLoader)
    except Exception as exc:
        data = None
        failures.append(f"YAML parse failed: {exc}")

    jobs: dict[str, Any] = {}
    if not isinstance(data, dict):
        failures.append("workflow is not a YAML mapping")
    else:
        trigger = data.get("on")
        if not isinstance(trigger, dict) or "workflow_dispatch" not in trigger:
            failures.append("workflow_dispatch trigger missing")
        permissions = data.get("permissions")
        if not isinstance(permissions, dict) or permissions.get("contents") != "read":
            failures.append("least-privilege contents: read permission missing")
        raw_jobs = data.get("jobs", {})
        if isinstance(raw_jobs, dict):
            jobs = raw_jobs
        else:
            failures.append("jobs is not a mapping")

    missing_jobs = EXPECTED_JOBS.difference(jobs)
    unexpected_jobs = set(jobs).difference(EXPECTED_JOBS)
    if missing_jobs:
        failures.append(f"missing jobs: {sorted(missing_jobs)}")
    if unexpected_jobs:
        failures.append(f"unexpected jobs: {sorted(unexpected_jobs)}")

    for job_name, expected in EXPECTED_NEEDS.items():
        actual = _needs_set(jobs.get(job_name, {}).get("needs") if isinstance(jobs.get(job_name), dict) else None)
        if actual != expected:
            failures.append(f"job {job_name!r} needs {sorted(actual)}, expected {sorted(expected)}")

    final_job = jobs.get("final_adjudication", {})
    if isinstance(final_job, dict) and final_job.get("if") != "always()":
        failures.append("final_adjudication must use if: always()")

    continuum_job = jobs.get("continuum", {})
    continuum_if = continuum_job.get("if") if isinstance(continuum_job, dict) else None
    if continuum_if != "needs.center_sdps.outputs.outcome == 'CANDIDATE_PRODUCED'":
        failures.append("continuum job condition is not the validated candidate-only condition")

    action_refs = set(re.findall(r"uses:\s*([^\s#]+)", text))
    missing_actions = EXPECTED_ACTIONS.difference(action_refs)
    if missing_actions:
        failures.append(f"missing expected action references: {sorted(missing_actions)}")
    legacy_refs = sorted(
        ref for ref in action_refs
        if ref.startswith("actions/") and ref not in EXPECTED_ACTIONS
    )
    if legacy_refs:
        failures.append(f"unexpected or legacy first-party action references: {legacy_refs}")

    upload_steps = [
        step
        for job in jobs.values()
        for step in _steps(job)
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    if len(upload_steps) != 4:
        failures.append(f"expected 4 artifact upload steps, found {len(upload_steps)}")
    for step in upload_steps:
        with_block = step.get("with", {})
        if not isinstance(with_block, dict) or with_block.get("if-no-files-found") != "error":
            failures.append(f"artifact upload is not fail-closed: {step.get('name', '<unnamed>')}")

    required_snippets = [
        "center_status.py --summary",
        "0|10|11|20|21",
        "sha256sum --check trackS_final_remote_execution_bundle.zip.sha256",
        "python orchestration/run_integrity_stage.py",
        "actions/download-artifact@v8",
    ]
    for snippet in required_snippets:
        if snippet not in text:
            failures.append(f"required workflow contract missing: {snippet}")

    forbidden_snippets = [
        "Preserve center operational exit semantics",
        'exit "$(cat build/center_work/center_operational_exit_code.txt)"',
        "if-no-files-found: warn",
    ]
    for snippet in forbidden_snippets:
        if snippet in text:
            failures.append(f"forbidden workflow pattern present: {snippet}")

    if text.count("${{") != text.count("}}"):
        failures.append("unbalanced GitHub expression delimiters")

    shell_results: list[dict[str, Any]] = []
    for job_name, job in jobs.items():
        for index, step in enumerate(_steps(job), start=1):
            script = step.get("run")
            if not isinstance(script, str):
                continue
            # GitHub expressions are expanded before the selected shell runs.
            # Replace them with a plain token so Bash can validate the remaining
            # script, including heredocs and multiline control structures.
            normalized = re.sub(r"\$\{\{.*?\}\}", "GITHUB_EXPRESSION", script)
            process = subprocess.run(
                ["bash", "-n"],
                input=normalized,
                text=True,
                capture_output=True,
            )
            record = {
                "job": job_name,
                "step": step.get("name", f"step-{index}"),
                "returncode": process.returncode,
                "stderr": process.stderr,
            }
            shell_results.append(record)
            if process.returncode != 0:
                failures.append(
                    f"Bash syntax failed in {job_name}/{record['step']}: {process.stderr.strip()}"
                )

    missing_paths = [path for path in REQUIRED_PATHS if not (repo / path).exists()]
    if missing_paths:
        failures.append(f"missing repository paths: {missing_paths}")

    try:
        workflow_display = workflow.relative_to(repo).as_posix()
    except ValueError:
        workflow_display = workflow.as_posix()

    result = {
        "schema_version": 2,
        "status": "PASS" if not failures else "FAIL",
        "workflow": workflow_display,
        "jobs": sorted(jobs),
        "action_references": sorted(action_refs),
        "artifact_upload_count": len(upload_steps),
        "shell_run_block_count": len(shell_results),
        "shell_run_blocks": shell_results,
        "failures": failures,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
