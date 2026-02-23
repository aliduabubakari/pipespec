from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .validator import SCHEMA_VERSION, load_schema, validate_file

app = typer.Typer(add_completion=False, help="Validate PipeSpec (.pipespec.json/.yaml) documents.")
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
        t.add_column("instance_path")
        t.add_column("message")
        for w in result.warnings:
            t.add_row(w.kind, w.instance_path or "-", w.message)
        console.print(t)


@app.callback(invoke_without_command=True)
def default(
    ctx: typer.Context,
    file: Optional[Path] = typer.Argument(None, help="Path to .pipespec.json or .pipespec.yaml/.yml"),
    semantic: bool = typer.Option(False, "--semantic", help="Enable semantic cross-reference checks (warnings)."),
    quiet: bool = typer.Option(False, "--quiet", help="Only use exit code; do not print output."),
) -> None:
    """
    Validate a PipeSpec file. If no subcommand is provided, this validates FILE.
    """
    if ctx.invoked_subcommand is not None:
        return

    if file is None:
        raise typer.BadParameter("Missing FILE. Example: pipespec-validate schema/examples/airvisual_pipeline.pipespec.json")

    result = validate_file(file, semantic_checks=semantic)

    if not quiet:
        _render_result(file, result)

    raise typer.Exit(code=0 if result.ok else 2)


@app.command("schema-info")
def schema_info_cmd() -> None:
    schema = load_schema()
    sid = schema.get("$id", "(no $id)")
    console.print(f"PipeSpec schema version: [bold]{SCHEMA_VERSION}[/bold]")
    console.print(f"Schema $id: {sid}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()