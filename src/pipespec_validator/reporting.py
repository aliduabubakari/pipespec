from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

import yaml

from .hints import generate_hints, hints_to_dict, llm_escalation_needed
from .models import ValidationErrorItem, ValidationResult
from .validator import load_schema, SCHEMA_VERSION


ReportFormat = Literal["json", "yaml", "md"]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _error_item_to_dict(e: ValidationErrorItem) -> dict[str, Any]:
    d = asdict(e)
    rule_id = None
    if isinstance(e.details, dict):
        rule_id = e.details.get("rule_id")
    d["rule_id"] = rule_id
    return d


def _result_block(result: ValidationResult) -> dict[str, Any]:
    """Shared helper: build errors/warnings/hints block from a ValidationResult."""
    hints = generate_hints(result.errors, result.warnings)
    return {
        "ok": result.ok,
        "summary": {
            "error_count": len(result.errors),
            "warning_count": len(result.warnings),
            "hint_count": len(hints),
            "llm_escalation_needed": llm_escalation_needed(hints),
        },
        "errors": [_error_item_to_dict(e) for e in result.errors],
        "warnings": [_error_item_to_dict(w) for w in result.warnings],
        "hints": hints_to_dict(hints),
    }


def make_report(
    result: ValidationResult,
    *,
    source_path: str | None = None,
    include_schema_id: bool = True,
    include_timestamp: bool = True,
    tool_name: str = "pipespec-validator",
    fix_actions: list[dict[str, Any]] | None = None,
    post_autofix_result: ValidationResult | None = None,
    rounds_run: int | None = None,
) -> dict[str, Any]:
    schema = load_schema() if include_schema_id else {}
    schema_id = schema.get("$id") if include_schema_id else None

    # Build main result block (errors + warnings + hints)
    main_block = _result_block(result)

    report: dict[str, Any] = {
        "report_version": "1.0",
        "tool": tool_name,
        "generated_at": _utc_now_iso() if include_timestamp else None,
        "pipespec_schema_version": SCHEMA_VERSION,
        "pipespec_schema_id": schema_id,
        "source_path": source_path,
        "ok": result.ok,
        "summary": main_block["summary"],
        "errors": main_block["errors"],
        "warnings": main_block["warnings"],
        "hints": main_block["hints"],
    }

    if fix_actions is not None:
        report["fix_actions"] = fix_actions
        if rounds_run is not None:
            report["autofix_rounds"] = rounds_run

    if post_autofix_result is not None:
        post_block = _result_block(post_autofix_result)
        report["post_autofix"] = post_block

    # Strip None values for neatness
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
        ext = out.suffix.lower().lstrip(".")
        if ext in {"yaml", "yml"}:
            fmt = "yaml"
        elif ext == "md":
            fmt = "md"
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
    lines: list[str] = []

    def h(level: int, text: str) -> None:
        lines.append(f"{'#' * level} {text}")
        lines.append("")

    def p(text: str) -> None:
        lines.append(text)
        lines.append("")

    def li(text: str) -> None:
        lines.append(f"- {text}")

    def code_block(content: str, lang: str = "json") -> None:
        lines.append(f"```{lang}")
        lines.append(content)
        lines.append("```")
        lines.append("")

    h(1, "PipeSpec Validation Report")
    li(f"Tool: `{report.get('tool')}`")
    li(f"Generated at: `{report.get('generated_at', '')}`")
    li(f"Schema version: `{report.get('pipespec_schema_version')}`")
    li(f"Source: `{report.get('source_path', '')}`")
    li(f"Valid: `{report.get('ok')}`")
    lines.append("")

    summary = report.get("summary", {})
    h(2, "Summary")
    li(f"Errors: `{summary.get('error_count', 0)}`")
    li(f"Warnings: `{summary.get('warning_count', 0)}`")
    li(f"Hints: `{summary.get('hint_count', 0)}`")
    li(f"LLM escalation needed: `{summary.get('llm_escalation_needed', False)}`")
    lines.append("")

    def render_errors_warnings(title: str, items: list[dict[str, Any]]) -> None:
        h(2, title)
        if not items:
            p("_None_")
            return
        for idx, it in enumerate(items, 1):
            h(3, f"{title[:-1]} {idx}")
            li(f"kind: `{it.get('kind')}`")
            if it.get("rule_id"):
                li(f"rule_id: `{it.get('rule_id')}`")
            if it.get("instance_path"):
                li(f"instance_path: `{it.get('instance_path')}`")
            if it.get("schema_path"):
                li(f"schema_path: `{it.get('schema_path')}`")
            li(f"message: {it.get('message')}")
            lines.append("")
            details = it.get("details")
            if isinstance(details, dict) and details:
                p("details:")
                code_block(json.dumps(details, indent=2, ensure_ascii=False))

    def render_hints(hints: list[dict[str, Any]]) -> None:
        h(2, "Hints")
        if not hints:
            p("_None_")
            return
        for idx, h_item in enumerate(hints, 1):
            h(3, f"Hint {idx}: {h_item.get('code', '')}")
            tier = h_item.get("tier", "")
            severity = h_item.get("severity", "")
            badge = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(severity, "")
            li(f"tier: `{tier}` | severity: {badge} `{severity}`")
            li(f"message: {h_item.get('message')}")
            li(f"paths: `{', '.join(h_item.get('paths', []))}`")
            lines.append("")
            action = h_item.get("suggested_action")
            if action:
                p(f"**Suggested action:** {action}")
            details = h_item.get("details")
            if isinstance(details, dict) and details:
                code_block(json.dumps(details, indent=2, ensure_ascii=False))

    def render_fix_actions(actions: list[dict[str, Any]]) -> None:
        h(2, "AutoFix Actions Applied")
        if not actions:
            p("_None_")
            return
        for idx, a in enumerate(actions, 1):
            h(3, f"Action {idx}: `{a.get('code')}`")
            sev = a.get("severity", "info")
            badge = "⚠️" if sev == "warning" else "✅"
            li(f"severity: {badge} `{sev}`")
            li(f"path: `{a.get('path')}`")
            li(f"message: {a.get('message')}")
            lines.append("")
            details = a.get("details")
            if isinstance(details, dict) and details:
                code_block(json.dumps(details, indent=2, ensure_ascii=False))

    render_errors_warnings("Errors", report.get("errors", []))
    render_errors_warnings("Warnings", report.get("warnings", []))
    render_hints(report.get("hints", []))

    fix_actions = report.get("fix_actions")
    if fix_actions is not None:
        rounds = report.get("autofix_rounds")
        if rounds:
            h(2, f"AutoFix Actions Applied ({rounds} round(s))")
        else:
            h(2, "AutoFix Actions Applied")
        if not fix_actions:
            p("_None_")
        else:
            for idx, a in enumerate(fix_actions, 1):
                h(3, f"Action {idx}: `{a.get('code')}`")
                sev = a.get("severity", "info")
                badge = "⚠️" if sev == "warning" else "✅"
                li(f"severity: {badge} `{sev}`")
                li(f"path: `{a.get('path')}`")
                li(f"message: {a.get('message')}")
                lines.append("")
                details = a.get("details")
                if isinstance(details, dict) and details:
                    code_block(json.dumps(details, indent=2, ensure_ascii=False))

    post_autofix = report.get("post_autofix")
    if post_autofix:
        h(2, "Post-AutoFix Validation")
        li(f"Valid: `{post_autofix.get('ok')}`")
        post_sum = post_autofix.get("summary", {})
        li(f"Errors: `{post_sum.get('error_count', 0)}`")
        li(f"Warnings: `{post_sum.get('warning_count', 0)}`")
        li(f"Hints: `{post_sum.get('hint_count', 0)}`")
        li(f"LLM escalation needed: `{post_sum.get('llm_escalation_needed', False)}`")
        lines.append("")
        render_errors_warnings("Post-AutoFix Errors", post_autofix.get("errors", []))
        render_hints(post_autofix.get("hints", []))

    return "\n".join(lines).rstrip() + "\n"