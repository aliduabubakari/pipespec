from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .validator import SCHEMA_VERSION, load_schema, validate_file
from .reporting import make_report, write_report

console = Console()


def _render_result(path: Path, result) -> None:
    if result.ok:
        console.print(f"[bold green]OK[/bold green]  {path}  (PipeSpec schema {result.schema_version})")
    else:
        console.print(f"[bold red]INVALID[/bold red]  {path}  (PipeSpec schema {result.schema_version})")

    if result.errors:
        t = Table(title="Errors", show_lines=True)
        t.add_column("kind", style="bold")
        t.add_column("instance_path")
        t.add_column("message")
        t.add_column("schema_path")

        for e in result.errors:
            t.add_row(e.kind, e.instance_path or "-", e.message, e.schema_path or "-")
        console.print(t)

    if result.warnings:
        t = Table(title="Warnings (semantic checks)", show_lines=True)
        t.add_column("kind", style="bold")
        t.add_column("rule_id")
        t.add_column("instance_path")
        t.add_column("message")

        for w in result.warnings:
            rule_id = (w.details or {}).get("rule_id", "-")
            t.add_row(w.kind, rule_id, w.instance_path or "-", w.message)
        console.print(t)


def cli(
    file: Optional[Path] = typer.Argument(
        None, help="Path to .pipespec.json (normative) or .pipespec.yaml/.yml (tooling convenience)."
    ),
    semantic: bool = typer.Option(False, "--semantic", help="Enable semantic cross-reference checks (warnings)."),
    quiet: bool = typer.Option(False, "--quiet", help="Only use exit code; do not print output."),
    schema_info: bool = typer.Option(False, "--schema-info", help="Print schema metadata and exit."),
    report: Optional[Path] = typer.Option(
        None,
        "--report",
        help="Write a validation report to this file (json/yaml/md).",
    ),
    report_format: Optional[str] = typer.Option(
        None,
        "--report-format",
        help="Report format: json|yaml|md. If omitted, inferred from --report extension.",
    ),
) -> None:
    """
    Validate a PipeSpec file against PipeSpec v1 JSON Schema.
    """
    if schema_info:
        schema = load_schema()
        sid = schema.get("$id", "(no $id)")
        console.print(f"PipeSpec schema version: [bold]{SCHEMA_VERSION}[/bold]")
        console.print(f"Schema $id: {sid}")
        raise typer.Exit(code=0)

    if file is None:
        raise typer.BadParameter("Missing FILE. Example: pipespec-validate schema/examples/airvisual_pipeline.pipespec.json")

    result = validate_file(file, semantic_checks=semantic)

    if report is not None:
        rep = make_report(result, source_path=str(file))
        write_report(rep, report, fmt=report_format)

    if not quiet:
        _render_result(file, result)

    raise typer.Exit(code=0 if result.ok else 2)


def main() -> None:
    typer.run(cli)


if __name__ == "__main__":
    main()