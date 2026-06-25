from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from .io_utils import load_doc


console = Console()


@dataclass(frozen=True)
class SemanticDiffReport:
    left_path: str
    right_path: str
    sections: dict[str, Any]

    @property
    def has_changes(self) -> bool:
        for section in self.sections.values():
            if isinstance(section, dict):
                for value in section.values():
                    if isinstance(value, list) and value:
                        return True
                    if isinstance(value, dict) and value:
                        return True
            elif section:
                return True
        return False


def _sorted_unique(values: list[str]) -> list[str]:
    return sorted(set(values))


def _component_projection(component: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": component.get("name"),
        "category": component.get("category"),
        "executor_type": component.get("executor_type"),
        "io_spec": component.get("io_spec", []),
        "upstream_policy": component.get("upstream_policy"),
        "retry_policy": component.get("retry_policy"),
        "concurrency": component.get("concurrency"),
        "datasets": component.get("datasets"),
        "connections": component.get("connections", []),
    }


def _connection_projection(conn: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": conn.get("name"),
        "type": conn.get("type"),
        "direction": conn.get("direction"),
        "used_by_components": sorted(conn.get("used_by_components", [])),
        "datasets": conn.get("datasets"),
    }


def _flow_edge_key(edge: dict[str, Any]) -> str:
    frm = edge.get("from")
    to = edge.get("to")
    edge_type = edge.get("edge_type")
    cond = edge.get("condition")
    return f"{frm}->{to}:{edge_type}:{cond}"


def _flatten_parameter_paths(parameters: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for top_key in ["pipeline", "schedule", "execution", "environment"]:
        section = parameters.get(top_key, {})
        if isinstance(section, dict):
            for k in section.keys():
                out.append(f"{top_key}.{k}")

    comps = parameters.get("components", {})
    if isinstance(comps, dict):
        for comp_id, comp_params in comps.items():
            if isinstance(comp_params, dict):
                for k in comp_params.keys():
                    out.append(f"components.{comp_id}.{k}")
    return _sorted_unique(out)


def _compare_keyed_objects(
    left: dict[str, dict[str, Any]],
    right: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    left_ids = set(left.keys())
    right_ids = set(right.keys())
    added = sorted(right_ids - left_ids)
    removed = sorted(left_ids - right_ids)

    changed: dict[str, Any] = {}
    for key in sorted(left_ids & right_ids):
        if left[key] != right[key]:
            changed[key] = {"left": left[key], "right": right[key]}

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def semantic_diff(left_doc: dict[str, Any], right_doc: dict[str, Any], *, left_path: str, right_path: str) -> SemanticDiffReport:
    left_components = {
        c["id"]: _component_projection(c)
        for c in left_doc.get("components", [])
        if isinstance(c, dict) and isinstance(c.get("id"), str)
    }
    right_components = {
        c["id"]: _component_projection(c)
        for c in right_doc.get("components", [])
        if isinstance(c, dict) and isinstance(c.get("id"), str)
    }

    left_nodes = left_doc.get("flow_structure", {}).get("nodes", {})
    right_nodes = right_doc.get("flow_structure", {}).get("nodes", {})
    left_node_ids = sorted(left_nodes.keys()) if isinstance(left_nodes, dict) else []
    right_node_ids = sorted(right_nodes.keys()) if isinstance(right_nodes, dict) else []

    left_edges = left_doc.get("flow_structure", {}).get("edges", [])
    right_edges = right_doc.get("flow_structure", {}).get("edges", [])
    left_edge_keys = _sorted_unique([_flow_edge_key(e) for e in left_edges if isinstance(e, dict)])
    right_edge_keys = _sorted_unique([_flow_edge_key(e) for e in right_edges if isinstance(e, dict)])

    left_conns = {
        c["id"]: _connection_projection(c)
        for c in left_doc.get("integrations", {}).get("connections", [])
        if isinstance(c, dict) and isinstance(c.get("id"), str)
    }
    right_conns = {
        c["id"]: _connection_projection(c)
        for c in right_doc.get("integrations", {}).get("connections", [])
        if isinstance(c, dict) and isinstance(c.get("id"), str)
    }

    left_param_paths = _flatten_parameter_paths(left_doc.get("parameters", {}))
    right_param_paths = _flatten_parameter_paths(right_doc.get("parameters", {}))

    left_summary = left_doc.get("pipeline_summary", {}) if isinstance(left_doc.get("pipeline_summary"), dict) else {}
    right_summary = right_doc.get("pipeline_summary", {}) if isinstance(right_doc.get("pipeline_summary"), dict) else {}
    summary_changed = {}
    for field in ["name", "description", "flow_patterns", "task_executors", "complexity"]:
        if left_summary.get(field) != right_summary.get(field):
            summary_changed[field] = {
                "left": left_summary.get(field),
                "right": right_summary.get(field),
            }

    sections = {
        "pipeline_summary": {
            "changed": summary_changed,
        },
        "components": _compare_keyed_objects(left_components, right_components),
        "flow_nodes": {
            "added": sorted(set(right_node_ids) - set(left_node_ids)),
            "removed": sorted(set(left_node_ids) - set(right_node_ids)),
        },
        "flow_edges": {
            "added": sorted(set(right_edge_keys) - set(left_edge_keys)),
            "removed": sorted(set(left_edge_keys) - set(right_edge_keys)),
        },
        "integrations": _compare_keyed_objects(left_conns, right_conns),
        "parameter_keys": {
            "added": sorted(set(right_param_paths) - set(left_param_paths)),
            "removed": sorted(set(left_param_paths) - set(right_param_paths)),
        },
    }
    return SemanticDiffReport(left_path=left_path, right_path=right_path, sections=sections)


def _render_human(report: SemanticDiffReport) -> None:
    console.print(f"[bold]Left:[/bold]  {report.left_path}")
    console.print(f"[bold]Right:[/bold] {report.right_path}")

    t = Table(title="PipeSpec Semantic Diff", show_lines=True)
    t.add_column("section", style="bold")
    t.add_column("added")
    t.add_column("removed")
    t.add_column("changed")

    def count_changed(val: Any) -> int:
        if isinstance(val, dict):
            return len(val)
        if isinstance(val, list):
            return len(val)
        return 0

    for section_name, section in report.sections.items():
        added = count_changed(section.get("added", [])) if isinstance(section, dict) else 0
        removed = count_changed(section.get("removed", [])) if isinstance(section, dict) else 0
        changed = count_changed(section.get("changed", {})) if isinstance(section, dict) else 0
        t.add_row(section_name, str(added), str(removed), str(changed))
    console.print(t)

    if not report.has_changes:
        console.print("[green]No semantic differences detected.[/green]")
        return

    for section_name, section in report.sections.items():
        if not isinstance(section, dict):
            continue
        added = section.get("added", [])
        removed = section.get("removed", [])
        changed = section.get("changed", {})
        if not added and not removed and not changed:
            continue
        console.print(f"\n[bold]{section_name}[/bold]")
        if added:
            console.print(f"  [green]+ added:[/green] {added}")
        if removed:
            console.print(f"  [red]- removed:[/red] {removed}")
        if changed:
            keys = list(changed.keys()) if isinstance(changed, dict) else changed
            console.print(f"  [yellow]~ changed:[/yellow] {keys}")


def run_diff_command(
    *,
    left: Path,
    right: Path,
    as_json: bool,
) -> int:
    left_doc, _ = load_doc(left)
    right_doc, _ = load_doc(right)
    report = semantic_diff(left_doc, right_doc, left_path=str(left), right_path=str(right))
    payload = {
        "left_path": report.left_path,
        "right_path": report.right_path,
        "has_changes": report.has_changes,
        "sections": report.sections,
    }

    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
    else:
        _render_human(report)
    return 1 if report.has_changes else 0


def diff_command(
    left: Path = typer.Option(..., "--left", help="Left PipeSpec file (json/yaml)."),
    right: Path = typer.Option(..., "--right", help="Right PipeSpec file (json/yaml)."),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    code = run_diff_command(left=left, right=right, as_json=as_json)
    raise typer.Exit(code=code)


def main() -> None:
    typer.run(diff_command)
