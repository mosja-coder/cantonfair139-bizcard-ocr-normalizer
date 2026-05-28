#!/usr/bin/env bash
# 打包可上传 GitHub 的脱敏源码（不含 PDF/xlsx/缓存/密钥）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/_release/cantonfair139-bizcard-ocr-normalizer}"

rm -rf "$OUT"
mkdir -p "$OUT"

copy() {
  local src="$1" dst="$2"
  mkdir -p "$(dirname "$OUT/$dst")"
  cp "$src" "$OUT/$dst"
}

for f in README.md requirements.txt .env.example .gitignore; do
  copy "$ROOT/$f" "$f"
done

for f in scripts/*.sh; do
  copy "$f" "scripts/$(basename "$f")"
done

for f in src/*.py; do
  copy "$f" "src/$(basename "$f")"
done

mkdir -p "$OUT/data/input" "$OUT/data/output" "$OUT/data/deliverables" "$OUT/data/cache/deepseek"
touch "$OUT/data/input/.gitkeep" "$OUT/data/output/.gitkeep" "$OUT/data/deliverables/.gitkeep" "$OUT/data/cache/deepseek/.gitkeep"

cat > "$OUT/data/README.md" <<'EOF'
# Data layout

Place scanned PDFs in `input/`. Pipeline writes to `output/` and `cache/deepseek/`.
Final Excel exports go to `deliverables/`.

These directories are empty in the repository; large or sensitive artifacts stay local.
EOF

# 项目名与脱敏 README 标题
sed -i '' '1s/.*/# cantonfair139-bizcard-ocr-normalizer/' "$OUT/README.md" 2>/dev/null || \
  sed -i '1s/.*/# cantonfair139-bizcard-ocr-normalizer/' "$OUT/README.md"
sed -i '' 's|card_139届广交会名片/|cantonfair139-bizcard-ocr-normalizer/|g' "$OUT/README.md" 2>/dev/null || \
  sed -i 's|card_139届广交会名片/|cantonfair139-bizcard-ocr-normalizer/|g' "$OUT/README.md"

# 剔除可能误拷的敏感文件
find "$OUT" -name '.env' -delete
find "$OUT" -name '*.pdf' -delete
find "$OUT" -name '*.xlsx' -delete
find "$OUT" -name '.DS_Store' -delete

echo "Release tree: $OUT"
find "$OUT" -type f | wc -l | xargs echo "files:"
