#!/usr/bin/env bash
# DeepSeek KP 后处理（断点续传 + 终端实时进度）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/src"
source "$ROOT/.venv/bin/activate"
# -u: 无缓冲 stdout，进度行即时显示在 Cursor Terminal
exec python -u deepseek_kp_pipeline.py --resume --workers 6 --batch-size 6 "$@"
