#!/usr/bin/env python3
"""
Checkpoint management for OCR batch processing.

Each OCR run directory contains:
  - checkpoint.json   — progress metadata (atomic writes)
  - ocr_results.jsonl — one JSON object per page (append-only, flushed after each page)

Reusable batch pattern (also used by deepseek_kp_pipeline for card-level keys):
  1) Stable unit ID (page number / card_key)
  2) checkpoint.json lists completed IDs + status
  3) Result store flushed after each unit (jsonl upsert)
  4) --resume skips completed IDs; fatal API errors stop the run
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

CHECKPOINT_FILENAME = "checkpoint.json"
RESULTS_FILENAME = "ocr_results.jsonl"

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_INTERRUPTED = "interrupted"
STATUS_FATAL = "fatal"


def input_fingerprint(path: Path) -> str:
    """Stable identifier for an input file (name + size + mtime)."""
    stat = path.stat()
    return f"{path.name}:{stat.st_size}:{int(stat.st_mtime_ns)}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class OCRCheckpoint:
    """Manages incremental OCR progress for a single PDF run."""

    def __init__(self, run_dir: Path, input_file: Path) -> None:
        self.run_dir = run_dir
        self.input_file = input_file.resolve()
        self.checkpoint_path = run_dir / CHECKPOINT_FILENAME
        self.results_path = run_dir / RESULTS_FILENAME
        self._state: Optional[Dict[str, Any]] = None

    def load_or_create(self, total_pages: int) -> Dict[str, Any]:
        fingerprint = input_fingerprint(self.input_file)
        if self.checkpoint_path.exists():
            state = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            if state.get("input_fingerprint") == fingerprint:
                self._state = state
                return state
        self._state = {
            "version": 1,
            "input_file": self.input_file.name,
            "input_fingerprint": fingerprint,
            "total_pages": total_pages,
            "completed_pages": [],
            "failed_pages": [],
            "status": STATUS_RUNNING,
            "last_error": None,
            "started_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        }
        self.save()
        return self._state

    @property
    def state(self) -> Dict[str, Any]:
        if self._state is None:
            raise RuntimeError("Checkpoint not loaded; call load_or_create first")
        return self._state

    def save(self) -> None:
        self.state["updated_at"] = utc_now_iso()
        atomic_write_json(self.checkpoint_path, self.state)

    def completed_page_set(self) -> Set[int]:
        return set(self.state.get("completed_pages", []))

    def failed_page_set(self) -> Set[int]:
        return set(self.state.get("failed_pages", []))

    def is_fully_done(self) -> bool:
        total = self.state.get("total_pages", 0)
        if total <= 0:
            return False
        done = len(self.completed_page_set()) + len(self.failed_page_set())
        return done >= total and self.state.get("status") == STATUS_COMPLETED

    def load_existing_results(self) -> Dict[int, Dict[str, Any]]:
        """Load page -> result mapping from existing jsonl (for resume)."""
        by_page: Dict[int, Dict[str, Any]] = {}
        if not self.results_path.exists():
            return by_page
        with self.results_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                page = obj.get("page")
                if page is not None:
                    by_page[int(page)] = obj
        return by_page

    def upsert_result(self, item: Dict[str, Any]) -> None:
        """Write or replace one page result; always flush to disk immediately."""
        page = item.get("page")
        self.run_dir.mkdir(parents=True, exist_ok=True)

        by_page = self.load_existing_results()
        if page is not None:
            by_page[int(page)] = item
            lines = [by_page[k] for k in sorted(by_page.keys())]
        else:
            lines = list(by_page.values()) + [item]

        fd, tmp = tempfile.mkstemp(dir=str(self.run_dir), suffix=".jsonl.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for obj in lines:
                    f.write(json.dumps(obj, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.results_path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

        if page is not None:
            page_int = int(page)
            completed: List[int] = self.state.setdefault("completed_pages", [])
            failed: List[int] = self.state.setdefault("failed_pages", [])
            if item.get("ok"):
                if page_int not in completed:
                    completed.append(page_int)
                if page_int in failed:
                    failed.remove(page_int)
            else:
                if page_int not in failed:
                    failed.append(page_int)
                if page_int in completed:
                    completed.remove(page_int)
        self.save()

    def append_result(self, item: Dict[str, Any]) -> None:
        """Alias for upsert_result (backward compatible)."""
        self.upsert_result(item)

    def mark_completed(self) -> None:
        self.state["status"] = STATUS_COMPLETED
        self.state["last_error"] = None
        self.save()

    def mark_interrupted(self, error: str) -> None:
        self.state["status"] = STATUS_INTERRUPTED
        self.state["last_error"] = error
        self.save()

    def mark_fatal(self, error: str) -> None:
        self.state["status"] = STATUS_FATAL
        self.state["last_error"] = error
        self.save()


def is_fatal_api_error(result: Dict[str, Any]) -> bool:
    """Detect errors that should stop the batch (auth/quota/billing)."""
    http_status = result.get("http_status")
    if http_status in (401, 403, 402):
        return True
    msg = str(result.get("message") or result.get("error") or "").lower()
    fatal_keywords = [
        "quota",
        "balance",
        "欠费",
        "余额",
        "鉴权",
        "unauthorized",
        "access denied",
        "permission",
        "invalid access",
        "ak/sk",
        "credential",
    ]
    return any(k in msg for k in fatal_keywords)


def rebuild_csv_from_jsonl(results_path: Path, out_dir: Path) -> None:
    """Regenerate summary CSVs from jsonl (used after resume or completion)."""
    import csv

    if not results_path.exists():
        return

    results: List[Dict[str, Any]] = []
    with results_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                results.append(json.loads(line))

    summary_csv = out_dir / "ocr_contacts.csv"
    fail_csv = out_dir / "ocr_failed.csv"
    ok_items = [x for x in results if x.get("ok")]
    fail_items = [x for x in results if not x.get("ok")]

    with summary_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "file",
                "page",
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
        for item in ok_items:
            ext = item.get("extracted", {}) or {}
            writer.writerow(
                {
                    "file": item.get("file"),
                    "page": item.get("page"),
                    "name": ext.get("name"),
                    "company": ext.get("company"),
                    "title": ext.get("title"),
                    "phones": ";".join(ext.get("phones", [])),
                    "emails": ";".join(ext.get("emails", [])),
                    "websites": ";".join(ext.get("websites", [])),
                    "wechat_ids": ";".join(ext.get("wechat_ids", [])),
                    "qq_ids": ";".join(ext.get("qq_ids", [])),
                    "address": ext.get("address"),
                }
            )

    with fail_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f, fieldnames=["file", "page", "error", "http_like_code", "request_id"]
        )
        writer.writeheader()
        for item in fail_items:
            writer.writerow(
                {
                    "file": item.get("file"),
                    "page": item.get("page"),
                    "error": item.get("error"),
                    "http_like_code": item.get("http_like_code"),
                    "request_id": item.get("request_id"),
                }
            )
