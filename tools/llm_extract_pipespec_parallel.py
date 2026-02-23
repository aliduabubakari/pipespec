from __future__ import annotations

"""
llm_extract_pipespec_parallel.py
=================================
Parallel PipeSpec v1 extraction.

Strategy
--------
Instead of one giant prompt (which causes smaller models like Qwen-32B to return
error JSON), we split the PipeSpec into independent *segments* and call the LLM
concurrently for each.  A lightweight merge + normalization pass stitches the
results together, and the existing validator gives us schema-level feedback for
any repair retries.

Segment dependency graph
------------------------

  [Wave 1 – fully parallel]
    A) metadata + pipeline_summary
    B) components  (most complex; isolated so it gets full context window)
    C) parameters  (independent)
    D) integrations (independent)

  [Wave 2 – needs component IDs from B]
    E) flow_structure

  [Normalize → Merge → Validate → segment-level repair loop]

Key design decisions
--------------------
- Parameters are extracted as natural key→value pairs and then *programmatically*
  normalized into ParameterSpec objects {description, type, default, required}.
  This avoids asking the LLM to output a deeply nested structure it consistently
  gets wrong.

- Repair is segment-scoped: errors are grouped by top-level JSON path key, and
  only that segment (~1/5 of the doc) is sent back to the model for correction.
  This keeps payloads small enough for Qwen-32B to accept without hitting the
  "Invalid JSON input" error it returns when the prompt is too large.
"""

import argparse
import json
import logging
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipespec_validator import validate_dict

LOGGER = logging.getLogger("pipespec.parallel_extract")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(verbose: bool, debug: bool) -> None:
    level = logging.WARNING
    if verbose:
        level = logging.INFO
    if debug:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


# ---------------------------------------------------------------------------
# OpenAI-compatible JSON-mode call
# ---------------------------------------------------------------------------

def call_json_mode(
    *,
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    temperature: float,
    segment_name: str = "?",
) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)
    LOGGER.info("[%s] → calling model=%s", segment_name, model)

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=max_tokens,
        temperature=temperature,
    )
    content = resp.choices[0].message.content or ""
    LOGGER.info("[%s] ← received %d chars", segment_name, len(content))
    LOGGER.debug("[%s] preview: %r", segment_name, content[:300])
    return content


# ---------------------------------------------------------------------------
# Segment system prompts (each is small and focused)
# ---------------------------------------------------------------------------

_COMMON_RULES = """\
Hard rules:
- Output MUST be a single valid JSON object.
- Do NOT output markdown, code fences, or extra commentary.
- Do NOT invent details not found in the description; use null or [] or {}.
"""

_ENUM_HINTS = """\
Enum reference (use ONLY these values):
  executor_type       : python | http | sql | bash | email | docker | custom
  category            : Extractor | Transformer | Loader | Reconciliator | QualityCheck | Notifier | Sensor | Custom
  flow_pattern/pattern: sequential | parallel | dag | conditional | loop
  edge_type           : success | failure | always | conditional
  node.kind           : Task | Group | Branch | Sensor | ParallelGroup
  upstream_policy.type: all_success | none_failed | one_success | all_done
  complexity          : low | medium | high
  io direction        : input | output
  io kind             : file | table | api | object | stream
"""


# ── A: metadata + pipeline_summary ─────────────────────────────────────────

SYSTEM_META = f"""\
You are extracting the METADATA and PIPELINE_SUMMARY segment of a PipeSpec v1 JSON.
{_COMMON_RULES}
{_ENUM_HINTS}

Return ONLY a JSON object with exactly these two top-level keys:
  "metadata"         – provenance info (analysis_timestamp ISO8601, source_file null,
                       llm_provider null, llm_model null, schema_version "1.0")
  "pipeline_summary" – name (string), description (string),
                       flow_patterns (array of allowed pattern enums),
                       task_executors (array of allowed executor_type enums),
                       complexity (low|medium|high)
"""

def user_meta(desc: str) -> str:
    skeleton = {
        "metadata": {
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
            "source_file": None,
            "llm_provider": None,
            "llm_model": None,
            "schema_version": "1.0",
        },
        "pipeline_summary": {
            "name": "",
            "description": "",
            "flow_patterns": ["sequential"],
            "task_executors": ["python"],
            "complexity": "low",
        },
    }
    return (
        "Extract metadata and pipeline_summary from this pipeline description.\n"
        "Return ONLY the JSON object below with values filled in.\n\n"
        f"PIPELINE DESCRIPTION:\n{desc}\n\n"
        f"SHAPE TEMPLATE:\n{json.dumps(skeleton, indent=2)}\n"
    )


# ── B: components ───────────────────────────────────────────────────────────

SYSTEM_COMPONENTS = f"""\
You are extracting the COMPONENTS segment of a PipeSpec v1 JSON.
{_COMMON_RULES}
{_ENUM_HINTS}

Structure rules:
- "components" MUST be a JSON array (NOT an object/map).
- Each element MUST have: id, name, category, executor_type, io_spec.
- io_spec MUST be a non-empty array of {{name, direction, kind, format, path_pattern, connection_id}}.
- upstream_policy.type MUST be one of the allowed enum values.
- retry_policy MUST have: max_attempts (int ≥ 0), delay_seconds (int ≥ 0),
  exponential_backoff (bool), retry_on (array of strings).
- concurrency MUST have: supports_parallelism (bool), supports_dynamic_mapping (bool),
  map_over_param (string or null), max_parallel_instances (int or null).
- datasets MUST have: consumes (array of strings), produces (array of strings).
- connections is an array (may be empty).

Return ONLY a JSON object with a single top-level key: "components"
"""

def user_components(desc: str) -> str:
    skeleton_component = {
        "id": "snake_case_id",
        "name": "Human readable name",
        "category": "Extractor",
        "description": "",
        "executor_type": "python",
        "executor_config": None,
        "io_spec": [
            {
                "name": "io_name",
                "direction": "input",
                "kind": "api",
                "format": "json",
                "path_pattern": None,
                "connection_id": None,
            }
        ],
        "upstream_policy": {
            "type": "none_failed",
            "description": "",
            "timeout_seconds": None,
        },
        "retry_policy": {
            "max_attempts": 0,
            "delay_seconds": 0,
            "exponential_backoff": False,
            "retry_on": [],
        },
        "concurrency": {
            "supports_parallelism": False,
            "supports_dynamic_mapping": False,
            "map_over_param": None,
            "max_parallel_instances": None,
        },
        "connections": [],
        "datasets": {"consumes": [], "produces": []},
    }
    skeleton = {"components": [skeleton_component]}
    return (
        "Extract ALL pipeline components from the description. Each component = one task/step.\n"
        "Return ONLY a JSON object with key 'components' whose value is an array.\n\n"
        f"PIPELINE DESCRIPTION:\n{desc}\n\n"
        f"SHAPE TEMPLATE (one entry shown; produce as many as needed):\n"
        f"{json.dumps(skeleton, indent=2)}\n"
    )


# ── C: parameters  ──────────────────────────────────────────────────────────
#
# DESIGN NOTE: The PipeSpec schema requires every parameter value to be a
# ParameterSpec object {description, type, default, required, ...}.  Smaller
# models reliably emit flat key→value maps instead.  We intentionally ask for
# that natural form here and convert it programmatically in normalize_parameters().

SYSTEM_PARAMETERS = f"""\
You are extracting the PARAMETERS segment of a PipeSpec v1 JSON.
{_COMMON_RULES}

Output a JSON object with key "parameters" containing five sub-objects:
  pipeline    – pipeline-level settings (name, description, topology, execution model, etc.)
  schedule    – scheduling info (schedule_type, start_date, catchup, cron, etc.)
  execution   – execution settings (max_active_runs, timeout, etc.)
  components  – per-component settings keyed by component id
  environment – environment variable names and their values (use null for secrets)

IMPORTANT: Output each parameter VALUE DIRECTLY as a string, number, boolean, or array.
Do NOT wrap values in nested objects with "description", "type", "default" keys.

Correct example:
{{
  "parameters": {{
    "pipeline": {{"name": "my_pipeline", "description": "Does stuff"}},
    "schedule": {{"cron": "@daily", "catchup": false, "start_date": "2025-01-01"}},
    "execution": {{}},
    "components": {{"extract_data": {{"source_url": "https://api.example.com"}}}},
    "environment": {{"API_KEY": null, "DB_HOST": "localhost"}}
  }}
}}
"""

def user_parameters(desc: str) -> str:
    return (
        "Extract parameter values from this pipeline description.\n"
        "Return each parameter as a DIRECT value (string, number, boolean, array).\n"
        "Return ONLY a JSON object with top-level key 'parameters'.\n\n"
        f"PIPELINE DESCRIPTION:\n{desc}\n"
    )


# ── D: integrations ─────────────────────────────────────────────────────────

SYSTEM_INTEGRATIONS = f"""\
You are extracting the INTEGRATIONS segment of a PipeSpec v1 JSON.
{_COMMON_RULES}

Return ONLY a JSON object with exactly this top-level key: "integrations"
It must contain:
  "connections"  – array of integration connection objects
  "data_lineage" – object with keys: sources (array), sinks (array),
                   intermediate_datasets (array)

Each connection object MUST have:
  id (string), name (string),
  type (one of: api | database | filesystem | object_store | message_queue | smtp | other),
  config (object), authentication (object),
  used_by_components (array of component id strings),
  direction (one of: input | output | both),
  rate_limit (object or null), datasets (object or null)
"""

def user_integrations(desc: str) -> str:
    skeleton = {
        "integrations": {
            "connections": [
                {
                    "id": "conn_id",
                    "name": "Connection name",
                    "type": "api",
                    "config": {},
                    "authentication": {},
                    "used_by_components": [],
                    "direction": "input",
                    "rate_limit": None,
                    "datasets": None,
                }
            ],
            "data_lineage": {
                "sources": [],
                "sinks": [],
                "intermediate_datasets": [],
            },
        }
    }
    return (
        "Extract integration points from this pipeline description.\n"
        "Return ONLY the JSON object below, filled in.\n\n"
        f"PIPELINE DESCRIPTION:\n{desc}\n\n"
        f"SHAPE TEMPLATE:\n{json.dumps(skeleton, indent=2)}\n"
    )


# ── E: flow_structure (needs component IDs from B) ──────────────────────────

SYSTEM_FLOW = f"""\
You are extracting the FLOW_STRUCTURE segment of a PipeSpec v1 JSON.
{_COMMON_RULES}
{_ENUM_HINTS}

Structure rules:
- "nodes" MUST be a JSON OBJECT/MAP keyed by node id (NOT an array).
- For Task nodes: the node id MUST exactly equal the component id it represents.
  Do NOT add suffixes like _task, _node, _step.
- entry_points MUST reference node ids that exist in nodes.
- All edge "from"/"to" values MUST reference existing node ids.
- Each node MUST have: kind, component_type_id, upstream_policy ({{type, timeout_seconds}}),
  next_nodes (array), branch_config (null or object), sensor_config (null or object),
  parallel_config (null or object).

Return ONLY a JSON object with exactly this top-level key: "flow_structure"
"""

def user_flow(desc: str, component_ids: list[str]) -> str:
    nodes_skeleton: dict[str, Any] = {
        cid: {
            "kind": "Task",
            "component_type_id": cid,
            "upstream_policy": {"type": "all_success", "timeout_seconds": None},
            "next_nodes": [],
            "branch_config": None,
            "sensor_config": None,
            "parallel_config": None,
        }
        for cid in component_ids
    }
    skeleton = {
        "flow_structure": {
            "pattern": "sequential",
            "entry_points": [component_ids[0]] if component_ids else [],
            "nodes": nodes_skeleton,
            "edges": [
                {
                    "from": "first_id",
                    "to": "second_id",
                    "edge_type": "success",
                    "condition": None,
                    "metadata": {},
                }
            ],
        }
    }
    return (
        "Build the flow_structure for this pipeline.\n"
        f"Component IDs (use them EXACTLY as Task node ids): {component_ids}\n"
        "Fill in next_nodes and edges to reflect the real execution order.\n"
        "Return ONLY the JSON object below, correctly filled in.\n\n"
        f"PIPELINE DESCRIPTION:\n{desc}\n\n"
        f"SHAPE TEMPLATE:\n{json.dumps(skeleton, indent=2)}\n"
    )


# ---------------------------------------------------------------------------
# Parameter normalization
# ---------------------------------------------------------------------------
#
# Bridges the gap between the LLM's natural key→value output and the schema's
# required ParameterSpec objects — without any additional LLM call.

def _infer_type(value: Any) -> str:
    """Infer a ParameterSpec 'type' string from a Python value."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}", value):
        return "datetime"
    return "string"


def _to_param_spec(key: str, value: Any) -> dict[str, Any]:
    """Wrap a raw value into a ParameterSpec object."""
    # Already a valid ParameterSpec — pass through
    if (
        isinstance(value, dict)
        and {"description", "type", "default", "required"}.issubset(value.keys())
    ):
        return value
    return {
        "description": key.replace("_", " ").capitalize(),
        "type": _infer_type(value),
        "default": value,
        "required": False,
        "constraints": None,
    }


def normalize_parameters(raw_params: dict[str, Any]) -> dict[str, Any]:
    """
    Convert the LLM's natural key→value parameter output into the
    ParameterSpec-per-leaf structure required by the PipeSpec schema.

    LLM output (natural):
        {"pipeline": {"name": "my_dag"}, "schedule": {"catchup": false}, ...}

    Schema-compliant output:
        {"pipeline": {"name": {"description": "Name", "type": "string",
                               "default": "my_dag", "required": false, ...}},
         "schedule": {"catchup": {"description": "Catchup", "type": "boolean",
                                   "default": false, "required": false, ...}},
         ...}
    """
    result: dict[str, Any] = {
        "pipeline": {},
        "schedule": {},
        "execution": {},
        "components": {},
        "environment": {},
    }

    for top_key in ("pipeline", "schedule", "execution"):
        section = raw_params.get(top_key, {})
        if isinstance(section, dict):
            result[top_key] = {k: _to_param_spec(k, v) for k, v in section.items()}

    # components: two levels deep — component_id → {param_name → value}
    comp_section = raw_params.get("components", {})
    if isinstance(comp_section, dict):
        for comp_id, comp_params in comp_section.items():
            if isinstance(comp_params, dict):
                result["components"][comp_id] = {
                    k: _to_param_spec(k, v) for k, v in comp_params.items()
                }

    # environment: flat key → value
    env_section = raw_params.get("environment", {})
    if isinstance(env_section, dict):
        for k, v in env_section.items():
            spec = _to_param_spec(k, v)
            spec.setdefault("associated_component_id", None)
            result["environment"][k] = spec

    return result


# ---------------------------------------------------------------------------
# Segment response parsing
# ---------------------------------------------------------------------------

def _parse_segment(raw: str, segment_name: str) -> dict[str, Any] | None:
    if not raw:
        LOGGER.warning("[%s] empty response", segment_name)
        return None
    try:
        doc = json.loads(raw)
    except Exception as e:
        LOGGER.warning("[%s] JSON parse error: %s", segment_name, e)
        return None
    if not isinstance(doc, dict):
        LOGGER.warning("[%s] top-level is not a dict: %s", segment_name, type(doc).__name__)
        return None
    # Detect error echo-back from models that refuse oversized prompts
    if list(doc.keys()) == ["error"]:
        LOGGER.warning("[%s] model returned error JSON: %s", segment_name, doc)
        return None
    return doc


# ---------------------------------------------------------------------------
# Single-segment extraction with per-segment retry
# ---------------------------------------------------------------------------

def extract_segment(
    *,
    segment_name: str,
    system_prompt: str,
    user_prompt: str,
    expected_key: str,
    call_kwargs: dict,
    attempts: int,
    artifacts_dir: Path | None,
) -> Any:
    """
    Call the model for one focused segment, retrying on parse/key errors.
    Returns the VALUE under expected_key (not the wrapping dict).
    """
    feedback = ""

    for attempt in range(1, attempts + 1):
        LOGGER.info("[%s] attempt %d/%d", segment_name, attempt, attempts)

        raw = call_json_mode(
            system_prompt=system_prompt,
            user_prompt=user_prompt + feedback,
            segment_name=segment_name,
            **call_kwargs,
        )

        if artifacts_dir:
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            (artifacts_dir / f"{segment_name}_attempt{attempt:02d}_raw.txt").write_text(
                raw, encoding="utf-8"
            )

        doc = _parse_segment(raw, segment_name)
        if doc is None:
            feedback = (
                f"\n\nYour previous response could not be parsed as valid JSON "
                f"or was an error object. Return ONLY a valid JSON object with "
                f"top-level key '{expected_key}'.\n"
            )
            continue

        if expected_key not in doc:
            LOGGER.warning(
                "[%s] missing key '%s'; got keys: %s",
                segment_name, expected_key, list(doc.keys()),
            )
            feedback = (
                f"\n\nYour previous response was missing the required top-level key "
                f"'{expected_key}'. Return ONLY a JSON object whose single top-level "
                f"key is '{expected_key}'.\n"
            )
            continue

        value = doc[expected_key]
        LOGGER.info("[%s] ✓ extracted key '%s'", segment_name, expected_key)

        if artifacts_dir:
            (artifacts_dir / f"{segment_name}_final.json").write_text(
                json.dumps({expected_key: value}, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        return value

    LOGGER.error("[%s] failed after %d attempts — returning None", segment_name, attempts)
    return None


# ---------------------------------------------------------------------------
# Merge segments
# ---------------------------------------------------------------------------

def merge_segments(
    *,
    meta: dict,
    pipeline_summary: dict,
    components: list,
    parameters: dict,
    integrations: dict,
    flow_structure: dict,
    source_file: str | None = None,
) -> dict[str, Any]:
    return {
        "pipespec_version": "1.0",
        "metadata": {
            **meta,
            "analysis_timestamp": meta.get(
                "analysis_timestamp", datetime.now(timezone.utc).isoformat()
            ),
            "source_file": source_file or meta.get("source_file"),
        },
        "pipeline_summary": pipeline_summary,
        "components": components,
        "flow_structure": flow_structure,
        "parameters": parameters,
        "integrations": integrations,
    }


# ---------------------------------------------------------------------------
# Segment-scoped repair
# ---------------------------------------------------------------------------

def _compact_errors(errors, max_items: int = 20) -> str:
    lines = []
    for e in errors[:max_items]:
        path = e.instance_path or "(root)"
        lines.append(f"- {path}: {e.message}")
    if len(errors) > max_items:
        lines.append(f"... plus {len(errors) - max_items} more errors")
    return "\n".join(lines)


def _group_errors_by_segment(errors) -> dict[str, list]:
    """
    Group validation errors by top-level doc key so we can repair each
    failing segment independently.

    instance_path examples:
      "/parameters/schedule/catchup"  → "parameters"
      "/components/0/category"        → "components"
      "(root)"                        → "(root)"
    """
    groups: dict[str, list] = defaultdict(list)
    _known = {
        "metadata", "pipeline_summary", "components",
        "flow_structure", "parameters", "integrations",
    }
    for e in errors:
        path = (e.instance_path or "").lstrip("/")
        top = path.split("/")[0] if path else "(root)"
        key = top if top in _known else "(root)"
        groups[key].append(e)
    return dict(groups)


def _repair_segment(
    *,
    segment_key: str,
    segment_value: Any,
    errors: list,
    call_kwargs: dict,
    attempts: int,
    artifacts_dir: Path | None,
) -> Any:
    """
    Send only the failing segment + its errors back to the model for repair.
    Keeps the payload small so the model won't reject it.
    Returns the corrected segment value.
    """
    err_text = _compact_errors(errors)
    LOGGER.warning("[repair/%s] %d error(s):\n%s", segment_key, len(errors), err_text)

    system = (
        f"You are a PipeSpec v1 JSON repair engine for the '{segment_key}' section.\n"
        "Rules:\n"
        "- Return ONLY a valid JSON object.\n"
        f"- The object MUST have exactly one top-level key: '{segment_key}'.\n"
        "- Fix ONLY what the errors describe; preserve everything else.\n"
        "- Do NOT output markdown, code fences, or commentary.\n"
    )

    segment_json = json.dumps({segment_key: segment_value}, indent=2, ensure_ascii=False)

    for attempt in range(1, attempts + 1):
        LOGGER.info("[repair/%s] attempt %d/%d", segment_key, attempt, attempts)

        user = (
            f"Fix the '{segment_key}' segment to conform to PipeSpec v1 schema.\n"
            f"Return ONLY a JSON object with the single key '{segment_key}'.\n\n"
            f"Schema errors to fix:\n{err_text}\n\n"
            f"Current '{segment_key}' segment (fix this):\n{segment_json}\n"
        )

        if artifacts_dir:
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            (artifacts_dir / f"repair_{segment_key}_attempt{attempt:02d}_prompt.txt").write_text(
                user, encoding="utf-8"
            )

        raw = call_json_mode(
            system_prompt=system,
            user_prompt=user,
            segment_name=f"repair/{segment_key}",
            **call_kwargs,
        )

        if artifacts_dir:
            (artifacts_dir / f"repair_{segment_key}_attempt{attempt:02d}_raw.txt").write_text(
                raw, encoding="utf-8"
            )

        parsed = _parse_segment(raw, f"repair/{segment_key}")
        if parsed is None:
            LOGGER.warning("[repair/%s] unparseable/error response on attempt %d", segment_key, attempt)
            continue

        if segment_key not in parsed:
            LOGGER.warning(
                "[repair/%s] missing key '%s' in response; got: %s",
                segment_key, segment_key, list(parsed.keys()),
            )
            continue

        LOGGER.info("[repair/%s] ✓ repaired on attempt %d", segment_key, attempt)
        return parsed[segment_key]

    LOGGER.error("[repair/%s] all attempts failed — keeping original value", segment_key)
    return segment_value  # Return original so validator surfaces remaining errors


# ---------------------------------------------------------------------------
# Post-merge validate + segment-scoped repair loop
# ---------------------------------------------------------------------------

def validate_and_repair(
    doc: dict[str, Any],
    *,
    call_kwargs: dict,
    semantic_checks: bool,
    repair_attempts: int,
    artifacts_dir: Path | None,
) -> dict[str, Any]:
    """
    Validate the merged doc.  Group any schema errors by segment, send only
    each failing segment to the model for repair, patch back into the doc,
    and re-validate.  Repeat up to repair_attempts rounds.
    """
    for round_num in range(1, repair_attempts + 1):
        result = validate_dict(doc, semantic_checks=semantic_checks)
        if result.ok:
            LOGGER.info("✓ Document is schema-valid after round %d.", round_num)
            return doc

        LOGGER.warning("Validation round %d: %d error(s)", round_num, len(result.errors))

        groups = _group_errors_by_segment(result.errors)
        LOGGER.info("Errors grouped by segment: %s", {k: len(v) for k, v in groups.items()})

        for seg_key, seg_errors in groups.items():
            if seg_key == "(root)" or seg_key not in doc:
                LOGGER.warning(
                    "Cannot repair segment '%s' (not a patchable key); skipping", seg_key
                )
                continue

            doc[seg_key] = _repair_segment(
                segment_key=seg_key,
                segment_value=doc[seg_key],
                errors=seg_errors,
                call_kwargs=call_kwargs,
                attempts=repair_attempts,
                artifacts_dir=artifacts_dir,
            )

        if artifacts_dir:
            (artifacts_dir / f"after_repair_round{round_num:02d}.json").write_text(
                json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )

    # One final validation pass
    result = validate_dict(doc, semantic_checks=semantic_checks)
    if result.ok:
        LOGGER.info("✓ Document valid after all repair rounds.")
    else:
        LOGGER.error(
            "Document still has %d error(s) after %d repair rounds.",
            len(result.errors), repair_attempts,
        )
    return doc


# ---------------------------------------------------------------------------
# Main extraction orchestrator
# ---------------------------------------------------------------------------

def extract_parallel(
    *,
    description_text: str,
    base_url: str,
    api_key: str,
    model: str,
    max_tokens: int,
    temperature: float,
    segment_attempts: int,
    repair_attempts: int,
    semantic_checks: bool,
    print_model_output: bool,
    artifacts_dir: Path | None,
    source_file: str | None = None,
) -> dict[str, Any]:
    call_kwargs = dict(
        base_url=base_url,
        api_key=api_key,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    seg_kwargs = dict(
        call_kwargs=call_kwargs,
        attempts=segment_attempts,
        artifacts_dir=artifacts_dir,
    )

    # ── Wave 1: fully parallel ──────────────────────────────────────────────
    LOGGER.info("=== Wave 1: parallel segment extraction ===")

    wave1_defs = {
        "meta_summary": (SYSTEM_META,         user_meta(description_text),         "metadata"),
        "components":   (SYSTEM_COMPONENTS,    user_components(description_text),   "components"),
        "parameters":   (SYSTEM_PARAMETERS,    user_parameters(description_text),   "parameters"),
        "integrations": (SYSTEM_INTEGRATIONS,  user_integrations(description_text), "integrations"),
    }

    wave1_results: dict[str, Any] = {}

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures: dict[Future, str] = {
            pool.submit(
                extract_segment,
                segment_name=seg_name,
                system_prompt=sys_p,
                user_prompt=usr_p,
                expected_key=exp_key,
                **seg_kwargs,
            ): seg_name
            for seg_name, (sys_p, usr_p, exp_key) in wave1_defs.items()
        }
        for f in as_completed(futures):
            seg_name = futures[f]
            try:
                wave1_results[seg_name] = f.result()
            except Exception as exc:
                LOGGER.error("[%s] raised: %s", seg_name, exc, exc_info=True)
                wave1_results[seg_name] = None

    # ── Unpack meta (model may embed pipeline_summary inside or alongside) ──
    raw_meta = wave1_results.get("meta_summary") or {}
    meta_dict: dict = {}
    pipeline_summary_dict: dict = {}

    if isinstance(raw_meta, dict):
        if "pipeline_summary" in raw_meta:
            # Model returned both keys nested
            meta_dict = raw_meta.get("metadata", raw_meta)
            pipeline_summary_dict = raw_meta["pipeline_summary"]
        else:
            meta_dict = raw_meta

    # Fallback: extract pipeline_summary as its own call if still missing
    if not pipeline_summary_dict:
        LOGGER.info("[pipeline_summary] missing from meta result; extracting separately…")
        ps_sys = (
            "You are extracting ONLY the pipeline_summary of a PipeSpec v1 JSON.\n"
            "Return ONLY a JSON object with key 'pipeline_summary' containing:\n"
            "  name (string), description (string),\n"
            "  flow_patterns (array of: sequential|parallel|dag|conditional|loop),\n"
            "  task_executors (array of: python|http|sql|bash|email|docker|custom),\n"
            "  complexity (low|medium|high).\n"
            "Do NOT output markdown or commentary.\n"
        )
        ps_val = extract_segment(
            segment_name="pipeline_summary",
            system_prompt=ps_sys,
            user_prompt=(
                f"Extract pipeline_summary from this description:\n{description_text}\n\n"
                'Return exactly: {"pipeline_summary": {"name": "", "description": "",'
                ' "flow_patterns": ["sequential"], "task_executors": ["python"], "complexity": "low"}}'
            ),
            expected_key="pipeline_summary",
            **seg_kwargs,
        )
        pipeline_summary_dict = ps_val or {
            "name": "unnamed_pipeline",
            "description": "",
            "flow_patterns": ["sequential"],
            "task_executors": ["python"],
            "complexity": "low",
        }

    # ── Components ──────────────────────────────────────────────────────────
    components_val = wave1_results.get("components")
    if not isinstance(components_val, list):
        LOGGER.warning("components segment failed; using empty list")
        components_val = []

    # ── Parameters: normalize natural values → ParameterSpec objects ────────
    raw_params = wave1_results.get("parameters")
    if isinstance(raw_params, dict):
        LOGGER.info("Normalizing parameters: natural values → ParameterSpec objects")
        parameters_val = normalize_parameters(raw_params)
    else:
        LOGGER.warning("parameters segment failed; using empty structure")
        parameters_val = {
            "pipeline": {},
            "schedule": {},
            "execution": {},
            "components": {},
            "environment": {},
        }

    # ── Integrations ─────────────────────────────────────────────────────────
    integrations_val = wave1_results.get("integrations")
    if not isinstance(integrations_val, dict):
        LOGGER.warning("integrations segment failed; using empty structure")
        integrations_val = {
            "connections": [],
            "data_lineage": {"sources": [], "sinks": [], "intermediate_datasets": []},
        }

    # ── Wave 2: flow_structure (sequenced after components) ─────────────────
    LOGGER.info("=== Wave 2: flow_structure extraction ===")
    component_ids = [
        c["id"] for c in components_val if isinstance(c, dict) and "id" in c
    ]
    LOGGER.info("Component IDs for flow: %s", component_ids)

    flow_val = extract_segment(
        segment_name="flow_structure",
        system_prompt=SYSTEM_FLOW,
        user_prompt=user_flow(description_text, component_ids),
        expected_key="flow_structure",
        **seg_kwargs,
    )

    if not isinstance(flow_val, dict):
        LOGGER.warning("flow_structure segment failed; using minimal fallback")
        flow_val = {
            "pattern": "sequential",
            "entry_points": [component_ids[0]] if component_ids else [],
            "nodes": {
                cid: {
                    "kind": "Task",
                    "component_type_id": cid,
                    "upstream_policy": {"type": "all_success", "timeout_seconds": None},
                    "next_nodes": [],
                    "branch_config": None,
                    "sensor_config": None,
                    "parallel_config": None,
                }
                for cid in component_ids
            },
            "edges": [],
        }

    # ── Merge ────────────────────────────────────────────────────────────────
    LOGGER.info("=== Merging segments ===")
    doc = merge_segments(
        meta=meta_dict,
        pipeline_summary=pipeline_summary_dict,
        components=components_val,
        parameters=parameters_val,
        integrations=integrations_val,
        flow_structure=flow_val,
        source_file=source_file,
    )

    if artifacts_dir:
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        (artifacts_dir / "merged_pre_repair.json").write_text(
            json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    if print_model_output:
        LOGGER.info(
            "Merged doc (pre-repair):\n%s",
            json.dumps(doc, indent=2, ensure_ascii=False)[:4000],
        )

    # ── Validate + segment-scoped repair ─────────────────────────────────────
    LOGGER.info("=== Validate + segment-scoped repair ===")
    doc = validate_and_repair(
        doc,
        call_kwargs=call_kwargs,
        semantic_checks=semantic_checks,
        repair_attempts=repair_attempts,
        artifacts_dir=artifacts_dir,
    )

    if artifacts_dir:
        (artifacts_dir / "final.pipespec.json").write_text(
            json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    return doc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(
        description="Parallel PipeSpec v1 extraction from pipeline descriptions."
    )
    p.add_argument("--in", dest="inp", type=Path, required=True,
                   help="Text file with pipeline description.")
    p.add_argument("--out", dest="out", type=Path, required=True,
                   help="Output path for PipeSpec JSON.")

    p.add_argument("--base-url",
                   default=os.environ.get("OPENAI_BASE_URL", "https://api.deepinfra.com/v1/openai"))
    p.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY"))
    p.add_argument("--model",
                   default=os.environ.get("OPENAI_MODEL", "Qwen/Qwen2.5-Coder-32B-Instruct"))

    p.add_argument("--max-tokens", type=int, default=4096)
    p.add_argument("--temperature", type=float, default=0.1)
    p.add_argument("--segment-attempts", type=int, default=3,
                   help="Max retries per segment on key/parse failure.")
    p.add_argument("--repair-attempts", type=int, default=3,
                   help="Max segment-scoped repair rounds after merge.")

    p.add_argument("--semantic", action="store_true",
                   help="Enable semantic checks during validation.")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--print-model-output", action="store_true",
                   help="Log the merged doc before repair.")
    p.add_argument("--artifacts-dir", type=Path, default=None,
                   help="Write per-segment and per-repair artifacts here.")
    p.add_argument("--best-effort", action="store_true",
                   help="Write output even if document still has schema errors.")

    args = p.parse_args()
    setup_logging(args.verbose, args.debug)

    if not args.api_key:
        raise SystemExit("Missing --api-key (or set OPENAI_API_KEY).")

    desc = args.inp.read_text(encoding="utf-8")

    doc = extract_parallel(
        description_text=desc,
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        segment_attempts=args.segment_attempts,
        repair_attempts=args.repair_attempts,
        semantic_checks=args.semantic,
        print_model_output=args.print_model_output,
        artifacts_dir=args.artifacts_dir,
        source_file=str(args.inp),
    )

    # Final check
    result = validate_dict(doc, semantic_checks=args.semantic)
    if not result.ok:
        err_text = _compact_errors(result.errors)
        if args.best_effort:
            LOGGER.warning(
                "Writing best-effort output (%d error(s) remain):\n%s",
                len(result.errors), err_text,
            )
        else:
            raise SystemExit(
                f"Output still has {len(result.errors)} schema error(s). "
                f"Use --best-effort to write anyway.\n{err_text}"
            )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    LOGGER.info("Wrote PipeSpec: %s", args.out)
    print(f"Wrote PipeSpec: {args.out}")


if __name__ == "__main__":
    main()