from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console

from .corrections import autofix_multi_round
from .io_utils import load_doc, write_doc
from .llm_runtime import LLMConfig, build_llm_config, call_llm_json, parse_json_object
from .validator import validate_dict


console = Console()


def _compact_error_feedback(errors, max_items: int = 40) -> str:
    lines: list[str] = []
    for e in errors[:max_items]:
        path = e.instance_path or "(root)"
        lines.append(f"- {path}: {e.message}")
    if len(errors) > max_items:
        lines.append(f"... plus {len(errors) - max_items} more errors")
    return "\n".join(lines)


def _repair_prompt(doc: dict, description_text: str, errors_text: str) -> str:
    doc_text = json.dumps(doc, ensure_ascii=False, indent=2)
    if len(doc_text) > 16000:
        doc_text = doc_text[:16000] + "\n... (truncated)"
    if len(description_text) > 8000:
        description_text = description_text[:8000] + "\n... (truncated)"

    return (
        "You are repairing a PipeSpec v1 document.\n"
        "Return ONLY a corrected JSON object.\n"
        "Do not output markdown.\n"
        "Preserve correct fields and only fix errors.\n\n"
        "PIPELINE DESCRIPTION:\n"
        f"{description_text}\n\n"
        "CURRENT PIPESPEC:\n"
        f"{doc_text}\n\n"
        "SCHEMA ERRORS TO FIX:\n"
        f"{errors_text}\n"
    )


def repair_with_llm(
    *,
    start_doc: dict,
    description_text: str,
    llm_config: LLMConfig,
    attempts: int,
    max_tokens: int,
    temperature: float,
    semantic_checks: bool,
) -> tuple[dict, bool]:
    current = start_doc
    current_result = validate_dict(current, semantic_checks=semantic_checks)
    if current_result.ok:
        return current, True

    system_prompt = (
        "You are a strict JSON repair engine for PipeSpec v1. "
        "Always return one valid JSON object only."
    )

    for _ in range(attempts):
        errors_text = _compact_error_feedback(current_result.errors)
        user_prompt = _repair_prompt(current, description_text, errors_text)
        content = call_llm_json(
            config=llm_config,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        try:
            candidate = parse_json_object(content)
        except Exception:
            continue

        candidate_result = validate_dict(candidate, semantic_checks=semantic_checks)
        if candidate_result.ok:
            return candidate, True

        if len(candidate_result.errors) < len(current_result.errors):
            current = candidate
            current_result = candidate_result

    return current, False


def run_correct_command(
    *,
    inp: Path,
    out: Path,
    description: Path | None,
    provider: str,
    model: str | None,
    api_key: str | None,
    api_key_env: str | None,
    base_url: str | None,
    attempts: int,
    max_tokens: int,
    temperature: float,
    semantic: bool,
    autofix_rounds: int,
) -> int:
    doc, detected_fmt = load_doc(inp)
    fixed_doc, actions, _ = autofix_multi_round(doc, max_rounds=autofix_rounds)
    result = validate_dict(fixed_doc, semantic_checks=semantic)

    if result.ok:
        out_fmt = "yaml" if out.suffix.lower() in {".yaml", ".yml"} else detected_fmt
        write_doc(fixed_doc, out, out_fmt)
        console.print(f"[green]Wrote corrected PipeSpec:[/green] {out}")
        if actions:
            console.print(f"[dim]Applied deterministic fixes:[/dim] {len(actions)}")
        return 0

    if description is None:
        out_fmt = "yaml" if out.suffix.lower() in {".yaml", ".yml"} else detected_fmt
        write_doc(fixed_doc, out, out_fmt)
        console.print(f"[yellow]Wrote best-effort corrected PipeSpec:[/yellow] {out}")
        console.print(
            "[yellow]Schema issues remain. Provide --description to enable LLM correction.[/yellow]"
        )
        return 2

    llm_config = build_llm_config(
        provider=provider,
        model=model,
        api_key=api_key,
        api_key_env=api_key_env,
        base_url=base_url,
    )
    description_text = description.read_text(encoding="utf-8")
    repaired, ok = repair_with_llm(
        start_doc=fixed_doc,
        description_text=description_text,
        llm_config=llm_config,
        attempts=attempts,
        max_tokens=max_tokens,
        temperature=temperature,
        semantic_checks=semantic,
    )

    out_fmt = "yaml" if out.suffix.lower() in {".yaml", ".yml"} else detected_fmt
    write_doc(repaired, out, out_fmt)
    if ok:
        console.print(f"[green]Wrote corrected PipeSpec:[/green] {out}")
        return 0
    console.print(f"[yellow]Wrote best-effort corrected PipeSpec:[/yellow] {out}")
    return 2


def correct_command(
    inp: Path = typer.Option(..., "--in", help="Input PipeSpec path (.json/.yaml/.yml)."),
    out: Path = typer.Option(..., "--out", help="Output corrected path."),
    description: Path | None = typer.Option(
        None,
        "--description",
        help="Pipeline description text used for LLM-assisted correction.",
    ),
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
        help="Environment variable name containing the API key (e.g. ANTHROPIC_API_KEY).",
    ),
    base_url: str | None = typer.Option(None, "--base-url", help="Base URL (OpenAI-compatible providers)."),
    attempts: int = typer.Option(3, "--attempts", min=1, max=10),
    max_tokens: int = typer.Option(4096, "--max-tokens", min=256, max=16384),
    temperature: float = typer.Option(0.0, "--temperature", min=0.0, max=1.0),
    semantic: bool = typer.Option(False, "--semantic"),
    autofix_rounds: int = typer.Option(5, "--autofix-rounds", min=1, max=10),
) -> None:
    code = run_correct_command(
        inp=inp,
        out=out,
        description=description,
        provider=provider,
        model=model,
        api_key=api_key,
        api_key_env=api_key_env,
        base_url=base_url,
        attempts=attempts,
        max_tokens=max_tokens,
        temperature=temperature,
        semantic=semantic,
        autofix_rounds=autofix_rounds,
    )
    raise typer.Exit(code=code)
