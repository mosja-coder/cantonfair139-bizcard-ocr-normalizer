#!/usr/bin/env python3
"""
Normalize mainland China mobile numbers from OCR contacts CSV
and write expanded rows into Excel sheet "phone_processed".

Usage:
  python process_phone_sheet.py --input "output/ocr_run_20260508_PartA/ocr_card_contacts.csv"
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from typing import Dict, Iterable, List

from openpyxl import Workbook

MOBILE_11_RE = re.compile(r"1[3-9]\d{9}")
SPLIT_RE = re.compile(r"[;,，/|、\s]+")


def normalize_token_to_mobiles(token: str) -> List[str]:
    token = token.strip()
    if not token:
        return []

    # Keep digits and "+" only for robust cleanup.
    cleaned = re.sub(r"[^0-9+]", "", token)
    cleaned = cleaned.lstrip("+")

    # Remove common country code prefixes.
    while cleaned.startswith("0086"):
        cleaned = cleaned[4:]
    while len(cleaned) > 11 and cleaned.startswith("86"):
        cleaned = cleaned[2:]

    # Extract all possible mainland mobile numbers.
    return MOBILE_11_RE.findall(cleaned)


def normalize_phones(phones_raw: str) -> List[str]:
    if not phones_raw:
        return []

    results: List[str] = []
    seen = set()
    tokens = SPLIT_RE.split(phones_raw.strip())
    for token in tokens:
        if not token:
            continue
        for mobile in normalize_token_to_mobiles(token):
            if mobile not in seen:
                seen.add(mobile)
                results.append(mobile)
    return results


def read_csv_rows(csv_path: Path) -> List[Dict[str, str]]:
    encodings = ["utf-8-sig", "gb18030"]
    last_error: Exception | None = None
    for enc in encodings:
        try:
            with csv_path.open("r", encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError as e:
            last_error = e
            continue
    raise RuntimeError(f"无法识别CSV编码: {csv_path} ({last_error})")


def expand_by_phone(rows: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    expanded: List[Dict[str, str]] = []
    for row in rows:
        mobiles = normalize_phones(row.get("phones", "") or "")
        for mobile in mobiles:
            new_row = dict(row)
            new_row["phone_normalized"] = mobile
            expanded.append(new_row)
    return expanded


def write_phone_processed_sheet(output_xlsx: Path, expanded_rows: List[Dict[str, str]]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "phone_processed"

    if expanded_rows:
        headers = list(expanded_rows[0].keys())
    else:
        headers = ["phone_normalized"]

    ws.append(headers)
    for row in expanded_rows:
        ws.append([row.get(h, "") for h in headers])

    wb.save(output_xlsx)


def default_output_xlsx(input_csv: Path) -> Path:
    return input_csv.with_suffix(".xlsx")


def main() -> int:
    parser = argparse.ArgumentParser(description="手机号标准化并按手机号展开行")
    parser.add_argument("--input", required=True, help="输入CSV路径，如 ocr_card_contacts.csv")
    parser.add_argument("--output-xlsx", default="", help="输出Excel路径，默认与输入同名 .xlsx")
    args = parser.parse_args()

    input_csv = Path(args.input).expanduser().resolve()
    if not input_csv.exists():
        print(f"输入文件不存在: {input_csv}")
        return 2
    if input_csv.suffix.lower() != ".csv":
        print(f"当前脚本仅支持CSV输入: {input_csv}")
        return 2

    output_xlsx = Path(args.output_xlsx).expanduser().resolve() if args.output_xlsx else default_output_xlsx(input_csv)

    rows = read_csv_rows(input_csv)
    expanded_rows = expand_by_phone(rows)
    write_phone_processed_sheet(output_xlsx, expanded_rows)

    print(f"原始行数: {len(rows)}")
    print(f"手机号展开后行数: {len(expanded_rows)}")
    print(f"输出文件: {output_xlsx}")
    print("输出sheet: phone_processed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
