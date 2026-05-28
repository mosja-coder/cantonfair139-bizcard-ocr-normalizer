"""Central data directory layout for the project."""

from __future__ import annotations

from pathlib import Path

INPUT_DIRNAME = "input"
OUTPUT_DIRNAME = "output"
DELIVERABLES_DIRNAME = "deliverables"
CACHE_DEEPSEEK_DIRNAME = "cache/deepseek"
SOURCE_DATASET_LABEL = "139届广交会"


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def data_root(root: Path | None = None) -> Path:
    return (root or project_root()) / "data"


def input_dir(root: Path | None = None) -> Path:
    dr = data_root(root)
    nested = dr / INPUT_DIRNAME
    return nested if nested.is_dir() else dr


def output_dir(root: Path | None = None) -> Path:
    return data_root(root) / OUTPUT_DIRNAME


def deliverables_dir(root: Path | None = None) -> Path:
    return data_root(root) / DELIVERABLES_DIRNAME


def deepseek_cache_dir(root: Path | None = None) -> Path:
    return data_root(root) / CACHE_DEEPSEEK_DIRNAME


def run_dir(root: Path | None, run_name: str) -> Path:
    return output_dir(root) / run_name
