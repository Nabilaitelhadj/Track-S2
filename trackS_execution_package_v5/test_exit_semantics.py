#!/usr/bin/env python3
from __future__ import annotations

from run_center_sdps import (
    EXIT_CANDIDATE_PRODUCED,
    EXIT_EXECUTED_NO_CANDIDATE,
    EXIT_RUNTIME_ERROR,
    EXIT_SCREENING_ONLY,
    EXIT_TOOLCHAIN_UNAVAILABLE,
    _classify,
)


def run(solver: str, role: str, script: str, returncode: int | None):
    return {
        "solver": solver,
        "role": role,
        "script": script,
        "returncode": returncode,
        "stdout": "",
        "stderr": "",
    }


def check(name: str, installed, runs, expected_code, expected_outcome):
    result = _classify(installed, runs)
    assert result["exit_code"] == expected_code, (name, result)
    assert result["outcome"] == expected_outcome, (name, result)
    return {"name": name, "exit_code": expected_code, "outcome": expected_outcome}


def main():
    results = []
    results.append(check(
        "candidate produced",
        ["CLARABEL"],
        [
            run("CLARABEL", "primary", "common_sdp.py", 0),
            run("CLARABEL", "primary", "graph_sdp.py", 3),
        ],
        EXIT_CANDIDATE_PRODUCED,
        "CANDIDATE_PRODUCED",
    ))
    results.append(check(
        "valid primary execution no candidate",
        ["CLARABEL"],
        [
            run("CLARABEL", "primary", "common_sdp.py", 3),
            run("CLARABEL", "primary", "graph_sdp.py", 3),
        ],
        EXIT_EXECUTED_NO_CANDIDATE,
        "EXECUTED_NO_CANDIDATE",
    ))
    results.append(check(
        "screening only",
        ["SCS"],
        [
            run("SCS", "screening", "common_sdp.py", 3),
            run("SCS", "screening", "graph_sdp.py", 3),
        ],
        EXIT_SCREENING_ONLY,
        "SCREENING_ONLY",
    ))
    results.append(check(
        "no approved solver",
        ["OSQP"],
        [],
        EXIT_TOOLCHAIN_UNAVAILABLE,
        "TOOLCHAIN_UNAVAILABLE",
    ))
    results.append(check(
        "runtime error",
        ["CLARABEL"],
        [
            run("CLARABEL", "primary", "common_sdp.py", 1),
            run("CLARABEL", "primary", "graph_sdp.py", 3),
        ],
        EXIT_RUNTIME_ERROR,
        "RUNTIME_ERROR",
    ))
    print("status-specific exit-semantics tests passed")
    for item in results:
        print(item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
