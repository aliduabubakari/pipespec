from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Tuple

import yaml

DocFormat = Literal["json", "yaml"]


def load_doc(path: str | Path) -> tuple[dict[str, Any], DocFormat]:
    """
    Load a PipeSpec-like document from JSON or YAML into a Python dict.
    Returns (doc, fmt) where fmt is the detected source format.
    """
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    suf = p.suffix.lower()

    if suf == ".json":
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Top-level must be an object/dict.")
        return data, "json"

    if suf in {".yaml", ".yml"}:
        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            raise ValueError("Top-level must be an object/dict.")
        return data, "yaml"

    # fallback autodetect
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Top-level must be an object/dict.")
        return data, "json"
    except Exception:
        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            raise ValueError("Top-level must be an object/dict.")
        return data, "yaml"


def write_doc(doc: dict[str, Any], path: str | Path, fmt: DocFormat) -> None:
    """
    Write a document back to JSON or YAML.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "json":
        p.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return

    if fmt == "yaml":
        p.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return

    raise ValueError(f"Unknown format: {fmt}")