from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .corrections import autofix_dict, autofix_multi_round, MAX_ROUNDS
from .hints import generate_hints, llm_escalation_needed, escalation_summary
from .io_utils import load_doc, write_doc
from .reporting import make_report, write_report
from .validator import SCHEMA_VERSION, load_schema, validate_dict, validate_file

console = Console()


# ---------------------------------------------------------------------------
# Rich rendering helpers
# ---------------------------------------------------------------------------

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


def _render_hints(hints, *, max_display: int = 10) -> None:
    """Print hints as a compact Rich table."""
    if not hints:
        return

    SEVERITY_STYLE = {"high": "bold red", "medium": "yellow", "low": "dim"}
    TIER_ICON = {"structural": "🔧", "content": "📝", "semantic": "🔍"}

    t = Table(title="Hints", show_lines=True)
    t.add_column("sev", style="bold", width=4)
    t.add_column("tier", width=12)
    t.add_column("code")
    t.add_column("path")
    t.add_column("message")

    for h in hints[:max_display]:
        icon = TIER_ICON.get(h.tier, "")
        style = SEVERITY_STYLE.get(h.severity, "")
        path_str = h.paths[0] if h.paths else ""
        t.add_row(
            f"[{style}]{h.severity[0].upper()}[/{style}]",
            f"{icon} {h.tier}",
            h.code,
            path_str or "-",
            h.message[:80] + ("…" if len(h.message) > 80 else ""),
        )

    if len(hints) > max_display:
        t.add_row("…", "…", "…", "…", f"+{len(hints) - max_display} more (see --report)")

    console.print(t)


def _render_fix_actions(actions, rounds: int | None = None) -> None:
    if not actions:
        return
    round_str = f" ({rounds} round(s))" if rounds else ""
    t = Table(title=f"AutoFix Actions Applied{round_str}", show_lines=True)
    t.add_column("sev", width=4)
    t.add_column("code")
    t.add_column("path")
    t.add_column("message")
    for a in actions[:25]:
        sev_icon = "⚠" if a.severity == "warning" else "✓"
        t.add_row(sev_icon, a.code, a.path, a.message)
    if len(actions) > 25:
        t.add_row("…", "…", "…", f"+{len(actions) - 25} more (see --report)")
    console.print(t)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cli(
    file: Optional[Path] = typer.Argument(
        None,
        help="Path to .pipespec.json (normative) or .pipespec.yaml/.yml (tooling convenience).",
    ),
    semantic: bool = typer.Option(False, "--semantic", help="Enable semantic checks (warnings)."),
    quiet: bool = typer.Option(False, "--quiet", help="Only use exit code; suppress output."),
    schema_info: bool = typer.Option(False, "--schema-info", help="Print schema metadata and exit."),

    # Report options
    report: Optional[Path] = typer.Option(None, "--report", help="Write a validation report (json/yaml/md)."),
    report_format: Optional[str] = typer.Option(
        None, "--report-format", help="json|yaml|md. Inferred from --report extension if omitted."
    ),

    # AutoFix options
    autofix: bool = typer.Option(False, "--autofix", help="Apply deterministic autofixes."),
    autofix_out: Optional[Path] = typer.Option(None, "--autofix-out", help="Output path for fixed document."),
    autofix_max_rounds: int = typer.Option(
        MAX_ROUNDS,
        "--autofix-max-rounds",
        help=f"Max iterative fix rounds (default {MAX_ROUNDS}). "
             "Each round may enable the next set of fixes.",
        min=1,
        max=10,
    ),
    autofix_single_round: bool = typer.Option(
        False,
        "--autofix-single-round",
        help="Apply only one round of fixes (legacy behaviour).",
    ),
) -> None:
    """
    Validate a PipeSpec file against PipeSpec v1 JSON Schema.

    Exit codes:
      0 = valid (or valid after autofix)
      2 = invalid schema errors
    """
    if schema_info:
        schema = load_schema()
        sid = schema.get("$id", "(no $id)")
        console.print(f"PipeSpec schema version: [bold]{SCHEMA_VERSION}[/bold]")
        console.print(f"Schema $id: {sid}")
        raise typer.Exit(code=0)

    if file is None:
        raise typer.BadParameter(
            "Missing FILE. Example: pipespec-validate my_pipeline.pipespec.json"
        )

    # ── 1) Validate original ─────────────────────────────────────────────────
    result = validate_file(file, semantic_checks=semantic)

    # ── 2) AutoFix ───────────────────────────────────────────────────────────
    fix_actions_raw = None
    post_autofix_result = None
    rounds_run = None

    if autofix:
        if autofix_out is None:
            raise typer.BadParameter("--autofix requires --autofix-out <path>")

        doc, fmt = load_doc(file)

        if autofix_single_round:
            fixed, actions = autofix_dict(doc)
            rounds_run = 1
        else:
            fixed, actions, rounds_run = autofix_multi_round(
                doc, max_rounds=autofix_max_rounds
            )

        fix_actions_raw = [asdict(a) for a in actions]

        # Determine output format
        out_fmt = fmt
        if autofix_out.suffix.lower() == ".json":
            out_fmt = "json"
        elif autofix_out.suffix.lower() in {".yaml", ".yml"}:
            out_fmt = "yaml"

        write_doc(fixed, autofix_out, out_fmt)

        # Validate the fixed doc (with semantic if requested)
        post_autofix_result = validate_dict(fixed, semantic_checks=semantic)

        if not quiet:
            console.print(f"\n[dim]Wrote autofixed file:[/dim] {autofix_out}")
            _render_fix_actions(actions, rounds=rounds_run)

            console.print("\n[bold]Post-AutoFix validation:[/bold]")
            _render_result(autofix_out, post_autofix_result)

            # Show post-autofix hints
            post_hints = generate_hints(post_autofix_result.errors, post_autofix_result.warnings)
            if post_hints:
                _render_hints(post_hints)

            # Escalation guidance
            if llm_escalation_needed(post_hints):
                console.print(f"\n[yellow]{escalation_summary(post_hints)}[/yellow]")

    # ── 3) Report ────────────────────────────────────────────────────────────
    if report is not None:
        rep = make_report(
            result,
            source_path=str(file),
            fix_actions=fix_actions_raw,
            post_autofix_result=post_autofix_result,
            rounds_run=rounds_run,
        )
        write_report(rep, report, fmt=report_format)
        if not quiet:
            console.print(f"\n[dim]Wrote report:[/dim] {report}")

    # ── 4) Print original result ─────────────────────────────────────────────
    if not quiet:
        console.print("\n[bold]Original file validation:[/bold]")
        _render_result(file, result)

        # Always show hints for the original result (concise — top 5 only)
        orig_hints = generate_hints(result.errors, result.warnings)
        if orig_hints and not result.ok:
            _render_hints(orig_hints, max_display=5)
            if llm_escalation_needed(orig_hints):
                console.print(f"\n[yellow]{escalation_summary(orig_hints)}[/yellow]")

    # ── 5) Exit code ─────────────────────────────────────────────────────────
    # If autofix ran, exit based on post_autofix_result (more useful in CI pipelines).
    if autofix and post_autofix_result is not None:
        raise typer.Exit(code=0 if post_autofix_result.ok else 2)
    raise typer.Exit(code=0 if result.ok else 2)


def main() -> None:
    typer.run(cli)


if __name__ == "__main__":
    main()