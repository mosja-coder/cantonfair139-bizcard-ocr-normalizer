#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/src"
source "$ROOT/.venv/bin/activate"
exec python -u compare_ab_pipeline.py "$@"
