from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

import yaml

from .models import ValidationErrorItem, ValidationResult
from .validator import load_schema, SCHEMA_VERSION


ReportFormat = Literal["json", "yaml", "md"]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _error_item_to_dict(e: ValidationErrorItem) -> dict[str, Any]:
    d = asdict(e)
    # Convenient top-level rule_id (if present)
    rule_id = None
    if isinstance(e.details, dict):
        rule_id = e.details.get("rule_id")
    d["rule_id"] = rule_id
    return d


def make_report(
    result: ValidationResult,
    *,
    source_path: str | None = None,
    include_schema_id: bool = True,
    include_timestamp: bool = True,
    tool_name: str = "pipespec-validator",
) -> dict[str, Any]:
    schema = load_schema() if include_schema_id else {}
    schema_id = schema.get("$id") if include_schema_id else None

    report: dict[str, Any] = {
        "report_version": "1.0",
        "tool": tool_name,
        "generated_at": _utc_now_iso() if include_timestamp else None,
        "pipespec_schema_version": SCHEMA_VERSION,
        "pipespec_schema_id": schema_id,
        "source_path": source_path,
        "ok": result.ok,
        "summary": {
            "error_count": len(result.errors),
            "warning_count": len(result.warnings),
        },
        "errors": [_error_item_to_dict(e) for e in result.errors],
        "warnings": [_error_item_to_dict(w) for w in result.warnings],
    }

    # remove None values for neatness
    return {k: v for k, v in report.items() if v is not None}


def write_report(
    report: dict[str, Any],
    out_path: str | Path,
    *,
    fmt: ReportFormat | None = None,
) -> None:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if fmt is None:
        # infer from extension
        ext = out.suffix.lower().lstrip(".")
        if ext in {"json", "yaml", "yml", "md"}:
            fmt = "yaml" if ext in {"yaml", "yml"} else ext  # type: ignore[assignment]
        else:
            fmt = "json"

    if fmt == "json":
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return

    if fmt == "yaml":
        out.write_text(yaml.safe_dump(report, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return

    if fmt == "md":
        out.write_text(render_report_markdown(report), encoding="utf-8")
        return

    raise ValueError(f"Unsupported report format: {fmt}")


def render_report_markdown(report: dict[str, Any]) -> str:
    """
    Minimal human-readable report. JSON/YAML remain the canonical machine-readable formats.
    """
    lines: list[str] = []
    lines.append("# PipeSpec Validation Report")
    lines.append("")
    lines.append(f"- Tool: `{report.get('tool')}`")
    lines.append(f"- Generated at: `{report.get('generated_at', '')}`")
    lines.append(f"- Schema version: `{report.get('pipespec_schema_version')}`")
    lines.append(f"- Schema $id: `{report.get('pipespec_schema_id', '')}`")
    lines.append(f"- Source: `{report.get('source_path', '')}`")
    lines.append(f"- OK: `{report.get('ok')}`")
    lines.append("")

    summary = report.get("summary", {})
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Errors: `{summary.get('error_count', 0)}`")
    lines.append(f"- Warnings: `{summary.get('warning_count', 0)}`")
    lines.append("")

    def render_items(title: str, items: list[dict[str, Any]]) -> None:
        lines.append(f"## {title}")
        lines.append("")
        if not items:
            lines.append("_None_")
            lines.append("")
            return

        for idx, it in enumerate(items, start=1):
            lines.append(f"### {title[:-1]} {idx}")
            lines.append("")
            lines.append(f"- kind: `{it.get('kind')}`")
            if it.get("rule_id"):
                lines.append(f"- rule_id: `{it.get('rule_id')}`")
            if it.get("instance_path"):
                lines.append(f"- instance_path: `{it.get('instance_path')}`")
            if it.get("schema_path"):
                lines.append(f"- schema_path: `{it.get('schema_path')}`")
            lines.append(f"- message: {it.get('message')}")
            lines.append("")
            details = it.get("details")
            if isinstance(details, dict) and details:
                lines.append("details:")
                lines.append("```json")
                lines.append(json.dumps(details, indent=2, ensure_ascii=False))
                lines.append("```")
                lines.append("")

    render_items("Errors", report.get("errors", []))
    render_items("Warnings", report.get("warnings", []))

    return "\n".join(lines).rstrip() + "\n"