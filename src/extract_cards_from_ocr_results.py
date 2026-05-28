#!/usr/bin/env python3
"""
Post-process Volcengine OCR page-level results into card-level structured contacts.

Input:
  - ocr_results.jsonl (one page per line, each line has line_texts + raw.data.line_rects)

Output:
  - ocr_card_results.jsonl
  - ocr_card_contacts.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from statistics import median
from typing import Dict, List, Tuple

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(
    r"(?:\+?\d{1,4}[-\s]?)?(?:\(?\d{2,4}\)?[-\s]?)?\d{3,4}[-\s]?\d{4}(?:[-\s]?\d{1,4})?"
)
WEBSITE_RE = re.compile(r"(?:https?://|www\.)[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(?:/[^\s]*)?")
WECHAT_RE = re.compile(r"(?:微信|wechat|WeChat|WX|vx)[:：\s]*([A-Za-z0-9_\-]{5,})")
QQ_RE = re.compile(r"(?:QQ|qq)[:：\s]*([1-9]\d{4,11})")

TITLE_KEYWORDS = [
    "总监",
    "经理",
    "主管",
    "总经理",
    "董事长",
    "CEO",
    "COO",
    "CTO",
    "CFO",
    "VP",
    "销售",
    "商务",
    "工程师",
    "顾问",
    "主任",
    "创始人",
    "director",
    "manager",
    "president",
    "owner",
]
COMPANY_KEYWORDS = [
    "公司",
    "集团",
    "有限公司",
    "研究院",
    "工作室",
    "事务所",
    "厂",
    "Inc",
    "INC",
    "Ltd",
    "LTD",
    "LLC",
    "Corporation",
    "Corp",
    "CO.,",
    "CO.,LTD",
]
ADDRESS_KEYWORDS = ["省", "市", "区", "县", "路", "街", "号", "大厦", "室", "楼", "园区", "镇", "ADD", "Address"]


def normalize_phone_candidates(lines: List[str]) -> List[str]:
    phones: List[str] = []
    for line in lines:
        for m in PHONE_RE.findall(line):
            cleaned = re.sub(r"[^\d+]", "", m)
            if len(cleaned.replace("+", "")) >= 7:
                phones.append(cleaned)
    return sorted(set(phones))


def first_match_by_keywords(lines: List[str], keywords: List[str]) -> str | None:
    for line in lines:
        lower = line.lower()
        if any(k.lower() in lower for k in keywords):
            return line
    return None


def guess_name(lines: List[str]) -> str | None:
    for line in lines:
        txt = line.strip()
        if not txt:
            continue
        lower = txt.lower()
        if any(k.lower() in lower for k in COMPANY_KEYWORDS + TITLE_KEYWORDS + ADDRESS_KEYWORDS):
            continue
        if EMAIL_RE.search(txt) or WEBSITE_RE.search(txt):
            continue
        if re.search(r"\d", txt):
            continue
        if 2 <= len(txt) <= 30:
            return txt
    return None


def extract_fields(lines: List[str]) -> Dict[str, object]:
    merged = " | ".join(lines)
    emails = sorted(set(EMAIL_RE.findall(merged)))
    websites = sorted(set(WEBSITE_RE.findall(merged)))
    wechat_ids = sorted(set(m.group(1) for m in WECHAT_RE.finditer(merged)))
    qq_ids = sorted(set(m.group(1) for m in QQ_RE.finditer(merged)))

    return {
        "name": guess_name(lines),
        "company": first_match_by_keywords(lines, COMPANY_KEYWORDS),
        "title": first_match_by_keywords(lines, TITLE_KEYWORDS),
        "address": first_match_by_keywords(lines, ADDRESS_KEYWORDS),
        "phones": normalize_phone_candidates(lines),
        "emails": emails,
        "websites": websites,
        "wechat_ids": wechat_ids,
        "qq_ids": qq_ids,
        "full_text": "\n".join(lines),
    }


def merge_to_target_centers(values: List[float], target_count: int) -> List[float]:
    if not values:
        return []
    clusters = [[v] for v in sorted(values)]
    while len(clusters) > target_count:
        best_i = 0
        best_gap = float("inf")
        for i in range(len(clusters) - 1):
            gap = abs(median(clusters[i + 1]) - median(clusters[i]))
            if gap < best_gap:
                best_gap = gap
                best_i = i
        clusters[best_i].extend(clusters[best_i + 1])
        del clusters[best_i + 1]
    return [median(c) for c in clusters]


def nearest_idx(v: float, centers: List[float]) -> int:
    return min(range(len(centers)), key=lambda i: abs(v - centers[i]))


def cluster_lines_to_cards(
    line_texts: List[str], line_rects: List[Dict[str, float]], expected_rows: int = 3, expected_cols: int = 2
) -> Dict[Tuple[int, int], List[Tuple[float, float, str]]]:
    rows = min(expected_rows, len(line_texts)) if line_texts else 0
    cols = expected_cols if line_texts else 0
    if rows == 0 or cols == 0:
        return {}

    items = []
    for i, txt in enumerate(line_texts):
        if i >= len(line_rects):
            continue
        r = line_rects[i]
        x = float(r.get("x", 0.0))
        y = float(r.get("y", 0.0))
        w = float(r.get("width", 0.0))
        h = float(r.get("height", 0.0))
        cx = x + w / 2.0
        cy = y + h / 2.0
        items.append((cx, cy, x, y, txt))

    if not items:
        return {}

    y_centers = merge_to_target_centers([it[1] for it in items], rows)
    x_centers = merge_to_target_centers([it[0] for it in items], cols)

    grouped: Dict[Tuple[int, int], List[Tuple[float, float, str]]] = {}
    for cx, cy, x, y, txt in items:
        r_idx = nearest_idx(cy, y_centers)
        c_idx = nearest_idx(cx, x_centers)
        grouped.setdefault((r_idx, c_idx), []).append((y, x, txt))
    return grouped


def process_input(input_jsonl: Path, output_dir: Path) -> Tuple[int, int, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_jsonl = output_dir / "ocr_card_results.jsonl"
    out_csv = output_dir / "ocr_card_contacts.csv"

    total_pages = 0
    total_cards = 0
    empty_cards = 0

    with (
        input_jsonl.open("r", encoding="utf-8") as fin,
        out_jsonl.open("w", encoding="utf-8") as fout,
        out_csv.open("w", newline="", encoding="utf-8-sig") as fcsv,
    ):
        writer = csv.DictWriter(
            fcsv,
            fieldnames=[
                "file",
                "page",
                "card_no",
                "grid_pos",
                "has_text",
                "name",
                "company",
                "title",
                "phones",
                "emails",
                "websites",
                "wechat_ids",
                "qq_ids",
                "address",
            ],
        )
        writer.writeheader()

        for line in fin:
            if not line.strip():
                continue
            page_obj = json.loads(line)
            if not page_obj.get("ok"):
                continue

            total_pages += 1
            page_no = page_obj.get("page")
            source_file = page_obj.get("file")
            line_texts = page_obj.get("line_texts", []) or []
            line_rects = page_obj.get("raw", {}).get("data", {}).get("line_rects", []) or []
            grouped = cluster_lines_to_cards(line_texts, line_rects, expected_rows=3, expected_cols=2)

            # Emit fixed 6 cards per page in row-major order (3x2 grid).
            card_no = 0
            for r_idx in range(3):
                for c_idx in range(2):
                    card_no += 1
                    rows = grouped.get((r_idx, c_idx), [])
                    sorted_lines = [txt for _, _, txt in sorted(rows, key=lambda t: (t[0], t[1]))]
                    has_text = bool(sorted_lines)
                    if not has_text:
                        empty_cards += 1

                    extracted = extract_fields(sorted_lines)
                    card_obj = {
                        "file": source_file,
                        "page": page_no,
                        "card_no": card_no,
                        "grid_pos": f"r{r_idx + 1}c{c_idx + 1}",
                        "has_text": has_text,
                        "line_texts": sorted_lines,
                        "extracted": extracted,
                    }
                    fout.write(json.dumps(card_obj, ensure_ascii=False) + "\n")
                    writer.writerow(
                        {
                            "file": source_file,
                            "page": page_no,
                            "card_no": card_no,
                            "grid_pos": card_obj["grid_pos"],
                            "has_text": has_text,
                            "name": extracted.get("name"),
                            "company": extracted.get("company"),
                            "title": extracted.get("title"),
                            "phones": ";".join(extracted.get("phones", [])),
                            "emails": ";".join(extracted.get("emails", [])),
                            "websites": ";".join(extracted.get("websites", [])),
                            "wechat_ids": ";".join(extracted.get("wechat_ids", [])),
                            "qq_ids": ";".join(extracted.get("qq_ids", [])),
                            "address": extracted.get("address"),
                        }
                    )
                    total_cards += 1

    return total_pages, total_cards, empty_cards


def main() -> int:
    parser = argparse.ArgumentParser(description="将页面级 OCR 结果拆分为名片级结构化结果")
    parser.add_argument(
        "--input-jsonl",
        default="output/ocr_run_20260508_PartA/ocr_results.jsonl",
        help="页面级结果文件路径（ocr_results.jsonl）",
    )
    parser.add_argument(
        "--output-dir",
        default="output/ocr_run_20260508_PartA",
        help="输出目录（默认写入同一运行目录）",
    )
    args = parser.parse_args()

    input_jsonl = Path(args.input_jsonl).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if not input_jsonl.exists():
        print(f"输入文件不存在: {input_jsonl}")
        return 2

    pages, cards, empty_cards = process_input(input_jsonl, output_dir)
    print(f"完成：页面 {pages}，名片 {cards}，空白名片位 {empty_cards}")
    print(f"输出: {output_dir / 'ocr_card_contacts.csv'}")
    print(f"输出: {output_dir / 'ocr_card_results.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
