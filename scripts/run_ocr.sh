#!/usr/bin/env bash
# 批量 OCR 链路 A：data/input 下全部 PDF，结果写入 data/output
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/src"
source "$ROOT/.venv/bin/activate"
exec python run_pipeline.py --data-dir "../data" --resume "$@"
