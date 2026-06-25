from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .cli import cli as validate_cli
from .correct_llm import correct_command
from .diffing import diff_command
from .elicitation.cli import elicit_command
from .generate import generate_command
from .llm_runtime import (
    default_base_url_for_provider,
    default_model_for_provider,
    detect_api_key_source,
    normalize_provider,
    supported_providers,
)


app = typer.Typer(
    help="PipeSpec toolkit: generate, validate, and correct PipeSpec JSON/YAML documents."
)
console = Console()


@app.command("validate")
def validate_command(
    file: Optional[Path] = typer.Argument(
        None,
        help="Path to .pipespec.json or .pipespec.yaml/.yml.",
    ),
    semantic: bool = typer.Option(False, "--semantic", help="Enable semantic checks (warnings)."),
    quiet: bool = typer.Option(False, "--quiet", help="Only use exit code; suppress output."),
    schema_info: bool = typer.Option(False, "--schema-info", help="Print schema metadata and exit."),
    report: Optional[Path] = typer.Option(None, "--report", help="Write a validation report (json/yaml/md)."),
    report_format: Optional[str] = typer.Option(None, "--report-format", help="json|yaml|md."),
    autofix: bool = typer.Option(False, "--autofix", help="Apply deterministic autofixes."),
    autofix_out: Optional[Path] = typer.Option(None, "--autofix-out", help="Output path for fixed document."),
    autofix_max_rounds: int = typer.Option(5, "--autofix-max-rounds", min=1, max=10),
    autofix_single_round: bool = typer.Option(False, "--autofix-single-round"),
) -> None:
    validate_cli(
        file=file,
        semantic=semantic,
        quiet=quiet,
        schema_info=schema_info,
        report=report,
        report_format=report_format,
        autofix=autofix,
        autofix_out=autofix_out,
        autofix_max_rounds=autofix_max_rounds,
        autofix_single_round=autofix_single_round,
    )


app.command("generate")(generate_command)
app.command("elicit")(elicit_command)
app.command("correct")(correct_command)
app.command("diff")(diff_command)


@app.command("providers")
def providers_command(
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        help="Optional provider filter (openai|claude|deepinfra|deepseek|openrouter|ollama|openai_compatible).",
    ),
    api_key_env: Optional[str] = typer.Option(
        None,
        "--api-key-env",
        help="Optional key env var name to check first for auth status.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON instead of a table.",
    ),
) -> None:
    rows = supported_providers()
    if provider is not None:
        rows = [normalize_provider(provider)]
        if rows[0] not in supported_providers():
            raise typer.BadParameter(f"Unsupported provider: {provider}")

    payload = []
    for p in rows:
        payload.append(
            {
                "provider": p,
                "default_model": default_model_for_provider(p),
                "base_url": default_base_url_for_provider(p),
                "api_key_source": detect_api_key_source(p, api_key_env=api_key_env),
            }
        )

    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return

    t = Table(title="PipeSpec Provider Status", show_lines=True)
    t.add_column("provider", style="bold")
    t.add_column("default_model")
    t.add_column("base_url")
    t.add_column("api_key_source")

    for row in payload:
        t.add_row(
            row["provider"],
            row["default_model"],
            row["base_url"] or "-",
            row["api_key_source"],
        )

    console.print(t)
    if provider is None:
        console.print("[dim]Alias:[/dim] claude -> anthropic")


def main() -> None:
    app()
