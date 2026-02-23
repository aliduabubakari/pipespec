from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Literal

from .models import ValidationErrorItem

Severity = Literal["warning", "error"]


@dataclass(frozen=True)
class SemanticRule:
    rule_id: str
    description: str
    severity: Severity
    apply: Callable[[dict[str, Any]], list[ValidationErrorItem]]


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------

def _warn(*, rule_id: str, message: str, instance_path: str) -> ValidationErrorItem:
    return ValidationErrorItem(
        kind="semantic",
        message=message,
        instance_path=instance_path,
        schema_path="",
        details={"rule_id": rule_id},
    )


def _edge_key(edge: dict[str, Any]) -> tuple[str, str, str, str]:
    # Key used for duplicate edge detection
    return (
        str(edge.get("from")),
        str(edge.get("to")),
        str(edge.get("edge_type")),
        str(edge.get("condition")),
    )


def _build_edge_pairs(edges: list[dict[str, Any]], node_ids: set[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        frm = e.get("from")
        to = e.get("to")
        if not isinstance(frm, str) or not isinstance(to, str):
            continue
        if frm not in node_ids or to not in node_ids:
            continue
        pairs.append((frm, to))
    return pairs


def _detect_cycle(node_ids: set[str], edge_pairs: list[tuple[str, str]]) -> bool:
    """
    DFS color marking cycle detection.
    """
    graph: dict[str, list[str]] = {n: [] for n in node_ids}
    for a, b in edge_pairs:
        graph.setdefault(a, []).append(b)

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in node_ids}

    def dfs(u: str) -> bool:
        color[u] = GRAY
        for v in graph.get(u, []):
            if v not in color:
                continue
            if color[v] == GRAY:
                return True
            if color[v] == WHITE and dfs(v):
                return True
        color[u] = BLACK
        return False

    for n in node_ids:
        if color[n] == WHITE and dfs(n):
            return True
    return False


def _reachable_from_entrypoints(entry_points: list[str], edge_pairs: list[tuple[str, str]]) -> set[str]:
    graph: dict[str, list[str]] = {}
    for a, b in edge_pairs:
        graph.setdefault(a, []).append(b)

    q = deque([ep for ep in entry_points if isinstance(ep, str)])
    seen: set[str] = set(q)

    while q:
        u = q.popleft()
        for v in graph.get(u, []):
            if v not in seen:
                seen.add(v)
                q.append(v)
    return seen


def _in_degrees(node_ids: set[str], edge_pairs: list[tuple[str, str]]) -> dict[str, int]:
    indeg = {n: 0 for n in node_ids}
    for _a, b in edge_pairs:
        if b in indeg:
            indeg[b] += 1
    return indeg


def _get_components(doc: dict[str, Any]) -> list[dict[str, Any]]:
    comps = doc.get("components") or []
    # Called only after schema validation, but keep it safe.
    return comps if isinstance(comps, list) else []


def _get_component_ids(components: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for c in components:
        if isinstance(c, dict):
            cid = c.get("id")
            if isinstance(cid, str):
                ids.append(cid)
    return ids


def _get_integrations_connection_ids(doc: dict[str, Any]) -> set[str]:
    integrations = doc.get("integrations") or {}
    conns = integrations.get("connections") or []
    ids: set[str] = set()
    if isinstance(conns, list):
        for c in conns:
            if isinstance(c, dict):
                cid = c.get("id")
                if isinstance(cid, str):
                    ids.add(cid)
    return ids


def _get_flow(doc: dict[str, Any]) -> dict[str, Any]:
    flow = doc.get("flow_structure") or {}
    return flow if isinstance(flow, dict) else {}


def _get_nodes(flow: dict[str, Any]) -> dict[str, Any]:
    nodes = flow.get("nodes") or {}
    return nodes if isinstance(nodes, dict) else {}


def _get_edges(flow: dict[str, Any]) -> list[dict[str, Any]]:
    edges = flow.get("edges") or []
    return edges if isinstance(edges, list) else []


def _get_entry_points(flow: dict[str, Any]) -> list[str]:
    eps = flow.get("entry_points") or []
    if not isinstance(eps, list):
        return []
    return [e for e in eps if isinstance(e, str)]


# ----------------------------------------------------------------------
# Rules
# ----------------------------------------------------------------------

def rule_duplicate_component_ids(doc: dict[str, Any]) -> list[ValidationErrorItem]:
    rid = "PIPESPEC-SEM-01"
    warnings: list[ValidationErrorItem] = []

    components = _get_components(doc)
    ids = _get_component_ids(components)

    seen: set[str] = set()
    dups: set[str] = set()
    for cid in ids:
        if cid in seen:
            dups.add(cid)
        seen.add(cid)

    if dups:
        warnings.append(
            _warn(
                rule_id=rid,
                message=f"Duplicate component ids found: {sorted(dups)}",
                instance_path="/components",
            )
        )
    return warnings


def rule_integration_references_resolve(doc: dict[str, Any]) -> list[ValidationErrorItem]:
    rid = "PIPESPEC-SEM-02"
    warnings: list[ValidationErrorItem] = []

    components = _get_components(doc)
    integration_ids = _get_integrations_connection_ids(doc)

    for cidx, c in enumerate(components):
        if not isinstance(c, dict):
            continue

        io_spec = c.get("io_spec") or []
        if isinstance(io_spec, list):
            for iidx, io in enumerate(io_spec):
                if not isinstance(io, dict):
                    continue
                conn_id = io.get("connection_id")
                if conn_id is None:
                    continue
                if isinstance(conn_id, str) and conn_id not in integration_ids:
                    warnings.append(
                        _warn(
                            rule_id=rid,
                            message=f"io_spec.connection_id '{conn_id}' not found in integrations.connections[].id",
                            instance_path=f"/components/{cidx}/io_spec/{iidx}/connection_id",
                        )
                    )

        conn_refs = c.get("connections") or []
        if isinstance(conn_refs, list):
            for ridx, ref in enumerate(conn_refs):
                if not isinstance(ref, dict):
                    continue
                cid = ref.get("id")
                if isinstance(cid, str) and cid not in integration_ids:
                    warnings.append(
                        _warn(
                            rule_id=rid,
                            message=f"connections[].id '{cid}' not found in integrations.connections[].id",
                            instance_path=f"/components/{cidx}/connections/{ridx}/id",
                        )
                    )

    return warnings


def rule_flow_cross_references(doc: dict[str, Any]) -> list[ValidationErrorItem]:
    """
    Covers:
    - entry_points exist in nodes
    - edges refer to existing nodes
    """
    rid = "PIPESPEC-SEM-03"
    warnings: list[ValidationErrorItem] = []

    flow = _get_flow(doc)
    nodes = _get_nodes(flow)
    node_ids = set(nodes.keys())

    entry_points = _get_entry_points(flow)
    for eidx, ep in enumerate(entry_points):
        if ep not in node_ids:
            warnings.append(
                _warn(
                    rule_id=rid,
                    message=f"entry_points contains '{ep}' not present in flow_structure.nodes",
                    instance_path=f"/flow_structure/entry_points/{eidx}",
                )
            )

    edges = _get_edges(flow)
    for eidx, edge in enumerate(edges):
        if not isinstance(edge, dict):
            continue
        frm = edge.get("from")
        to = edge.get("to")
        if isinstance(frm, str) and frm not in node_ids:
            warnings.append(
                _warn(
                    rule_id=rid,
                    message=f"edge.from '{frm}' not present in flow_structure.nodes",
                    instance_path=f"/flow_structure/edges/{eidx}/from",
                )
            )
        if isinstance(to, str) and to not in node_ids:
            warnings.append(
                _warn(
                    rule_id=rid,
                    message=f"edge.to '{to}' not present in flow_structure.nodes",
                    instance_path=f"/flow_structure/edges/{eidx}/to",
                )
            )

    return warnings


def rule_node_component_ids_resolve(doc: dict[str, Any]) -> list[ValidationErrorItem]:
    rid = "PIPESPEC-SEM-04"
    warnings: list[ValidationErrorItem] = []

    components = _get_components(doc)
    component_ids = set(_get_component_ids(components))

    flow = _get_flow(doc)
    nodes = _get_nodes(flow)

    for nid, n in nodes.items():
        if not isinstance(n, dict):
            continue
        ctid = n.get("component_type_id")
        if isinstance(ctid, str) and ctid not in component_ids:
            warnings.append(
                _warn(
                    rule_id=rid,
                    message=f"node.component_type_id '{ctid}' not found among components[].id",
                    instance_path=f"/flow_structure/nodes/{nid}/component_type_id",
                )
            )

    return warnings


def rule_edge_self_loops_and_duplicates(doc: dict[str, Any]) -> list[ValidationErrorItem]:
    rid = "PIPESPEC-SEM-05"
    warnings: list[ValidationErrorItem] = []

    flow = _get_flow(doc)
    edges = _get_edges(flow)

    seen_edges: set[tuple[str, str, str, str]] = set()
    dup_edges: set[tuple[str, str, str, str]] = set()

    for eidx, edge in enumerate(edges):
        if not isinstance(edge, dict):
            continue

        frm = edge.get("from")
        to = edge.get("to")

        if isinstance(frm, str) and isinstance(to, str) and frm == to:
            warnings.append(
                _warn(
                    rule_id=rid,
                    message=f"Self-loop edge detected: '{frm}' -> '{to}'. This is unusual for DAG pipelines.",
                    instance_path=f"/flow_structure/edges/{eidx}",
                )
            )

        k = _edge_key(edge)
        if k in seen_edges:
            dup_edges.add(k)
        else:
            seen_edges.add(k)

    if dup_edges:
        warnings.append(
            _warn(
                rule_id=rid,
                message=f"Duplicate edges detected (same from/to/edge_type/condition): {sorted(dup_edges)}",
                instance_path="/flow_structure/edges",
            )
        )

    return warnings


def rule_dag_cycle_detection(doc: dict[str, Any]) -> list[ValidationErrorItem]:
    rid = "PIPESPEC-SEM-06"
    warnings: list[ValidationErrorItem] = []

    flow = _get_flow(doc)
    pattern = flow.get("pattern")
    if not isinstance(pattern, str):
        return warnings

    # Only enforce DAG-like constraint when pattern != loop
    if pattern == "loop":
        return warnings

    nodes = _get_nodes(flow)
    node_ids = set(nodes.keys())
    edges = _get_edges(flow)

    if not node_ids:
        return warnings

    edge_pairs = _build_edge_pairs(edges, node_ids)

    if _detect_cycle(node_ids, edge_pairs):
        warnings.append(
            _warn(
                rule_id=rid,
                message=(
                    "Cycle detected in flow_structure (pipeline is not a DAG). "
                    "If this is intentional looping behaviour, set flow_structure.pattern='loop'."
                ),
                instance_path="/flow_structure/edges",
            )
        )

    return warnings


def rule_reachability_and_entrypoint_sanity(doc: dict[str, Any]) -> list[ValidationErrorItem]:
    rid = "PIPESPEC-SEM-07"
    warnings: list[ValidationErrorItem] = []

    flow = _get_flow(doc)
    nodes = _get_nodes(flow)
    node_ids = set(nodes.keys())
    edges = _get_edges(flow)
    entry_points = _get_entry_points(flow)

    if not node_ids or not entry_points:
        return warnings

    edge_pairs = _build_edge_pairs(edges, node_ids)

    reachable = _reachable_from_entrypoints(entry_points, edge_pairs)
    unreachable = sorted([n for n in node_ids if n not in reachable])
    if unreachable:
        warnings.append(
            _warn(
                rule_id=rid,
                message=f"Unreachable node(s): {unreachable}. Not reachable from entry_points={entry_points}.",
                instance_path="/flow_structure/nodes",
            )
        )

    indeg = _in_degrees(node_ids, edge_pairs)
    bad_eps = sorted([ep for ep in entry_points if indeg.get(ep, 0) > 0])
    if bad_eps:
        warnings.append(
            _warn(
                rule_id=rid,
                message=f"Entry point(s) have non-zero in-degree (suspicious): {bad_eps}.",
                instance_path="/flow_structure/entry_points",
            )
        )

    return warnings


SEMANTIC_RULES: list[SemanticRule] = [
    SemanticRule(
        rule_id="PIPESPEC-SEM-01",
        description="Component ids should be unique.",
        severity="warning",
        apply=rule_duplicate_component_ids,
    ),
    SemanticRule(
        rule_id="PIPESPEC-SEM-02",
        description="Integration references should resolve.",
        severity="warning",
        apply=rule_integration_references_resolve,
    ),
    SemanticRule(
        rule_id="PIPESPEC-SEM-03",
        description="Flow cross-references should resolve (entry_points, edges endpoints).",
        severity="warning",
        apply=rule_flow_cross_references,
    ),
    SemanticRule(
        rule_id="PIPESPEC-SEM-04",
        description="Nodes should reference existing components via component_type_id.",
        severity="warning",
        apply=rule_node_component_ids_resolve,
    ),
    SemanticRule(
        rule_id="PIPESPEC-SEM-05",
        description="Self-loop edges and duplicate edges should not exist.",
        severity="warning",
        apply=rule_edge_self_loops_and_duplicates,
    ),
    SemanticRule(
        rule_id="PIPESPEC-SEM-06",
        description="When flow_structure.pattern != loop, the graph should be acyclic (DAG).",
        severity="warning",
        apply=rule_dag_cycle_detection,
    ),
    SemanticRule(
        rule_id="PIPESPEC-SEM-07",
        description="Nodes should be reachable from entry_points; entry_points should have indegree 0.",
        severity="warning",
        apply=rule_reachability_and_entrypoint_sanity,
    ),
]


def run_semantic_checks(doc: dict[str, Any]) -> list[ValidationErrorItem]:
    """
    Run all semantic rules and return warnings/errors as ValidationErrorItem objects.

    In v1, semantic checks are warnings-only from the validator perspective.
    """
    out: list[ValidationErrorItem] = []
    for rule in SEMANTIC_RULES:
        try:
            out.extend(rule.apply(doc))
        except Exception as e:
            # Semantic checks should never crash validation.
            out.append(
                ValidationErrorItem(
                    kind="semantic",
                    message=f"Semantic rule {rule.rule_id} crashed: {e}",
                    instance_path="",
                    schema_path="",
                    details={"rule_id": rule.rule_id, "exception": repr(e)},
                )
            )
    return out