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
    lines: list[str] = []
    for e in errors[:max_items]:
        path = e.instance_path or "(root)"
        lines.append(f"- {path}: {e.message}")
    if len(errors) > max_items:
        lines.append(f"... plus {len(errors) - max_items} more errors")
    return "\n".join(lines)


def build_system_prompt() -> str:
    return (
        "You are a pipeline extraction engine.\n"
        "You MUST output a PipeSpec v1 JSON document.\n"
        "\n"
        "Hard rules:\n"
        "- Output MUST be a single valid JSON object.\n"
        "- Output MUST conform to PipeSpec v1 structure.\n"
        "- Do NOT output markdown, code fences, or extra commentary.\n"
        "- Do NOT invent unknown details; use null or empty arrays/objects.\n"
        "\n"
        "Critical structure rules:\n"
        "- components MUST be an array of component objects (NOT an object/map).\n"
        "- flow_structure.nodes MUST be an object/map keyed by node id (NOT an array).\n"
        "- integrations MUST contain keys: connections (array) and data_lineage (object).\n"
    )


def build_user_prompt(description_text: str, provider: str, model: str) -> str:
    skeleton = {
        "pipespec_version": "1.0",
        "metadata": {
            "analysis_timestamp": "2026-01-01T00:00:00Z",
            "source_file": None,
            "llm_provider": provider,
            "llm_model": model,
            "schema_version": "1.0",
        },
        "pipeline_summary": {
            "name": "",
            "description": "",
            "flow_patterns": ["sequential"],
            "task_executors": [],
            "complexity": "low",
        },
        "components": [],
        "flow_structure": {
            "pattern": "sequential",
            "entry_points": [],
            "nodes": {},
            "edges": [],
        },
        "parameters": {
            "pipeline": {},
            "schedule": {},
            "execution": {},
            "components": {},
            "environment": {},
        },
        "integrations": {
            "connections": [],
            "data_lineage": {
                "sources": [],
                "sinks": [],
                "intermediate_datasets": [],
            },
        },
    }

    return (
        "Extract a PipeSpec v1 JSON document from this pipeline description.\n"
        "Use the skeleton below as a SHAPE TEMPLATE.\n"
        "You must keep data types and required keys.\n"
        "Replace placeholder values with real values from the description.\n"
        "Return ONLY the JSON object.\n\n"
        "PIPELINE DESCRIPTION:\n"
        f"{description_text}\n\n"
        "SKELETON SHAPE TEMPLATE:\n"
        f"{json.dumps(skeleton, indent=2)}\n"
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

    for _ in range(attempts):
        content = call_llm_json(
            config=llm_config,
            system_prompt=system_prompt,
            user_prompt=user_prompt + feedback,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        try:
            doc = parse_json_object(content)
        except Exception as e:
            last_error_summary = f"JSON parse error: {e}"
            feedback = (
                "\n\nYour previous output was not valid JSON.\n"
                f"JSON parse error: {e}\n"
                "Return ONLY a valid JSON object.\n"
            )
            continue

        result = validate_dict(doc, semantic_checks=semantic_checks)
        if result.ok:
            return doc

        err_feedback = _compact_error_feedback(result.errors, max_items=30)
        last_error_summary = f"{len(result.errors)} schema error(s). First few:\n{err_feedback}"
        feedback = (
            "\n\nThe previous JSON does not conform to the PipeSpec schema.\n"
            "Fix ONLY what is necessary to satisfy the schema.\n"
            "Preserve correct information.\n"
            "Return ONLY the corrected JSON object.\n\n"
            f"Schema errors:\n{err_feedback}\n"
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
