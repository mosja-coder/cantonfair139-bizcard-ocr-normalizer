"""12 分区配置：OCR 输出目录名 + 业务标签。"""

from __future__ import annotations

from typing import List, Tuple

# (ocr_run 目录名, source_label)
PARTITIONS: List[Tuple[str, str]] = [
    ("ocr_run_20260508_PartA", "1期-PartA"),
    ("ocr_run_20260508_PartB", "1期-PartB"),
    ("ocr_run_20260508_PartC", "1期-PartC"),
    ("ocr_run_20260508_PartD", "1期-PartD"),
    ("ocr_run_20260527_P2PartA", "2期-PartA"),
    ("ocr_run_20260527_P2PartB", "2期-PartB"),
    ("ocr_run_20260527_P2PartC", "2期-PartC"),
    ("ocr_run_20260527_P2PartD", "2期-PartD"),
    ("ocr_run_20260527_P3PartA", "3期-PartA"),
    ("ocr_run_20260527_P3PartB", "3期-PartB"),
    ("ocr_run_20260527_P3PartC", "3期-PartC"),
    ("ocr_run_20260527_P3PartD", "3期-PartD"),
]

SOURCE_COL = "source_label"
DATASET_COL = "source_dataset"
