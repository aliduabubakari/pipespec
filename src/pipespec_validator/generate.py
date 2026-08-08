from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console

from .io_utils import write_doc
from .llm_runtime import LLMConfig, build_llm_config, call_llm_json, parse_json_object
from .validator import validate_dict


console = Console()


def _compact_error_feedback(errors, max_items: int = 30) -> str:
    """Group errors by JSON path prefix for clearer feedback."""
    from collections import defaultdict
    groups: dict[str, list[str]] = defaultdict(list)
    for e in errors[:max_items]:
        path = e.instance_path or "(root)"
        section = path.split("/")[1] if path.startswith("/") else path
        groups[section].append(f"  - {path}: {e.message}")

    lines: list[str] = []
    for section in sorted(groups.keys()):
        msgs = groups[section]
        lines.append(f"[{section}] ({len(msgs)} issues):")
        lines.extend(msgs[:6])
        if len(msgs) > 6:
            lines.append(f"  ... plus {len(msgs) - 6} more in [{section}]")

    if len(errors) > max_items:
        lines.append(f"... plus {len(errors) - max_items} more errors total")
    return "\n".join(lines)


def _repair_feedback(errors) -> str:
    """Build repair feedback with specific fix hints for common errors."""
    base = _compact_error_feedback(errors, max_items=40)
    error_text = " ".join(f"{e.instance_path}:{e.message}" for e in errors)
    hints = []
    if "'from' is a required property" in error_text or "'source'" in error_text:
        hints.append("- Edge objects need 'from' and 'to' (NOT 'source'/'target')")
    if "io_spec" in error_text and "non-empty" in error_text:
        hints.append("- Every component needs at least one io_spec entry with name/direction/kind/format")
    if "component_type_id" in error_text:
        hints.append("- flow_structure.nodes[key] needs 'component_type_id' matching the component's 'id'")
    if "upstream_policy" in error_text and "'type'" in error_text:
        hints.append('- upstream_policy needs {"type": "all_success"}')
    if "parameters" in error_text and "execution" in error_text:
        hints.append("- parameters.execution should be {} or have properly structured parameter specs. Do NOT put pipeline metadata there.")
    if "schedule" in error_text and "type" in error_text:
        hints.append("- parameters.schedule entries MUST have: description, type, default, required")
    if hints:
        return base + "\n\nFIX HINTS:\n" + "\n".join(hints)
    return base


def build_system_prompt() -> str:
    return (
        "You are a pipeline extraction engine.\n"
        "You MUST output a single valid PipeSpec v1 JSON object.\n"
        "Do NOT output markdown, code fences, or extra commentary.\n"
        "\n"
        "=== VALID ENUM VALUES ===\n"
        "flow_patterns / pattern: sequential, parallel, dag, conditional, loop\n"
        "  Map: linear->sequential, branch_merge->dag, event_driven->dag\n"
        "executor_type / task_executors: python, http, sql, bash, email, docker, custom\n"
        "  Map: python_callable->python, shell_command->bash, sql_query->sql,\n"
        "       wait_poll->http, unknown->custom\n"
        "component category: Extractor, Transformer, Loader, Reconciliator,\n"
        "  QualityCheck, FeatureEngineering, ModelTraining, ModelEvaluation,\n"
        "  ModelInference, Notifier, Sensor, Custom\n"
        "io_spec direction: input, output\n"
        "io_spec kind: file, table, api, object, stream, features, model, metrics,\n"
        "  predictions, embedding\n"
        "common ML formats: pickle, pkl, joblib, onnx, pmml, mlflow, skops, npy, npz\n"
        "edge_type: success, failure, always, conditional\n"
        "complexity: low, medium, high\n"
        "parameter type: string, integer, float, boolean, array, object, datetime\n"
        "integration type: api, database, filesystem, object_store, feature_store,\n"
        "  model_registry, experiment_tracker, vector_store, message_queue, smtp, other\n"
        "upstream_policy type: all_success, none_failed, one_success, all_done\n"
        "schedule type: manual means no cron, cron means include cron expression\n"
        "\n"
        "=== CRITICAL STRUCTURE RULES ===\n"
        "- components is an ARRAY of objects, each with id/name/category/executor_type/io_spec.\n"
        "- flow_structure.nodes is an OBJECT keyed by component id (NOT an array).\n"
        "  Each node MUST have: kind, component_type_id, upstream_policy, next_nodes.\n"
        "  kind is always 'Task' unless specified otherwise.\n"
        "  component_type_id equals the component's id.\n"
        "  upstream_policy is {\"type\": \"all_success\"} unless specified.\n"
        "- flow_structure.edges is an ARRAY of {from, to, edge_type}.\n"
        "  Use 'from' and 'to' (NOT 'source'/'target').\n"
        "- io_spec is a non-empty ARRAY per component with name/direction/kind/format.\n"
        "  Every component MUST have at least one io_spec entry.\n"
        "- parameters has 5 fixed sections: pipeline, schedule, execution, components, environment.\n"
        "  Each parameter within MUST have: description, type, default, required.\n"
        "  Do NOT put pipeline metadata (model, topology) in parameters.execution.\n"
        "- integrations has 2 fixed keys: connections (array) and data_lineage (object).\n"
        "  data_lineage has sources, sinks, intermediate_datasets (all arrays of strings).\n"
        "- retry_policy fields: max_attempts (int), delay_seconds (int),\n"
        "  exponential_backoff (bool), retry_on (array of strings).\n"
        "\n"
        "=== COMMON MISTAKES TO AVOID ===\n"
        "- Do NOT use 'source'/'target' for edges. Use 'from'/'to'.\n"
        "- Do NOT put executor types in parameters. Put them in components.executor_type.\n"
        "- Do NOT leave io_spec empty. Every component processes data.\n"
        "- Do NOT invent pipeline parameters (model, topology, type).\n"
        "  Only extract parameters explicitly mentioned in the description.\n"
        "- flow_structure.entry_points must reference valid component ids.\n"
    )


def build_user_prompt(description_text: str, provider: str, model: str) -> str:
    example = {
        "pipespec_version": "1.0",
        "metadata": {
            "analysis_timestamp": "2026-01-01T00:00:00Z",
            "source_file": None,
            "llm_provider": provider,
            "llm_model": model,
        },
        "pipeline_summary": {
            "name": "example_pipeline",
            "description": "A simple ETL pipeline",
            "flow_patterns": ["sequential"],
            "task_executors": ["python"],
            "complexity": "low",
        },
        "components": [{
            "id": "extract_data",
            "name": "Extract Data",
            "category": "Extractor",
            "description": "Fetch data from API",
            "executor_type": "python",
            "io_spec": [{
                "name": "raw_data",
                "direction": "output",
                "kind": "api",
                "format": "json"
            }]
        }],
        "flow_structure": {
            "pattern": "sequential",
            "entry_points": ["extract_data"],
            "nodes": {
                "extract_data": {
                    "kind": "Task",
                    "component_type_id": "extract_data",
                    "upstream_policy": {"type": "all_success"},
                    "next_nodes": []
                }
            },
            "edges": []
        },
        "parameters": {
            "pipeline": {},
            "schedule": {},
            "execution": {},
            "components": {},
            "environment": {}
        },
        "integrations": {
            "connections": [],
            "data_lineage": {
                "sources": [],
                "sinks": [],
                "intermediate_datasets": []
            }
        }
    }
    return (
        f"Extract a PipeSpec v1 JSON document from this description.\n\n"
        f"PIPELINE DESCRIPTION:\n{description_text}\n\n"
        f"Follow this EXACT structure (change values, keep field names):\n"
        f"{json.dumps(example, indent=2)}\n\n"
        f"RULES:\n"
        f"- Create one component per 'performs' sentence.\n"
        f"- Extract dependencies from arrows (A->B).\n"
        f"- Map mechanism names to executor_type via the enum table.\n"
        f"- Every component MUST have a non-empty io_spec array.\n"
        f"- flow_structure.nodes is an OBJECT keyed by component id.\n"
        f"- parameters sections are OBJECTS (pipeline/schedule/execution=empty {{}} unless specified).\n"
        f"- Do NOT add extra top-level keys beyond what the example shows.\n"
        f"- Return ONLY the JSON object.\n"
    )


def generate_with_repair(
    *,
    description_text: str,
    llm_config: LLMConfig,
    attempts: int,
    max_tokens: int,
    temperature: float,
    semantic_checks: bool,
) -> dict:
    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(description_text, llm_config.provider, llm_config.model)
    feedback = ""
    last_error_summary = "unknown error"

    for attempt_num in range(attempts):
        # Temperature annealing: start cold, warm up for repairs
        if attempt_num == 0:
            current_temp = min(temperature, 0.05)
        elif attempt_num == 1:
            current_temp = min(temperature, 0.15)
        else:
            current_temp = min(temperature + 0.1, 0.4)

        content = call_llm_json(
            config=llm_config,
            system_prompt=system_prompt,
            user_prompt=user_prompt + feedback,
            max_tokens=max_tokens,
            temperature=current_temp,
        )

        try:
            doc = parse_json_object(content)
        except Exception as e:
            last_error_summary = f"JSON parse error: {e}"
            feedback = (
                "\n\nYour previous output was not valid JSON.\n"
                f"JSON parse error: {e}\n"
                "Return ONLY a valid JSON object. No markdown.\n"
            )
            continue

        result = validate_dict(doc, semantic_checks=semantic_checks)
        if result.ok:
            return doc

        err_feedback = _repair_feedback(result.errors)
        last_error_summary = f"{len(result.errors)} schema error(s)"
        if attempt_num < attempts - 1:
            feedback = (
                "\n\nThe previous JSON has schema errors.\n"
                "Fix ONLY the errors listed below. Preserve correct data.\n"
                "Return ONLY the corrected JSON object.\n\n"
                f"ERRORS:\n{err_feedback}\n"
            )

    raise RuntimeError(
        f"Failed to produce valid PipeSpec after {attempts} attempts. "
        f"Last error summary: {last_error_summary}"
    )


def run_generate_command(
    *,
    inp: Path,
    out: Path,
    provider: str,
    model: str | None,
    api_key: str | None,
    api_key_env: str | None,
    base_url: str | None,
    attempts: int,
    max_tokens: int,
    temperature: float,
    semantic: bool,
) -> None:
    description_text = inp.read_text(encoding="utf-8")
    llm_config = build_llm_config(
        provider=provider,
        model=model,
        api_key=api_key,
        api_key_env=api_key_env,
        base_url=base_url,
    )

    doc = generate_with_repair(
        description_text=description_text,
        llm_config=llm_config,
        attempts=attempts,
        max_tokens=max_tokens,
        temperature=temperature,
        semantic_checks=semantic,
    )

    out_fmt = "yaml" if out.suffix.lower() in {".yaml", ".yml"} else "json"
    write_doc(doc, out, out_fmt)
    console.print(f"[bold green]Wrote PipeSpec:[/bold green] {out}")


def generate_command(
    inp: Path = typer.Option(..., "--in", help="Path to pipeline description text file."),
    out: Path = typer.Option(..., "--out", help="Output PipeSpec path (.json/.yaml/.yml)."),
    provider: str = typer.Option(
        "openai_compatible",
        "--provider",
        help="openai|claude|deepinfra|deepseek|openrouter|ollama|openai_compatible",
    ),
    model: str | None = typer.Option(None, "--model", help="Provider model id."),
    api_key: str | None = typer.Option(None, "--api-key", help="API key override."),
    api_key_env: str | None = typer.Option(
        None,
        "--api-key-env",
        help="Environment variable name containing the API key (e.g. OPENAI_API_KEY).",
    ),
    base_url: str | None = typer.Option(None, "--base-url", help="Base URL (OpenAI-compatible providers)."),
    attempts: int = typer.Option(4, "--attempts", min=1, max=10),
    max_tokens: int = typer.Option(4096, "--max-tokens", min=256, max=16384),
    temperature: float = typer.Option(0.1, "--temperature", min=0.0, max=1.0),
    semantic: bool = typer.Option(False, "--semantic", help="Enable semantic checks during repair loop."),
) -> None:
    try:
        run_generate_command(
            inp=inp,
            out=out,
            provider=provider,
            model=model,
            api_key=api_key,
            api_key_env=api_key_env,
            base_url=base_url,
            attempts=attempts,
            max_tokens=max_tokens,
            temperature=temperature,
            semantic=semantic,
        )
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[bold red]Generation failed:[/bold red] {e}")
        raise typer.Exit(code=2)
