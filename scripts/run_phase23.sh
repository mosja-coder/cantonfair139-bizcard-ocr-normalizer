#!/usr/bin/env bash
# 兼容旧入口，等同 run_ocr.sh
exec "$(dirname "$0")/run_ocr.sh" "$@"
