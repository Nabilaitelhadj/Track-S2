#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
python build_edge_conditioned_data.py
julia --project=julia julia/timing_family_enclosure.jl "$ROOT"
python timing_family_enclosure.py --skip-run
