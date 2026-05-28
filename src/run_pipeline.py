#!/usr/bin/env python3
"""
Unified pipeline entry:
1) OCR page-level extraction (with checkpoint/resume)
2) Card-level split/extraction
3) Phone normalization to xlsx (phone_processed sheet)

Examples:
  # Process one PDF
  python run_pipeline.py --input-file "data/广交会_2期3期_5600张/2期A区-xxx.pdf"

  # Batch process all PDFs in a data folder (auto-resume)
  python run_pipeline.py --data-dir "data/广交会_2期3期_5600张"

  # Resume after interruption
  python run_pipeline.py --data-dir "data/广交会_2期3期_5600张" --resume
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
from dotenv import load_dotenv

from checkpoint import CHECKPOINT_FILENAME, STATUS_COMPLETED
from paths import data_root as get_data_root
from paths import input_dir, output_dir, project_root


def bootstrap_dotenv(root: Path) -> None:
    load_dotenv(dotenv_path=root / ".env", override=False)


def run_cmd(args: List[str], cwd: Path) -> int:
    print(">>>", " ".join(args))
    proc = subprocess.run(args, cwd=str(cwd))
    return proc.returncode


def classify_part(pdf_path: Path) -> str:
    """Derive output tag from filename, e.g. P2PartA / P3PartB / PartA."""
    stem = pdf_path.stem
    phase = ""
    m_phase = re.search(r"([23])期", stem)
    if m_phase:
        phase = f"P{m_phase.group(1)}"
    m_zone = re.search(r"([A-Da-d])\s*区", stem)
    zone = m_zone.group(1).upper() if m_zone else ""
    if not zone:
        m2 = re.search(r"([A-Za-z])", stem)
        zone = m2.group(1).upper() if m2 else "X"
    if phase:
        return f"{phase}Part{zone}"
    return f"Part{zone}"


def choose_run_dir(workspace: Path, part_tag: str) -> Path:
    date_tag = datetime.now().strftime("%Y%m%d")
    run = output_dir(workspace) / f"ocr_run_{date_tag}_{part_tag}"
    run.mkdir(parents=True, exist_ok=True)
    return run


def is_ocr_complete(run_dir: Path) -> bool:
    ckpt_path = run_dir / CHECKPOINT_FILENAME
    if not ckpt_path.exists():
        return False
    try:
        state = json.loads(ckpt_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return state.get("status") == STATUS_COMPLETED and (run_dir / "ocr_results.jsonl").exists()


def is_card_extract_done(run_dir: Path) -> bool:
    return (run_dir / "ocr_card_contacts.csv").exists() and (run_dir / "ocr_card_results.jsonl").exists()


def is_phone_done(run_dir: Path) -> bool:
    return (run_dir / "ocr_card_contacts.xlsx").exists()


def find_existing_run_dir(workspace: Path, part_tag: str) -> Optional[Path]:
    output_root = output_dir(workspace)
    if not output_root.exists():
        return None
    prefix = f"ocr_run_"
    suffix = f"_{part_tag}"
    candidates = [
        p
        for p in output_root.iterdir()
        if p.is_dir() and p.name.startswith(prefix) and p.name.endswith(suffix)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def resolve_run_dir(workspace: Path, part_tag: str, resume: bool) -> Path:
    if resume:
        existing = find_existing_run_dir(workspace, part_tag)
        if existing:
            return existing
    return choose_run_dir(workspace, part_tag)


def process_one_pdf(
    src_dir: Path,
    workspace: Path,
    pdf: Path,
    part_tag: str,
    resume: bool,
) -> Tuple[Path, int]:
    run_dir = resolve_run_dir(workspace, part_tag, resume=resume)

    if resume and is_ocr_complete(run_dir):
        print(f"OCR 已完成，跳过: {run_dir}")
    else:
        ocr_args = [
            sys.executable,
            "batch_ocr_volcengine.py",
            "--input-file",
            str(pdf),
            "--run-dir",
            str(run_dir),
        ]
        if resume:
            ocr_args.append("--resume")
        code = run_cmd(ocr_args, cwd=src_dir)
        if code in (2, 3):
            if code == 2:
                print("缺少 API 凭证，请在项目根目录创建 .env（参考 .env.example）")
            else:
                print("OCR 因致命错误中断，断点已保存。")
            return run_dir, code
        if code != 0:
            raise RuntimeError(f"OCR 失败({code}): {pdf.name}")

    if resume and is_card_extract_done(run_dir):
        print(f"名片拆分已完成，跳过: {run_dir}")
    else:
        code = run_cmd(
            [
                sys.executable,
                "extract_cards_from_ocr_results.py",
                "--input-jsonl",
                str(run_dir / "ocr_results.jsonl"),
                "--output-dir",
                str(run_dir),
            ],
            cwd=src_dir,
        )
        if code != 0:
            raise RuntimeError(f"名片拆分失败({code}): {pdf.name}")

    if resume and is_phone_done(run_dir):
        print(f"手机号标准化已完成，跳过: {run_dir}")
    else:
        code = run_cmd(
            [
                sys.executable,
                "process_phone_sheet.py",
                "--input",
                str(run_dir / "ocr_card_contacts.csv"),
            ],
            cwd=src_dir,
        )
        if code != 0:
            raise RuntimeError(f"手机号标准化失败({code}): {pdf.name}")

    return run_dir, 0


def list_pdfs(input_dir: Path) -> List[Path]:
    return sorted(p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")


def resolve_workspace(arg: str, root: Path) -> Path:
    if arg:
        p = Path(arg).expanduser()
        if not p.is_absolute():
            p = root / p
        return p.resolve()
    return get_data_root(root)


def print_status(workspace: Path) -> None:
    inp = input_dir(workspace)
    pdfs = list_pdfs(inp)
    if not pdfs:
        print(f"未找到 PDF: {inp}")
        return
    print(f"数据目录: {workspace}")
    print(f"PDF 输入: {inp}\n")
    print(f"{'分区':<12} {'OCR':<8} {'名片':<8} {'Excel':<8} 文件")
    print("-" * 72)
    for pdf in pdfs:
        tag = classify_part(pdf)
        run_dir = find_existing_run_dir(workspace, tag)
        ocr = "—"
        cards = "—"
        xlsx = "—"
        if run_dir:
            if is_ocr_complete(run_dir):
                ckpt = json.loads((run_dir / CHECKPOINT_FILENAME).read_text(encoding="utf-8"))
                done = len(ckpt.get("completed_pages", []))
                total = ckpt.get("total_pages", "?")
                ocr = f"{done}/{total}"
            elif (run_dir / CHECKPOINT_FILENAME).exists():
                ckpt = json.loads((run_dir / CHECKPOINT_FILENAME).read_text(encoding="utf-8"))
                done = len(ckpt.get("completed_pages", []))
                total = ckpt.get("total_pages", "?")
                ocr = f"{done}/{total}*"
            cards = "✓" if is_card_extract_done(run_dir) else "—"
            xlsx = "✓" if is_phone_done(run_dir) else "—"
        print(f"{tag:<12} {ocr:<8} {cards:<8} {xlsx:<8} {pdf.name[:40]}")
    print("\n* = 进行中（可用 --resume 续传）")


def main() -> int:
    parser = argparse.ArgumentParser(description="统一OCR处理流水线（OCR + 名片拆分 + 手机号标准化 + 断点续传）")
    parser.add_argument("--project-root", default="", help="项目根目录，默认自动检测")
    parser.add_argument(
        "--data-dir",
        default="",
        help="数据工作区，默认 data/（PDF 在 input/，OCR 在 output/）",
    )
    parser.add_argument("--input-file", default="", help="单个PDF文件路径")
    parser.add_argument("--input-dir", default="", help="PDF目录路径（批量，兼容旧参数）")
    parser.add_argument("--skip-parts", default="", help="批量模式跳过分区，例如 A,B 或 P2PartA")
    parser.add_argument("--tag", default="", help="单文件模式输出分区标签")
    parser.add_argument("--resume", action="store_true", help="断点续传（跳过已完成步骤）")
    parser.add_argument("--status", action="store_true", help="查看数据集处理进度")
    args = parser.parse_args()

    root = Path(args.project_root).expanduser().resolve() if args.project_root else project_root()
    src_dir = root / "src"
    bootstrap_dotenv(root)

    workspace = resolve_workspace(args.data_dir or args.input_dir, root)

    if args.status:
        print_status(workspace)
        return 0

    if args.input_file:
        pdf = Path(args.input_file).expanduser().resolve()
        if not pdf.exists():
            print(f"输入PDF不存在: {pdf}")
            return 2
        part_tag = args.tag.strip() or classify_part(pdf)
        out, code = process_one_pdf(src_dir, workspace, pdf, part_tag, resume=args.resume)
        print(f"完成: {pdf.name}")
        print(f"输出: {out}")
        return code

    inp = input_dir(workspace)
    if workspace.exists() and inp.exists():
        pdfs = list_pdfs(inp)
        if not pdfs:
            print(f"目录内未找到PDF: {inp}")
            return 2

        skip = set(x.strip() for x in args.skip_parts.split(",") if x.strip())
        todo: List[Tuple[Path, str]] = []
        for pdf in pdfs:
            part_tag = classify_part(pdf)
            if part_tag in skip or part_tag[-1] in skip:
                continue
            todo.append((pdf, part_tag))

        print(f"数据目录: {workspace}")
        print(f"PDF 输入: {inp}")
        print(f"待处理数量: {len(todo)}")
        print(f"断点续传: {'是' if args.resume else '否（加 --resume 可续传）'}")

        for i, (pdf, part_tag) in enumerate(todo, start=1):
            print(f"\n[{i}/{len(todo)}] 处理: {pdf.name} ({part_tag})")
            out, code = process_one_pdf(src_dir, workspace, pdf, part_tag, resume=args.resume)
            print(f"完成输出: {out}")
            if code in (2, 3):
                print("\n批量处理暂停。修复后重新运行相同命令并加 --resume。")
                return code

        print("\n全部完成。")
        return 0

    print("请提供 --input-file，或确保 data/input/ 下有 PDF（可用 --data-dir 指定工作区）")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
