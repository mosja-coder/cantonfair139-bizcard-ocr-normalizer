#!/usr/bin/env python3
"""
Batch OCR for business cards using Volcengine OCRNormal API.

Usage example:
  python batch_ocr_volcengine.py --input-file "../data/input/your-scan.pdf" --run-dir "../data/output/ocr_run_YYYYMMDD_PartA"

Required env vars:
  VOLC_ACCESS_KEY
  VOLC_SECRET_KEY

Optional env vars:
  VOLC_REGION=cn-north-1
  VOLC_SERVICE=cv
  VOLC_ENDPOINT=visual.volcengineapi.com
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import hashlib
import hmac
import json
import mimetypes
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv

from checkpoint import (
    OCRCheckpoint,
    is_fatal_api_error,
    rebuild_csv_from_jsonl,
)

API_ACTION = "OCRNormal"
API_VERSION = "2020-08-26"
SUPPORTED_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".pdf", ".heic", ".heif"}


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def bootstrap_dotenv() -> None:
    # Auto-load .env in project root; existing system env takes precedence.
    root = project_root()
    for env_path in (root / ".env", Path(__file__).resolve().parent / ".env"):
        if env_path.exists():
            load_dotenv(dotenv_path=env_path, override=False)
            return
    load_dotenv(dotenv_path=root / ".env", override=False)

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
]
ADDRESS_KEYWORDS = ["省", "市", "区", "县", "路", "街", "号", "大厦", "室", "楼", "园区", "镇"]


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def build_authorization(
    access_key: str,
    secret_key: str,
    region: str,
    service: str,
    host: str,
    body_str: str,
    x_date: str,
    query: Dict[str, str],
    security_token: Optional[str] = None,
) -> Tuple[str, str]:
    short_date = x_date[:8]
    x_content_sha256 = sha256_hex(body_str.encode("utf-8"))
    canonical_query = "&".join(
        f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
        for k, v in sorted(query.items(), key=lambda kv: kv[0])
    )
    canonical_headers_list = [
        ("content-type", "application/x-www-form-urlencoded"),
        ("host", host),
        ("x-content-sha256", x_content_sha256),
        ("x-date", x_date),
    ]
    if security_token:
        canonical_headers_list.append(("x-security-token", security_token))
    canonical_headers_list = sorted(canonical_headers_list, key=lambda kv: kv[0])
    canonical_headers = "".join(f"{k}:{v}\n" for k, v in canonical_headers_list)
    signed_headers = ";".join(k for k, _ in canonical_headers_list)

    canonical_request = "\n".join(
        [
            "POST",
            "/",
            canonical_query,
            canonical_headers,
            signed_headers,
            x_content_sha256,
        ]
    )
    credential_scope = f"{short_date}/{region}/{service}/request"
    string_to_sign = "\n".join(
        ["HMAC-SHA256", x_date, credential_scope, sha256_hex(canonical_request.encode("utf-8"))]
    )

    # Follow official Volcengine signature demo:
    # kDate = HMAC_SHA256(secret_key, short_date)
    k_date = hmac_sha256(secret_key.encode("utf-8"), short_date)
    k_region = hmac.new(k_date, region.encode("utf-8"), hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode("utf-8"), hashlib.sha256).digest()
    k_signing = hmac.new(k_service, b"request", hashlib.sha256).digest()
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    auth = (
        "HMAC-SHA256 "
        f"Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )
    return auth, x_content_sha256


def maybe_convert_heic(path: Path) -> Optional[bytes]:
    ext = path.suffix.lower()
    if ext not in {".heic", ".heif"}:
        return path.read_bytes()
    try:
        from PIL import Image  # type: ignore
        import pillow_heif  # type: ignore
    except Exception:
        return None
    pillow_heif.register_heif_opener()
    with Image.open(path) as img:
        from io import BytesIO

        buf = BytesIO()
        rgb = img.convert("RGB")
        rgb.save(buf, format="JPEG", quality=95)
        return buf.getvalue()


def normalize_phone_candidates(lines: List[str]) -> List[str]:
    phones: List[str] = []
    for line in lines:
        for m in PHONE_RE.findall(line):
            cleaned = re.sub(r"[^\d+]", "", m)
            if len(cleaned.replace("+", "")) >= 7:
                phones.append(cleaned)
    return sorted(set(phones))


def first_match_by_keywords(lines: List[str], keywords: List[str]) -> Optional[str]:
    for line in lines:
        if any(k in line for k in keywords):
            return line
    return None


def guess_name(lines: List[str]) -> Optional[str]:
    for line in lines:
        txt = line.strip()
        if not txt:
            continue
        if any(k in txt for k in COMPANY_KEYWORDS + TITLE_KEYWORDS + ADDRESS_KEYWORDS):
            continue
        if EMAIL_RE.search(txt) or WEBSITE_RE.search(txt):
            continue
        if re.search(r"\d", txt):
            continue
        if 2 <= len(txt) <= 12:
            return txt
    return None


def extract_business_card_fields(lines: List[str]) -> Dict[str, object]:
    merged = " | ".join(lines)
    emails = sorted(set(EMAIL_RE.findall(merged)))
    websites = sorted(set(WEBSITE_RE.findall(merged)))
    wechat_ids = sorted(set(m.group(1) for m in WECHAT_RE.finditer(merged)))
    qq_ids = sorted(set(m.group(1) for m in QQ_RE.finditer(merged)))

    company = first_match_by_keywords(lines, COMPANY_KEYWORDS)
    title = first_match_by_keywords(lines, TITLE_KEYWORDS)
    address = first_match_by_keywords(lines, ADDRESS_KEYWORDS)
    phones = normalize_phone_candidates(lines)
    name = guess_name(lines)

    return {
        "name": name,
        "company": company,
        "title": title,
        "phones": phones,
        "emails": emails,
        "websites": websites,
        "wechat_ids": wechat_ids,
        "qq_ids": qq_ids,
        "address": address,
        "full_text": "\n".join(lines),
    }


def call_ocr(
    image_bytes: bytes,
    access_key: str,
    secret_key: str,
    region: str,
    service: str,
    endpoint: str,
    security_token: Optional[str],
    timeout: int,
) -> Dict:
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    body_obj = {
        "image_base64": image_b64,
        "mode": "default",
        "filter_thresh": "80",
    }
    body_str = urllib.parse.urlencode(body_obj)

    query = {"Action": API_ACTION, "Version": API_VERSION}
    x_date = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    auth, x_content_sha256 = build_authorization(
        access_key=access_key,
        secret_key=secret_key,
        region=region,
        service=service,
        host=endpoint,
        body_str=body_str,
        x_date=x_date,
        query=query,
        security_token=security_token,
    )
    url = f"https://{endpoint}/?{urllib.parse.urlencode(query)}"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Host": endpoint,
        "X-Content-Sha256": x_content_sha256,
        "X-Date": x_date,
        "Authorization": auth,
    }
    if security_token:
        headers["X-Security-Token"] = security_token
    req = urllib.request.Request(url=url, data=body_str.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Return detailed server body for diagnosis, instead of only "HTTP Error 401".
        body = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
            parsed.setdefault("http_status", e.code)
            return parsed
        except Exception:
            return {"http_status": e.code, "message": body or str(e)}


def process_one(
    path: Path,
    image_bytes: bytes,
    access_key: str,
    secret_key: str,
    region: str,
    service: str,
    endpoint: str,
    security_token: Optional[str],
    timeout: int,
    retries: int,
) -> Dict[str, object]:
    last_err = None
    for attempt in range(retries + 1):
        try:
            result = call_ocr(
                image_bytes=image_bytes,
                access_key=access_key,
                secret_key=secret_key,
                region=region,
                service=service,
                endpoint=endpoint,
                security_token=security_token,
                timeout=timeout,
            )
            code = result.get("code")
            if code != 10000:
                err_item = {
                    "file": path.name,
                    "ok": False,
                    "error": result.get("message", "unknown error"),
                    "http_like_code": code,
                    "request_id": result.get("request_id"),
                    "raw": result,
                }
                if is_fatal_api_error(result):
                    err_item["fatal"] = True
                return err_item
            lines = result.get("data", {}).get("line_texts", []) or []
            extracted = extract_business_card_fields(lines)
            return {
                "file": path.name,
                "ok": True,
                "request_id": result.get("request_id"),
                "ocr_code": code,
                "line_texts": lines,
                "extracted": extracted,
                "raw": result,
            }
        except Exception as e:
            last_err = str(e)
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
            else:
                break
    return {"file": path.name, "ok": False, "error": last_err or "unknown exception"}


def split_pdf_pages(path: Path) -> Tuple[Optional[List[bytes]], Optional[str]]:
    try:
        from pypdf import PdfReader, PdfWriter  # type: ignore
    except Exception:
        return None, "处理 PDF 需要安装 pypdf，请执行: pip install pypdf"

    try:
        reader = PdfReader(str(path))
        page_bytes_list: List[bytes] = []
        for page in reader.pages:
            writer = PdfWriter()
            writer.add_page(page)
            buf = BytesIO()
            writer.write(buf)
            page_bytes_list.append(buf.getvalue())
        return page_bytes_list, None
    except Exception as e:
        return None, f"PDF 拆页失败: {e}"


def save_outputs(results: List[Dict[str, object]], out_dir: Path) -> None:
    """Write full results (non-incremental mode). Prefer rebuild_csv_from_jsonl after checkpoint runs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_jsonl = out_dir / "ocr_results.jsonl"
    with raw_jsonl.open("w", encoding="utf-8") as f:
        for item in results:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    rebuild_csv_from_jsonl(raw_jsonl, out_dir)


def list_input_files(input_dir: Path) -> List[Path]:
    files = []
    for p in sorted(input_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXT:
            files.append(p)
    return files


def process_pdf_with_checkpoint(
    file_path: Path,
    page_list: List[bytes],
    ckpt: OCRCheckpoint,
    access_key: str,
    secret_key: str,
    region: str,
    service: str,
    endpoint: str,
    security_token: Optional[str],
    timeout: int,
    retries: int,
    resume: bool,
) -> Tuple[int, int, bool]:
    """
    Process PDF pages with incremental checkpoint saves.
    Returns (ok_count, fail_count, stopped_fatal).
    """
    ckpt.load_or_create(total_pages=len(page_list))
    existing = ckpt.load_existing_results() if resume else {}
    skip_pages = ckpt.completed_page_set() if resume else set()

    ok_count = 0
    fail_count = 0

    for page_no, page_bytes in enumerate(page_list, start=1):
        if resume and page_no in skip_pages and existing.get(page_no, {}).get("ok"):
            print(f"    - 页 {page_no}/{len(page_list)} [跳过-已完成]")
            ok_count += 1
            continue

        print(f"    - 页 {page_no}/{len(page_list)}")
        item = process_one(
            path=file_path,
            image_bytes=page_bytes,
            access_key=access_key,
            secret_key=secret_key,
            region=region,
            service=service,
            endpoint=endpoint,
            security_token=security_token,
            timeout=timeout,
            retries=retries,
        )
        item["page"] = page_no
        ckpt.append_result(item)

        if item.get("ok"):
            ok_count += 1
        else:
            fail_count += 1
            print(f"      -> 失败: {item.get('error')}")
            if item.get("fatal") or is_fatal_api_error(item.get("raw") or {}):
                err = str(item.get("error") or "fatal API error")
                ckpt.mark_fatal(err)
                print(f"      -> 致命错误，已保存断点，停止处理: {err}")
                return ok_count, fail_count, True

    ckpt.mark_completed()
    rebuild_csv_from_jsonl(ckpt.results_path, ckpt.run_dir)
    return ok_count, fail_count, False


def main() -> int:
    bootstrap_dotenv()
    parser = argparse.ArgumentParser(description="Volcengine 名片批量 OCR（支持断点续传）")
    parser.add_argument("--input-dir", default="sample", help="输入目录，默认 sample")
    parser.add_argument("--input-file", default="", help="单个输入文件路径（图片或PDF）")
    parser.add_argument("--output-dir", default="output", help="输出目录，默认 output")
    parser.add_argument("--run-dir", default="", help="指定运行目录（断点续传时使用固定路径）")
    parser.add_argument("--resume", action="store_true", help="从断点续传，跳过已成功页")
    parser.add_argument("--timeout", type=int, default=30, help="请求超时秒数")
    parser.add_argument("--retries", type=int, default=2, help="失败重试次数")
    args = parser.parse_args()

    access_key = os.getenv("VOLC_ACCESS_KEY", "").strip()
    secret_key = os.getenv("VOLC_SECRET_KEY", "").strip()
    security_token = os.getenv("VOLC_SECURITY_TOKEN", "").strip() or None
    region = os.getenv("VOLC_REGION", "cn-north-1").strip()
    service = os.getenv("VOLC_SERVICE", "cv").strip()
    endpoint = os.getenv("VOLC_ENDPOINT", "visual.volcengineapi.com").strip()

    if not access_key or not secret_key:
        print("缺少环境变量：VOLC_ACCESS_KEY / VOLC_SECRET_KEY", file=sys.stderr)
        return 2

    files: List[Path]
    if args.input_file:
        input_file = Path(args.input_file).expanduser().resolve()
        if not input_file.exists() or not input_file.is_file():
            print(f"输入文件不存在: {input_file}", file=sys.stderr)
            return 2
        files = [input_file]
    else:
        input_dir = Path(args.input_dir).expanduser().resolve()
        if not input_dir.exists():
            print(f"输入目录不存在: {input_dir}", file=sys.stderr)
            return 2
        files = list_input_files(input_dir)
        if not files:
            print(f"输入目录未发现支持的文件: {input_dir}", file=sys.stderr)
            return 2

    if args.run_dir:
        out_dir = Path(args.run_dir).expanduser().resolve()
    else:
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path(args.output_dir).expanduser().resolve() / f"ocr_run_{ts}"

    print(f"待处理文件: {len(files)}")
    print(f"输出目录: {out_dir}")
    if args.resume:
        print("断点续传: 已启用")

    total_ok = 0
    total_fail = 0
    stopped_fatal = False

    for idx, file_path in enumerate(files, start=1):
        print(f"[{idx}/{len(files)}] 识别文件: {file_path.name}")
        file_run_dir = out_dir if len(files) == 1 else out_dir / file_path.stem
        ckpt = OCRCheckpoint(file_run_dir, file_path)

        if args.resume and ckpt.checkpoint_path.exists() and ckpt.is_fully_done():
            print(f"  -> 已全部完成，跳过（见 {ckpt.checkpoint_path}）")
            rebuild_csv_from_jsonl(ckpt.results_path, file_run_dir)
            existing = ckpt.load_existing_results()
            total_ok += sum(1 for v in existing.values() if v.get("ok"))
            total_fail += sum(1 for v in existing.values() if not v.get("ok"))
            continue

        if file_path.suffix.lower() == ".pdf":
            page_list, err = split_pdf_pages(file_path)
            if err:
                item = {"file": file_path.name, "page": None, "ok": False, "error": err}
                file_run_dir.mkdir(parents=True, exist_ok=True)
                ckpt.load_or_create(total_pages=0)
                ckpt.append_result(item)
                ckpt.mark_fatal(err)
                print(f"  -> 失败: {err}")
                total_fail += 1
                continue
            assert page_list is not None
            print(f"  -> PDF页数: {len(page_list)}")
            ok, fail, fatal = process_pdf_with_checkpoint(
                file_path=file_path,
                page_list=page_list,
                ckpt=ckpt,
                access_key=access_key,
                secret_key=secret_key,
                region=region,
                service=service,
                endpoint=endpoint,
                security_token=security_token,
                timeout=args.timeout,
                retries=args.retries,
                resume=args.resume,
            )
            total_ok += ok
            total_fail += fail
            if fatal:
                stopped_fatal = True
                break
        else:
            img_bytes = maybe_convert_heic(file_path)
            if img_bytes is None:
                item = {
                    "file": file_path.name,
                    "page": None,
                    "ok": False,
                    "error": "HEIC/HEIF 需要安装 pillow 和 pillow-heif 才能转换",
                }
                file_run_dir.mkdir(parents=True, exist_ok=True)
                ckpt.load_or_create(total_pages=1)
                ckpt.append_result(item)
                print(f"  -> 失败: {item.get('error')}")
                total_fail += 1
                continue
            ckpt.load_or_create(total_pages=1)
            item = process_one(
                path=file_path,
                image_bytes=img_bytes,
                access_key=access_key,
                secret_key=secret_key,
                region=region,
                service=service,
                endpoint=endpoint,
                security_token=security_token,
                timeout=args.timeout,
                retries=args.retries,
            )
            item["page"] = None
            ckpt.append_result(item)
            if item.get("ok"):
                total_ok += 1
            else:
                total_fail += 1
                print(f"  -> 失败: {item.get('error')}")
                if item.get("fatal"):
                    ckpt.mark_fatal(str(item.get("error")))
                    stopped_fatal = True
                    break
            ckpt.mark_completed()
            rebuild_csv_from_jsonl(ckpt.results_path, file_run_dir)

    print(f"完成: 成功 {total_ok}，失败 {total_fail}")
    if stopped_fatal:
        print("因致命错误中断。已保存断点，可使用 --resume 继续。")
        print(f"断点文件: {out_dir / 'checkpoint.json'}")
        return 3
    print(f"结果文件: {out_dir / 'ocr_contacts.csv'}")
    print(f"原始结果: {out_dir / 'ocr_results.jsonl'}")
    print(f"失败列表: {out_dir / 'ocr_failed.csv'}")
    return 0 if total_ok > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
