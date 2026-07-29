#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import platform
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODULES = ["numpy", "scipy", "cvxpy", "clarabel", "cvxopt", "scs", "mosek", "coptpy", "mpmath", "h5py"]
EXECUTABLES = ["python", "julia", "matlab", "octave", "docker", "podman", "sdpa", "mosek", "csdp"]
PRIMARY = ["MOSEK", "SDPA", "CLARABEL", "CVXOPT", "COPT"]
SCREENING = ["SCS"]


def module_record(name: str):
    spec = importlib.util.find_spec(name)
    if spec is None:
        return {"available": False}
    try:
        module = __import__(name)
        record = {
            "available": True,
            "version": getattr(module, "__version__", None),
            "path": getattr(module, "__file__", None),
        }
        if name == "cvxpy":
            record["installed_solvers"] = list(module.installed_solvers())
        return record
    except Exception as exc:
        return {"available": True, "import_error": repr(exc)}


def main():
    modules = {name: module_record(name) for name in MODULES}
    executables = {name: shutil.which(name) for name in EXECUTABLES}
    installed = []
    if modules.get("cvxpy", {}).get("available") and not modules["cvxpy"].get("import_error"):
        installed = modules["cvxpy"].get("installed_solvers", [])
    primary = [solver for solver in PRIMARY if solver in installed]
    screening = [solver for solver in SCREENING if solver in installed]
    if primary:
        status = "PRIMARY_LOCAL_SDP_TOOLCHAIN_AVAILABLE"
    elif screening:
        status = "SCREENING_ONLY_LOCAL_SDP_TOOLCHAIN"
    else:
        status = "NO_LOCAL_SDP_SOLVER_EXTERNAL_EXECUTION_REQUIRED"
    result = {
        "python_version": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "modules": modules,
        "executables": executables,
        "primary_local_sdp_solvers": primary,
        "screening_local_sdp_solvers": screening,
        "proof_grade_julia_available": bool(executables.get("julia")),
        "status": status,
        "model_scope": "frozen rounded canonical A1 linearized hybrid model; physical component tolerances are outside Track S",
        "notes": [
            "CLARABEL is an approved primary SDP candidate generator; final acceptance still requires independent rigorous verification.",
            "SCS is screening only and never establishes a final certificate in this package.",
            "A failed or unavailable solver is not an infeasibility certificate.",
            "One primary solver plus proof-grade verification is sufficient for a feasible certificate; a second solver is recommended for replication.",
            "High-precision mpmath checks are diagnostic; final PSD verification uses interval/verified eigenvalue bounds.",
        ],
    }
    (ROOT / "trackS_preflight.json").write_text(json.dumps(result, indent=2, sort_keys=True))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
