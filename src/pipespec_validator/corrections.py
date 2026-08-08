from __future__ import annotations

"""
corrections.py
--------------
Deterministic, safe autofixes for structurally malformed PipeSpec documents.

Design principles
-----------------
1.  SAFE:   Only fix what can be inferred from the document's own structure.
            Never invent domain content (component names, categories, etc.).
2.  ITERATIVE: `autofix_multi_round()` runs fix rounds until no progress is made.
               Progress = schema error count decreases OR new fix actions were applied.
3.  BOUNDED: Hard cap of MAX_ROUNDS to prevent infinite loops on pathological input.
4.  ESCALATE: When fixes stop making progress, remaining errors are flagged for
              LLM escalation via the hints system.

What each fix code covers
--------------------------
FIX-TOP-01  Add missing pipespec_version
FIX-TOP-02  Add missing parameters block (empty defaults)
FIX-TOP-03  Add missing integrations block (empty defaults)
FIX-TOP-04  Add missing flow_structure block (empty defaults)

FIX-COMP-01  components dict → list (inject id from key)
FIX-COMP-02  Add missing components as empty list
FIX-COMP-03  Normalize non-schema category values (e.g. SQLTransform → Transformer)
FIX-COMP-04  Inject stub io_spec for components missing it (placeholder, not content)

FIX-FLOW-01  flow_structure.nodes list → dict/map
FIX-FLOW-02  Add missing nodes as empty map
FIX-FLOW-03  Add missing edges as empty list
FIX-FLOW-04  Add missing entry_points as empty list
FIX-FLOW-05  Add missing pattern with default 'sequential'
FIX-FLOW-06  Remove dangling entry_points/edge endpoints not in nodes (semantic)
FIX-FLOW-07  Ensure every component has a corresponding Task node (stub)

FIX-PARAM-01  Add missing parameters sub-keys (pipeline/schedule/execution/components/environment)
FIX-INTEG-01  Add missing data_lineage sub-keys (sources/sinks/intermediate_datasets)
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from .io_utils import load_doc, write_doc
from .validator import SCHEMA_VERSION


MAX_ROUNDS = 5   # Safety cap for iterative autofix

FixSeverity = Literal["info", "warning"]


@dataclass(frozen=True)
class FixAction:
    code: str
    message: str
    path: str
    severity: FixSeverity = "info"
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_parameters_block() -> dict[str, Any]:
    return {
        "pipeline": {},
        "schedule": {},
        "execution": {},
        "components": {},
        "environment": {},
    }


def _default_integrations_block() -> dict[str, Any]:
    return {
        "connections": [],
        "data_lineage": {
            "sources": [],
            "sinks": [],
            "intermediate_datasets": [],
        },
    }


def _default_flow_structure_block() -> dict[str, Any]:
    return {
        "pattern": "sequential",
        "entry_points": [],
        "nodes": {},
        "edges": [],
    }


def _default_node_skeleton(component_id: str) -> dict[str, Any]:
    return {
        "kind": "Task",
        "component_type_id": component_id,
        "upstream_policy": {"type": "all_success", "timeout_seconds": None},
        "next_nodes": [],
        "branch_config": None,
        "sensor_config": None,
        "parallel_config": None,
    }


_STUB_IO_SPEC = [
    {
        "name": "unknown_input",
        "direction": "input",
        "kind": "file",
        "format": "unknown",
        "path_pattern": None,
        "connection_id": None,
    }
]

_CATEGORY_MAPPING: dict[str, str] = {
    "SQLTransform": "Transformer",
    "SqlTransform": "Transformer",
    "sql_transform": "Transformer",
    "sqltransform": "Transformer",
    "Enricher": "Transformer",
    "enricher": "Transformer",
    "FeatureEngineer": "FeatureEngineering",
    "feature_engineer": "FeatureEngineering",
    "feature_engineering": "FeatureEngineering",
    "Trainer": "ModelTraining",
    "trainer": "ModelTraining",
    "Training": "ModelTraining",
    "training": "ModelTraining",
    "ModelTrainer": "ModelTraining",
    "model_trainer": "ModelTraining",
    "Evaluator": "ModelEvaluation",
    "evaluator": "ModelEvaluation",
    "Evaluation": "ModelEvaluation",
    "evaluation": "ModelEvaluation",
    "Predictor": "ModelInference",
    "predictor": "ModelInference",
    "Inference": "ModelInference",
    "inference": "ModelInference",
    "Scorer": "ModelInference",
    "scorer": "ModelInference",
    "Aggregator": "Custom",
    "aggregator": "Custom",
    "Splitter": "Custom",
    "splitter": "Custom",
    "Merger": "Custom",
    "merger": "Custom",
    "Orchestrator": "Custom",
    "orchestrator": "Custom",
}

_VALID_CATEGORIES = {
    "Extractor", "Transformer", "Loader", "Reconciliator",
    "QualityCheck", "FeatureEngineering", "ModelTraining",
    "ModelEvaluation", "ModelInference", "Notifier", "Sensor", "Custom",
}

_VALID_EXECUTOR_TYPES = {
    "python", "http", "sql", "bash", "email", "docker", "custom",
}


def _normalize_category(cat: Any) -> Any:
    if not isinstance(cat, str):
        return cat
    return _CATEGORY_MAPPING.get(cat, cat)


def _normalize_executor_type(et: Any) -> Any:
    if not isinstance(et, str):
        return et
    lower = et.lower()
    if lower in _VALID_EXECUTOR_TYPES:
        return lower
    # Common aliases
    aliases = {
        "python_callable": "python",
        "pythoncallable": "python",
        "python_function": "python",
        "shell": "bash",
        "bash_script": "bash",
        "rest": "http",
        "rest_api": "http",
        "api": "http",
    }
    return aliases.get(lower, et)


# ---------------------------------------------------------------------------
# Single-round fix
# ---------------------------------------------------------------------------

def autofix_dict(doc: dict[str, Any]) -> tuple[dict[str, Any], list[FixAction]]:
    """
    Apply one round of deterministic, safe fixes.

    Returns:
        (fixed_doc, actions_applied)

    Will not make the document fully valid if it has content gaps
    (missing category, executor_type, etc.) — those require LLM escalation.
    """
    actions: list[FixAction] = []
    fixed = dict(doc)

    # ── A) Top-level structural defaults ────────────────────────────────────

    if "pipespec_version" not in fixed:
        fixed["pipespec_version"] = SCHEMA_VERSION
        actions.append(FixAction(
            code="FIX-TOP-01",
            message="Added missing top-level pipespec_version.",
            path="/pipespec_version",
            details={"value": SCHEMA_VERSION},
        ))

    if "parameters" not in fixed:
        fixed["parameters"] = _default_parameters_block()
        actions.append(FixAction(
            code="FIX-TOP-02",
            message="Added missing top-level parameters block with empty defaults.",
            path="/parameters",
        ))

    if "integrations" not in fixed:
        fixed["integrations"] = _default_integrations_block()
        actions.append(FixAction(
            code="FIX-TOP-03",
            message="Added missing top-level integrations block with empty defaults.",
            path="/integrations",
        ))

    if "flow_structure" not in fixed:
        fixed["flow_structure"] = _default_flow_structure_block()
        actions.append(FixAction(
            code="FIX-TOP-04",
            message="Added missing top-level flow_structure block with empty defaults.",
            path="/flow_structure",
        ))

    # ── B) components: dict → list ──────────────────────────────────────────

    comps = fixed.get("components")

    if isinstance(comps, dict):
        new_comps: list[dict[str, Any]] = []
        for key, value in comps.items():
            if not isinstance(value, dict):
                value = {"name": str(value)}
            c = dict(value)
            if "id" not in c:
                c["id"] = str(key)
            new_comps.append(c)
        fixed["components"] = new_comps
        actions.append(FixAction(
            code="FIX-COMP-01",
            message="Converted components from object/map to array and injected missing id fields.",
            path="/components",
            details={"original_kind": "object", "new_kind": "array", "count": len(new_comps)},
        ))

    if "components" not in fixed:
        fixed["components"] = []
        actions.append(FixAction(
            code="FIX-COMP-02",
            message="Added missing components as empty array.",
            path="/components",
            severity="warning",
        ))

    # ── C) Normalize component fields ───────────────────────────────────────

    comps_list = fixed.get("components", [])
    if isinstance(comps_list, list):
        for idx, c in enumerate(comps_list):
            if not isinstance(c, dict):
                continue

            # Category normalization
            old_cat = c.get("category")
            new_cat = _normalize_category(old_cat)
            if new_cat != old_cat and new_cat in _VALID_CATEGORIES:
                c["category"] = new_cat
                actions.append(FixAction(
                    code="FIX-COMP-03",
                    message=f"Normalized category '{old_cat}' → '{new_cat}'.",
                    path=f"/components/{idx}/category",
                ))

            # executor_type normalization (e.g. "python_callable" → "python")
            old_et = c.get("executor_type")
            new_et = _normalize_executor_type(old_et)
            if new_et != old_et and new_et in _VALID_EXECUTOR_TYPES:
                c["executor_type"] = new_et
                actions.append(FixAction(
                    code="FIX-COMP-03",
                    message=f"Normalized executor_type '{old_et}' → '{new_et}'.",
                    path=f"/components/{idx}/executor_type",
                ))

            # Stub io_spec if missing (structural placeholder — explicitly marked)
            if "io_spec" not in c:
                c["io_spec"] = _STUB_IO_SPEC.copy()
                actions.append(FixAction(
                    code="FIX-COMP-04",
                    message=(
                        f"Injected placeholder io_spec for component '{c.get('id', idx)}'. "
                        "Review and replace with actual I/O spec."
                    ),
                    path=f"/components/{idx}/io_spec",
                    severity="warning",
                    details={"stub": True, "review_required": True},
                ))

    # ── D) Normalize parameters sub-keys ────────────────────────────────────

    params = fixed.get("parameters")
    if isinstance(params, dict):
        changed = False
        for sub in ("pipeline", "schedule", "execution", "components", "environment"):
            if sub not in params:
                params[sub] = {}
                changed = True
        if changed:
            actions.append(FixAction(
                code="FIX-PARAM-01",
                message="Added missing parameters sub-keys (pipeline/schedule/execution/components/environment).",
                path="/parameters",
            ))

    # ── E) Normalize integrations sub-keys ──────────────────────────────────

    integrations = fixed.get("integrations")
    if isinstance(integrations, dict):
        changed = False
        if "connections" not in integrations:
            integrations["connections"] = []
            changed = True
        dl = integrations.get("data_lineage")
        if not isinstance(dl, dict):
            integrations["data_lineage"] = {"sources": [], "sinks": [], "intermediate_datasets": []}
            changed = True
        else:
            for sub in ("sources", "sinks", "intermediate_datasets"):
                if sub not in dl:
                    dl[sub] = []
                    changed = True
        if changed:
            actions.append(FixAction(
                code="FIX-INTEG-01",
                message="Added missing integrations sub-keys.",
                path="/integrations",
            ))

    # ── F) flow_structure structural fixes ───────────────────────────────────

    flow = fixed.get("flow_structure")
    if isinstance(flow, dict):

        if "pattern" not in flow:
            flow["pattern"] = "sequential"
            actions.append(FixAction(
                code="FIX-FLOW-05",
                message="Added missing flow_structure.pattern defaulting to 'sequential'.",
                path="/flow_structure/pattern",
            ))

        if "entry_points" not in flow:
            flow["entry_points"] = []
            actions.append(FixAction(
                code="FIX-FLOW-04",
                message="Added missing flow_structure.entry_points as empty array.",
                path="/flow_structure/entry_points",
            ))

        if "edges" not in flow:
            flow["edges"] = []
            actions.append(FixAction(
                code="FIX-FLOW-03",
                message="Added missing flow_structure.edges as empty array.",
                path="/flow_structure/edges",
            ))

        nodes = flow.get("nodes")

        # nodes list → dict
        if isinstance(nodes, list):
            node_map: dict[str, Any] = {}
            for n in nodes:
                if isinstance(n, str):
                    node_map[n] = _default_node_skeleton(n)
                elif isinstance(n, dict) and "id" in n:
                    node_map[str(n["id"])] = n
            flow["nodes"] = node_map
            actions.append(FixAction(
                code="FIX-FLOW-01",
                message="Converted flow_structure.nodes from array to object/map.",
                path="/flow_structure/nodes",
                details={"original_kind": "array", "new_kind": "object", "count": len(node_map)},
            ))

        if "nodes" not in flow:
            flow["nodes"] = {}
            actions.append(FixAction(
                code="FIX-FLOW-02",
                message="Added missing flow_structure.nodes as empty object/map.",
                path="/flow_structure/nodes",
            ))

        # ── G) Semantic structural fixes (no LLM needed) ────────────────────

        nodes = flow.get("nodes", {})
        node_ids: set[str] = set(nodes.keys()) if isinstance(nodes, dict) else set()
        edges = flow.get("edges", []) if isinstance(flow.get("edges"), list) else []
        entry_points = flow.get("entry_points", []) if isinstance(flow.get("entry_points"), list) else []

        # Remove dangling entry_points (FIX-FLOW-06)
        valid_eps = [ep for ep in entry_points if ep in node_ids]
        if len(valid_eps) != len(entry_points):
            removed = [ep for ep in entry_points if ep not in node_ids]
            flow["entry_points"] = valid_eps
            actions.append(FixAction(
                code="FIX-FLOW-06",
                message=f"Removed dangling entry_points not in nodes: {removed}.",
                path="/flow_structure/entry_points",
                details={"removed": removed},
            ))

        # Remove dangling edge endpoints (FIX-FLOW-06)
        valid_edges = []
        dangling_edges = []
        for edge in edges:
            if not isinstance(edge, dict):
                dangling_edges.append(edge)
                continue
            frm = edge.get("from")
            to = edge.get("to")
            if (frm in node_ids or frm is None) and (to in node_ids or to is None):
                valid_edges.append(edge)
            else:
                dangling_edges.append(edge)
        if dangling_edges:
            flow["edges"] = valid_edges
            actions.append(FixAction(
                code="FIX-FLOW-06",
                message=f"Removed {len(dangling_edges)} edge(s) with dangling node references.",
                path="/flow_structure/edges",
                details={"removed_count": len(dangling_edges)},
            ))

        # Stub missing Task nodes for known components (FIX-FLOW-07)
        comps_list2 = fixed.get("components", [])
        if isinstance(comps_list2, list) and isinstance(nodes, dict):
            stubbed = []
            for c in comps_list2:
                if not isinstance(c, dict):
                    continue
                cid = c.get("id")
                if isinstance(cid, str) and cid not in nodes:
                    nodes[cid] = _default_node_skeleton(cid)
                    stubbed.append(cid)
            if stubbed:
                actions.append(FixAction(
                    code="FIX-FLOW-07",
                    message=f"Added stub Task nodes for components missing from flow: {stubbed}.",
                    path="/flow_structure/nodes",
                    details={"stubbed_ids": stubbed},
                ))

    return fixed, actions


# ---------------------------------------------------------------------------
# Iterative autofix
# ---------------------------------------------------------------------------

def autofix_multi_round(
    doc: dict[str, Any],
    *,
    max_rounds: int = MAX_ROUNDS,
    semantic_checks: bool = False,
) -> tuple[dict[str, Any], list[FixAction], int]:
    """
    Run autofix_dict() in a loop until:
    - No new fix actions are produced (converged), OR
    - Schema error count stops decreasing (stuck), OR
    - max_rounds is reached.

    Returns:
        (final_doc, all_actions, rounds_run)
    """
    # Import here to avoid circular import
    from .validator import validate_dict

    all_actions: list[FixAction] = []
    current_doc = doc
    prev_error_count: int | None = None

    for round_num in range(1, max_rounds + 1):
        fixed, round_actions = autofix_dict(current_doc)
        all_actions.extend(round_actions)
        current_doc = fixed

        # Check progress
        result = validate_dict(current_doc, semantic_checks=False)
        current_error_count = len(result.errors)

        no_new_actions = len(round_actions) == 0
        no_progress = (
            prev_error_count is not None
            and current_error_count >= prev_error_count
        )
        converged = current_error_count == 0

        if converged or no_new_actions or no_progress:
            return current_doc, all_actions, round_num

        prev_error_count = current_error_count

    return current_doc, all_actions, max_rounds


# ---------------------------------------------------------------------------
# File-level convenience
# ---------------------------------------------------------------------------

def autofix_file(
    in_path: str,
    out_path: str,
    *,
    out_format: str | None = None,
    multi_round: bool = True,
    max_rounds: int = MAX_ROUNDS,
) -> tuple[dict[str, Any], list[FixAction]]:
    """
    Load JSON/YAML, apply autofix (single or multi-round), write result.

    - If out_format is None: preserve input format.
    - out_format may be 'json' or 'yaml'.
    """
    doc, fmt = load_doc(in_path)

    if multi_round:
        fixed, actions, _ = autofix_multi_round(doc, max_rounds=max_rounds)
    else:
        fixed, actions = autofix_dict(doc)

    final_fmt = fmt if out_format is None else out_format  # type: ignore[assignment]
    write_doc(fixed, out_path, final_fmt)                  # type: ignore[arg-type]
    return fixed, actions
