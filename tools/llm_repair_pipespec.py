#!/usr/bin/env python3
"""
tools/llm_repair_pipespec.py
-----------------------------
LLM-based repair for PipeSpec content gaps that deterministic AutoFix cannot fill.

ONLY run this AFTER `pipespec-validate --autofix` has been applied and the
validation report confirms `post_autofix.summary.llm_escalation_needed: true`.

What this tool does
-------------------
1. Loads the post-autofix PipeSpec (partially valid, with content gaps).
2. Loads the validation report to extract exactly which hints need LLM repair.
3. Loads the original pipeline description (plain text).
4. For each content-tier hint, builds a minimal, surgical prompt asking the LLM
   to fill ONLY the missing fields at the flagged paths.
5. Merges the LLM patch into the document (never overwrites correct fields).
6. Validates the result. If still invalid, retries up to --attempts times.
7. Writes the repaired document only if it has FEWER schema errors than the input.
8. Appends a `llm_repair` block to the validation report.

What this tool does NOT do
--------------------------
- It does not rewrite the whole document.
- It does not touch fields not mentioned in the hints.
- It does not fabricate data when the description is insufficient — it leaves
  fields as null and reports them as unresolved.
- It does not write output if the repair made things worse.

Usage
-----
    python tools/llm_repair_pipespec.py \\
        --pipespec /tmp/fixed.yaml \\
        --description Pipeline_Description_Dataset/my_pipeline.txt \\
        --report /tmp/fix_report.json \\
        --out /tmp/repaired.yaml \\
        --consent \\
        [--model Qwen/Qwen2.5-Coder-32B-Instruct] \\
        [--base-url https://api.deepinfra.com/v1/openai] \\
        [--api-key $PIPESPEC_LLM_API_KEY] \\
        [--attempts 3] \\
        [--debug]

Environment variables (fallbacks for --api-key)
-----------------------------------------------
    PIPESPEC_LLM_API_KEY   preferred
    OPENAI_API_KEY         standard fallback

Dependencies (tools/ only, not in core library)
-----------------------------------------------
    pip install openai pyyaml rich
    (pipespec_validator must be installed or on PYTHONPATH)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import textwrap
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Ensure the src/ layout is importable when run directly from repo root
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

import yaml

try:
    from openai import OpenAI
except ImportError:
    print("ERROR: 'openai' package not installed. Run: pip install openai", file=sys.stderr)
    sys.exit(1)

try:
    import typer
    from rich.console import Console
    from rich.table import Table
except ImportError:
    print("ERROR: 'typer' and 'rich' packages not installed. Run: pip install typer rich", file=sys.stderr)
    sys.exit(1)

from pipespec_validator import (
    generate_hints,
    validate_dict,
)
from pipespec_validator.hints import Hint, llm_escalation_needed
from pipespec_validator.io_utils import load_doc, write_doc
from pipespec_validator.validator import SCHEMA_VERSION, load_schema


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "Qwen/Qwen2.5-Coder-32B-Instruct"
DEFAULT_BASE_URL = "https://api.deepinfra.com/v1/openai"
DEFAULT_ATTEMPTS = 3
MAX_DESCRIPTION_CHARS = 8_000   # Truncate long descriptions to keep prompt focused
MAX_DOC_CHARS = 12_000          # Truncate doc preview if enormous

VALID_CATEGORIES = [
    "Extractor",
    "Transformer",
    "Loader",
    "Reconciliator",
    "QualityCheck",
    "FeatureEngineering",
    "ModelTraining",
    "ModelEvaluation",
    "ModelInference",
    "Notifier",
    "Sensor",
    "Custom",
]
VALID_EXECUTOR_TYPES = ["python", "http", "sql", "bash", "email", "docker", "custom"]

console = Console()
log = logging.getLogger("pipespec.llm_repair")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RepairAction:
    hint_code: str
    path: str
    fields_requested: list[str]
    fields_filled: list[str]
    fields_unresolved: list[str]
    patch_applied: dict[str, Any]
    attempt: int
    success: bool
    reason: str | None = None


@dataclass
class RepairResult:
    pipespec_path: str
    description_path: str
    original_error_count: int
    final_error_count: int
    improved: bool
    actions: list[RepairAction]
    attempts_total: int
    output_path: str | None
    generated_at: str = ""

    def __post_init__(self) -> None:
        if not self.generated_at:
            self.generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _schema_enum_summary() -> str:
    """Inline schema reference for fields the LLM commonly gets wrong."""
    return textwrap.dedent(f"""
        ENUM CONSTRAINTS (you must use EXACTLY these values):
        - component.category: one of {VALID_CATEGORIES}
        - component.executor_type: one of {VALID_EXECUTOR_TYPES}
        - flow_structure.pattern: one of [sequential, parallel, dag, conditional, loop]
        - node.kind: one of [Task, Group, Branch, Sensor, ParallelGroup]
        - edge.edge_type: one of [success, failure, always, conditional]
        - io_spec[].direction: one of [input, output]
        - io_spec[].kind: one of [file, table, api, object, stream, features, model, metrics, predictions, embedding]
        - common ML formats: pickle, pkl, joblib, onnx, pmml, mlflow, skops, npy, npz
    """).strip()


def _build_component_patch_prompt(
    description: str,
    component_obj: dict[str, Any],
    hint_raw: dict[str, Any],
    missing_fields: list[str],
    attempt: int,
) -> str:
    """
    Prompt for filling missing required fields on one or more components.
    Receives the component object directly (not a wrapper dict) to avoid
    YAML anchors from duplicate object references.
    """
    doc_yaml = yaml.safe_dump(component_obj, sort_keys=False, allow_unicode=True)
    if len(doc_yaml) > MAX_DOC_CHARS:
        doc_yaml = doc_yaml[:MAX_DOC_CHARS] + "\n... (truncated)"

    fields_str = ", ".join(missing_fields)
    paths = hint_raw.get("paths") or [""]
    path = paths[0] if paths else ""

    return textwrap.dedent(f"""
        You are a PipeSpec document repair assistant. A PipeSpec is a structured
        JSON/YAML document that describes a data pipeline.

        TASK
        ----
        The PipeSpec document below is partially valid. It is missing these required
        fields at path {path!r}: {fields_str}

        You must read the PIPELINE DESCRIPTION and determine the correct values for
        ONLY the missing fields. Do not change anything else.

        {_schema_enum_summary()}

        PIPELINE DESCRIPTION
        --------------------
        {description[:MAX_DESCRIPTION_CHARS]}

        CURRENT PIPESPEC (partial, may have stubs)
        ------------------------------------------
        {doc_yaml}

        INSTRUCTIONS
        ------------
        Return a JSON object with ONLY the missing fields for the object at {path!r}.
        - If you cannot determine a value from the description, use null.
        - Do NOT include fields that are already present.
        - Do NOT wrap in markdown code fences.
        - Do NOT add explanation text.
        - Attempt {attempt} of {DEFAULT_ATTEMPTS}.

        REQUIRED OUTPUT FORMAT (example for missing category + executor_type):
        {{
          "category": "Extractor",
          "executor_type": "python"
        }}

        YOUR RESPONSE (JSON only):
    """).strip()


def _build_entry_points_prompt(
    description: str,
    doc_snippet: dict[str, Any],
    attempt: int,
) -> str:
    """
    Prompt for determining the correct entry_points from the pipeline description.
    """
    node_ids = list((doc_snippet.get("flow_structure") or {}).get("nodes", {}).keys())
    doc_yaml = yaml.safe_dump(doc_snippet.get("flow_structure", {}), sort_keys=False)
    if len(doc_yaml) > MAX_DOC_CHARS:
        doc_yaml = doc_yaml[:MAX_DOC_CHARS] + "\n... (truncated)"

    return textwrap.dedent(f"""
        You are a PipeSpec document repair assistant.

        TASK
        ----
        The PipeSpec flow_structure.entry_points list is empty. It must contain the
        id(s) of the node(s) where pipeline execution begins (nodes with no upstream
        dependencies).

        Available node ids: {node_ids}

        PIPELINE DESCRIPTION
        --------------------
        {description[:MAX_DESCRIPTION_CHARS]}

        CURRENT flow_structure
        ----------------------
        {doc_yaml}

        INSTRUCTIONS
        ------------
        Return a JSON object with entry_points as a list of node id strings.
        - Only include nodes that have NO incoming edges (true starting points).
        - If you cannot determine the entry point from the description, return the
          node that appears most likely to be first based on its name/id.
        - Do NOT wrap in markdown code fences.
        - Do NOT add explanation text.
        - Attempt {attempt} of {DEFAULT_ATTEMPTS}.

        REQUIRED OUTPUT FORMAT:
        {{
          "entry_points": ["node_id_here"]
        }}

        YOUR RESPONSE (JSON only):
    """).strip()


def _build_generic_patch_prompt(
    description: str,
    doc_snippet: dict[str, Any],
    hint_raw: dict[str, Any],
    missing_fields: list[str],
    attempt: int,
) -> str:
    """Fallback prompt for any other content hint not covered by a specific builder."""
    doc_yaml = yaml.safe_dump(doc_snippet, sort_keys=False, allow_unicode=True)
    paths = hint_raw.get("paths") or [""]
    path = paths[0] if paths else ""
    fields_str = ", ".join(missing_fields)

    return textwrap.dedent(f"""
        You are a PipeSpec document repair assistant.

        TASK
        ----
        The PipeSpec document below is missing required content at {path!r}: {fields_str}

        {_schema_enum_summary()}

        PIPELINE DESCRIPTION
        --------------------
        {description[:MAX_DESCRIPTION_CHARS]}

        CURRENT PIPESPEC (partial)
        --------------------------
        {doc_yaml[:MAX_DOC_CHARS]}

        INSTRUCTIONS
        ------------
        Return a JSON object containing ONLY the missing fields for {path!r}.
        - Use null for values you cannot determine from the description.
        - Do NOT wrap in markdown code fences.
        - Do NOT add explanation text.
        - Attempt {attempt} of {DEFAULT_ATTEMPTS}.

        YOUR RESPONSE (JSON only):
    """).strip()


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def _call_llm(
    client: OpenAI,
    model: str,
    prompt: str,
    *,
    debug: bool = False,
) -> dict[str, Any] | None:
    """
    Call the LLM and parse the response as JSON.
    Returns the parsed dict, or None if parsing fails.
    """
    if debug:
        log.debug("--- PROMPT ---\n%s\n--- END PROMPT ---", prompt)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,        # Deterministic as possible for repair
            max_tokens=1024,
            response_format={"type": "json_object"} if _model_supports_json_mode(model) else None,
        )
    except Exception as e:
        log.error("LLM API call failed: %s", e)
        return None

    raw = response.choices[0].message.content or ""
    if debug:
        log.debug("--- RAW RESPONSE ---\n%s\n--- END RESPONSE ---", raw)

    return _parse_json_response(raw)


def _model_supports_json_mode(model: str) -> bool:
    """
    Some models support response_format=json_object, others don't.
    Conservative: only enable for known-good models.
    """
    known_json_mode = {"gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"}
    return any(m in model.lower() for m in known_json_mode)


def _parse_json_response(raw: str) -> dict[str, Any] | None:
    """
    Strip reasoning noise and parse JSON from LLM response.

    Handles:
    - Qwen <think>...</think> reasoning blocks (emitted before JSON output)
    - Markdown ```json ... ``` or ``` ... ``` fences
    - Leading/trailing whitespace
    """
    import re
    text = raw.strip()

    # Strip <think>...</think> blocks (Qwen reasoning-model output)
    # These appear before the actual JSON response
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # Strip ```json ... ``` or ``` ... ``` fences
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:] if lines[0].startswith("```") else lines
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        text = "\n".join(inner).strip()

    if not text:
        log.warning("LLM response was empty after stripping reasoning blocks")
        return None

    try:
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            log.warning("LLM returned non-dict JSON: %s", type(parsed))
            return None
        return parsed
    except json.JSONDecodeError as e:
        log.warning("Failed to parse LLM response as JSON: %s\nRaw: %s", e, raw[:200])
        return None


# ---------------------------------------------------------------------------
# JSON Pointer helpers
# ---------------------------------------------------------------------------

def _pointer_get(doc: dict[str, Any], pointer: str) -> Any:
    """Retrieve value at a JSON Pointer path like /components/0."""
    if not pointer or pointer == "/":
        return doc
    parts = pointer.lstrip("/").split("/")
    current: Any = doc
    for part in parts:
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (IndexError, ValueError):
                return None
        else:
            return None
    return current


def _pointer_set(doc: dict[str, Any], pointer: str, updates: dict[str, Any]) -> dict[str, Any]:
    """
    Merge `updates` into the object at `pointer` in `doc`.
    Only sets keys that are missing or null in the target — never overwrites valid data.
    Returns a new doc (deep-copies the target object).
    """
    import copy
    doc = copy.deepcopy(doc)

    if not pointer or pointer == "/":
        target = doc
    else:
        parts = pointer.lstrip("/").split("/")
        target = doc
        for part in parts:
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(target, dict):
                target = target[part]
            elif isinstance(target, list):
                target = target[int(part)]

    if not isinstance(target, dict):
        log.warning("Target at %r is not a dict, cannot patch: %s", pointer, type(target))
        return doc

    filled: list[str] = []
    for key, value in updates.items():
        # Only set if missing or previously null/stub
        existing = target.get(key)
        if existing is None or existing == "unknown" or existing == []:
            if value is not None:
                target[key] = value
                filled.append(key)
                log.debug("Patched %s/%s = %r", pointer, key, value)
            # If value is null, leave existing as-is (LLM couldn't determine it)

    return doc


# ---------------------------------------------------------------------------
# Repair orchestration
# ---------------------------------------------------------------------------

def _hints_needing_repair(report: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extract content-tier high-severity hints from the post_autofix section
    of a validation report. Falls back to top-level hints if no post_autofix.
    """
    section = report.get("post_autofix") or report
    hints_raw = section.get("hints", [])
    return [
        h for h in hints_raw
        if h.get("tier") == "content" and h.get("severity") == "high"
    ]


def _repair_one_hint(
    client: OpenAI,
    model: str,
    doc: dict[str, Any],
    hint_raw: dict[str, Any],
    description: str,
    *,
    attempts: int,
    debug: bool,
) -> tuple[dict[str, Any], RepairAction]:
    """
    Attempt to repair a single hint. Returns (updated_doc, action).
    """
    code = hint_raw.get("code", "")
    path = (hint_raw.get("paths") or [""])[0]
    missing_fields: list[str] = (hint_raw.get("details") or {}).get("missing_fields", [])

    # Resolve the current object at the path for context
    context_obj = _pointer_get(doc, path) or {}
    # Include parent for context (components array etc.)
    path_parts = path.lstrip("/").split("/")
    parent_path = "/" + "/".join(path_parts[:-1]) if len(path_parts) > 1 else ""
    parent_obj = _pointer_get(doc, parent_path) if parent_path else doc
    doc_snippet = {
        "description_context": parent_obj,
        "target_object": context_obj,
    }

    last_patch: dict[str, Any] = {}
    last_attempt = 0

    for attempt in range(1, attempts + 1):
        last_attempt = attempt

        # Choose prompt builder based on hint code
        if code == "HINT-EMPTY-ENTRY-POINTS":
            prompt = _build_entry_points_prompt(description, doc, attempt)
            target_path = "/flow_structure"
        elif code == "HINT-MISSING-REQUIRED" and "/components/" in path:
            prompt = _build_component_patch_prompt(
                description, context_obj, hint_raw, missing_fields or [], attempt
            )
            target_path = path
        else:
            prompt = _build_generic_patch_prompt(
                description, doc_snippet, hint_raw, missing_fields or [], attempt
            )
            target_path = path

        patch = _call_llm(client, model, prompt, debug=debug)

        if patch is None:
            log.warning("[%s] attempt %d: LLM returned unparseable response", code, attempt)
            continue

        last_patch = patch

        # Validate the patch makes sense before applying
        if not patch:
            log.warning("[%s] attempt %d: LLM returned empty patch", code, attempt)
            continue

        # Apply patch to doc
        updated_doc = _pointer_set(doc, target_path, patch)

        # Check if this specific hint's fields are now satisfied
        result = validate_dict(updated_doc)
        remaining_at_path = [
            e for e in result.errors
            if e.instance_path.startswith(path) or e.instance_path == path
        ]

        if not remaining_at_path:
            # Hint resolved
            filled = [f for f in (missing_fields or list(patch.keys())) if f in patch and patch[f] is not None]
            unresolved = [f for f in (missing_fields or []) if f not in patch or patch[f] is None]
            return updated_doc, RepairAction(
                hint_code=code,
                path=path,
                fields_requested=missing_fields or list(patch.keys()),
                fields_filled=filled,
                fields_unresolved=unresolved,
                patch_applied=patch,
                attempt=attempt,
                success=True,
            )

        log.debug(
            "[%s] attempt %d: %d error(s) remain at path %r, retrying",
            code, attempt, len(remaining_at_path), path,
        )
        # Feed the doc with the partial patch into the next attempt
        doc = updated_doc

    # Exhausted attempts — apply whatever we got and mark as partial
    if last_patch:
        doc = _pointer_set(doc, target_path if code != "HINT-EMPTY-ENTRY-POINTS" else "/flow_structure", last_patch)
        filled = [f for f in (missing_fields or list(last_patch.keys())) if f in last_patch and last_patch[f] is not None]
        unresolved = [f for f in (missing_fields or []) if f not in last_patch or last_patch[f] is None]
    else:
        filled = []
        unresolved = missing_fields or []

    return doc, RepairAction(
        hint_code=code,
        path=path,
        fields_requested=missing_fields or [],
        fields_filled=filled,
        fields_unresolved=unresolved,
        patch_applied=last_patch,
        attempt=last_attempt,
        success=False,
        reason=f"Exhausted {attempts} attempt(s) without fully resolving errors at {path!r}.",
    )


def repair(
    pipespec_path: Path,
    description_path: Path,
    report_path: Path | None,
    out_path: Path,
    *,
    model: str,
    base_url: str,
    api_key: str,
    attempts: int,
    debug: bool,
) -> RepairResult:
    """Core repair logic. Returns a RepairResult regardless of outcome."""

    # ── Load inputs ──────────────────────────────────────────────────────────

    doc, fmt = load_doc(pipespec_path)
    description = description_path.read_text(encoding="utf-8").strip()

    report: dict[str, Any] = {}
    if report_path and report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))

    # ── Initial validation ───────────────────────────────────────────────────

    initial_result = validate_dict(doc)
    original_error_count = len(initial_result.errors)

    if original_error_count == 0:
        console.print("[green]✓ Document is already schema-valid. Nothing to repair.[/green]")
        return RepairResult(
            pipespec_path=str(pipespec_path),
            description_path=str(description_path),
            original_error_count=0,
            final_error_count=0,
            improved=True,
            actions=[],
            attempts_total=0,
            output_path=None,
        )

    # ── Extract hints ────────────────────────────────────────────────────────

    hints_to_fix = _hints_needing_repair(report)

    # If no report was provided (or report has no hints), derive hints from current validation
    if not hints_to_fix:
        log.info("No hints in report — deriving from current validation errors.")
        live_hints = generate_hints(initial_result.errors, initial_result.warnings)
        hints_to_fix = [
            asdict(h) for h in live_hints
            if h.tier == "content" and h.severity == "high"
        ]

    if not hints_to_fix:
        console.print(
            "[yellow]No content-tier hints found. Nothing for the LLM to repair.\n"
            "Remaining errors may require manual review.[/yellow]"
        )
        return RepairResult(
            pipespec_path=str(pipespec_path),
            description_path=str(description_path),
            original_error_count=original_error_count,
            final_error_count=original_error_count,
            improved=False,
            actions=[],
            attempts_total=0,
            output_path=None,
        )

    console.print(
        f"\n[bold]LLM Repair[/bold]  model=[cyan]{model}[/cyan]  "
        f"hints=[yellow]{len(hints_to_fix)}[/yellow]  "
        f"attempts=[dim]{attempts}[/dim]"
    )

    # ── Set up OpenAI client ─────────────────────────────────────────────────

    client = OpenAI(api_key=api_key, base_url=base_url)

    # ── Repair loop — one hint at a time ─────────────────────────────────────

    actions: list[RepairAction] = []
    current_doc = doc
    attempts_total = 0

    for hint_raw in hints_to_fix:
        code = hint_raw.get("code", "?")
        path = (hint_raw.get("paths") or ["?"])[0]
        fields = (hint_raw.get("details") or {}).get("missing_fields", [])

        console.print(f"\n  → Repairing [cyan]{code}[/cyan] at [dim]{path!r}[/dim]"
                      + (f"  fields: {fields}" if fields else ""))

        current_doc, action = _repair_one_hint(
            client, model, current_doc, hint_raw, description,
            attempts=attempts, debug=debug,
        )
        attempts_total += action.attempt
        actions.append(action)

        status = "[green]✓[/green]" if action.success else "[yellow]~[/yellow]"
        console.print(
            f"    {status} filled={action.fields_filled}  "
            f"unresolved={action.fields_unresolved}  "
            f"(attempt {action.attempt}/{attempts})"
        )

    # ── Final validation ─────────────────────────────────────────────────────

    final_result = validate_dict(current_doc)
    final_error_count = len(final_result.errors)
    improved = final_error_count < original_error_count

    console.print(f"\n  Schema errors: [bold]{original_error_count}[/bold] → [bold]{'[green]' if improved else '[red]'}{final_error_count}[/{'green]' if improved else 'red]'}[/bold]")

    # ── Write output only if improved ────────────────────────────────────────

    output_path: str | None = None

    if improved:
        # Preserve input format by default; respect out_path extension
        out_fmt = fmt
        if out_path.suffix.lower() == ".json":
            out_fmt = "json"
        elif out_path.suffix.lower() in {".yaml", ".yml"}:
            out_fmt = "yaml"

        write_doc(current_doc, out_path, out_fmt)
        output_path = str(out_path)
        console.print(f"  [green]Wrote repaired file:[/green] {out_path}")
    else:
        console.print(
            "  [red]Repair did not improve schema validity — output not written.[/red]\n"
            "  Review the unresolved hints and consider manual correction."
        )

    return RepairResult(
        pipespec_path=str(pipespec_path),
        description_path=str(description_path),
        original_error_count=original_error_count,
        final_error_count=final_error_count,
        improved=improved,
        actions=actions,
        attempts_total=attempts_total,
        output_path=output_path,
    )


# ---------------------------------------------------------------------------
# Report update
# ---------------------------------------------------------------------------

def _append_repair_to_report(
    report_path: Path,
    repair_result: RepairResult,
) -> None:
    """Append the llm_repair block to an existing JSON report in-place."""
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        report = {}

    report["llm_repair"] = {
        "generated_at": repair_result.generated_at,
        "model": repair_result.__dict__.get("model", ""),
        "original_error_count": repair_result.original_error_count,
        "final_error_count": repair_result.final_error_count,
        "improved": repair_result.improved,
        "output_path": repair_result.output_path,
        "attempts_total": repair_result.attempts_total,
        "actions": [asdict(a) for a in repair_result.actions],
    }

    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    console.print(f"  [dim]Updated report:[/dim] {report_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

app = typer.Typer(add_completion=False)


@app.command()
def main(
    pipespec: Path = typer.Option(
        ..., "--pipespec", help="Path to the post-autofix PipeSpec file (json/yaml).",
        exists=True,
    ),
    description: Path = typer.Option(
        ..., "--description", help="Path to the original pipeline description (plain text).",
        exists=True,
    ),
    out: Path = typer.Option(
        ..., "--out", help="Output path for the repaired PipeSpec (json/yaml).",
    ),
    report: Path | None = typer.Option(
        None, "--report",
        help="Path to the validation report JSON (output of pipespec-validate --report). "
             "Used to extract hints. If omitted, hints are re-derived from live validation.",
    ),
    consent: bool = typer.Option(
        False, "--consent",
        help="Required: explicitly consent to LLM processing of your pipeline description.",
    ),
    model: str = typer.Option(
        DEFAULT_MODEL, "--model",
        help="LLM model identifier.",
    ),
    base_url: str = typer.Option(
        DEFAULT_BASE_URL, "--base-url",
        help="OpenAI-compatible API base URL.",
    ),
    api_key: str | None = typer.Option(
        None, "--api-key",
        help="API key. Falls back to PIPESPEC_LLM_API_KEY then OPENAI_API_KEY env vars.",
    ),
    attempts: int = typer.Option(
        DEFAULT_ATTEMPTS, "--attempts",
        help="Max LLM attempts per hint before moving on.",
        min=1, max=10,
    ),
    debug: bool = typer.Option(
        False, "--debug",
        help="Print full prompts and raw LLM responses.",
    ),
) -> None:
    """
    LLM-based repair for PipeSpec content gaps that AutoFix cannot fill.

    Run AFTER: pipespec-validate --autofix --report <report.json>
    """
    # ── Logging ──────────────────────────────────────────────────────────────
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    # ── Consent gate ─────────────────────────────────────────────────────────
    if not consent:
        console.print(
            "[red]ERROR:[/red] --consent is required.\n\n"
            "This tool sends your pipeline description and PipeSpec to an external\n"
            "LLM API. Pass --consent to confirm you accept this.\n\n"
            "If you are using a self-hosted model, your data stays on your\n"
            "infrastructure, but --consent is still required as an explicit\n"
            "acknowledgement that LLM output may contain inaccuracies.",
            style="bold",
        )
        raise typer.Exit(code=1)

    # ── API key resolution ───────────────────────────────────────────────────
    resolved_key = (
        api_key
        or os.environ.get("PIPESPEC_LLM_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    if not resolved_key:
        console.print(
            "[red]ERROR:[/red] No API key found.\n"
            "Set PIPESPEC_LLM_API_KEY or OPENAI_API_KEY, or pass --api-key."
        )
        raise typer.Exit(code=1)

    # ── Guard: don't overwrite the input file ────────────────────────────────
    if out.resolve() == pipespec.resolve():
        console.print(
            "[red]ERROR:[/red] --out must differ from --pipespec.\n"
            "The tool writes a new file; it does not edit in-place."
        )
        raise typer.Exit(code=1)

    # ── Run repair ───────────────────────────────────────────────────────────
    result = repair(
        pipespec_path=pipespec,
        description_path=description,
        report_path=report,
        out_path=out,
        model=model,
        base_url=base_url,
        api_key=resolved_key,
        attempts=attempts,
        debug=debug,
    )

    # Store model on result for report
    result.__dict__["model"] = model

    # ── Summary table ────────────────────────────────────────────────────────
    if result.actions:
        t = Table(title="LLM Repair Actions", show_lines=True)
        t.add_column("hint", style="cyan")
        t.add_column("path")
        t.add_column("filled")
        t.add_column("unresolved")
        t.add_column("ok", width=4)
        for a in result.actions:
            t.add_row(
                a.hint_code,
                a.path,
                ", ".join(a.fields_filled) or "-",
                ", ".join(a.fields_unresolved) or "-",
                "[green]✓[/green]" if a.success else "[yellow]~[/yellow]",
            )
        console.print(t)

    # ── Update report ─────────────────────────────────────────────────────────
    if report and report.exists():
        _append_repair_to_report(report, result)

    # ── Exit code ────────────────────────────────────────────────────────────
    # Exit 0 if we improved (even partially). Exit 2 if no improvement at all.
    raise typer.Exit(code=0 if result.improved else 2)


if __name__ == "__main__":
    app()
