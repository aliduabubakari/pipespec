from __future__ import annotations

"""
hints.py
--------
Derives human-readable, actionable hints from validation errors.

Design principles:
- Hints are ALWAYS deterministic and computed from errors alone.
- Hints never invent content — they describe what is missing and why.
- Two tiers:
    TIER-1 (structural): AutoFix *could* handle with a safe placeholder.
                         Marked severity="medium". AutoFix will note these
                         were filled with defaults.
    TIER-2 (content):    Requires domain knowledge / original description.
                         Marked severity="high". Escalate to LLM tool.
"""

from dataclasses import dataclass, asdict
from typing import Any, Literal

from .models import ValidationErrorItem


HintSeverity = Literal["low", "medium", "high"]
HintTier = Literal["structural", "content", "semantic"]


@dataclass(frozen=True)
class Hint:
    code: str
    tier: HintTier          # structural | content | semantic
    severity: HintSeverity
    message: str
    paths: list[str]
    suggested_action: str   # plain-english next step
    details: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _path_top(instance_path: str) -> str:
    """Extract the first path segment, e.g. '/components/0/category' → 'components'."""
    return (instance_path or "").lstrip("/").split("/")[0]


def _component_index(instance_path: str) -> str | None:
    """Extract component index from a path like /components/0/... → '0'."""
    parts = (instance_path or "").lstrip("/").split("/")
    if len(parts) >= 2 and parts[0] == "components":
        return parts[1]
    return None


# ---------------------------------------------------------------------------
# Hint generators — one per recognisable error pattern
# ---------------------------------------------------------------------------

def _hints_missing_required(errors: list[ValidationErrorItem]) -> list[Hint]:
    """
    Group 'X is a required property' errors by their parent path.
    Each group becomes one hint.
    """
    hints: list[Hint] = []

    # parent_path → list of missing field names
    missing_by_path: dict[str, list[str]] = {}
    for e in errors:
        if e.kind != "schema":
            continue
        msg = e.message or ""
        if "is a required property" not in msg:
            continue
        # message format: "'field_name' is a required property"
        if not msg.startswith("'"):
            continue
        field = msg.split("'")[1]
        path = e.instance_path or ""
        missing_by_path.setdefault(path, []).append(field)

    for path, fields in missing_by_path.items():
        fields_sorted = sorted(set(fields))
        top = _path_top(path)

        # Classify: are any of these content fields (not safe to stub)?
        content_fields = {"category", "executor_type", "name"}
        structural_fields = {"io_spec", "retry_policy", "concurrency", "datasets",
                             "upstream_policy", "connections", "flow_patterns", "complexity"}

        needs_content = any(f in content_fields for f in fields_sorted)
        needs_structure = any(f in structural_fields for f in fields_sorted)

        if needs_content:
            tier: HintTier = "content"
            severity: HintSeverity = "high"
            suggested_action = (
                "These fields require domain knowledge from the original pipeline description. "
                "Use `pipespec correct --description <pipeline.txt> --in <spec> --out <fixed_spec>` "
                "to fill them in."
            )
        elif needs_structure:
            tier = "structural"
            severity = "medium"
            suggested_action = (
                "These structural fields can be safely stubbed with defaults. "
                "Re-run with --autofix to apply FIX-COMP-0x stubs, then review."
            )
        else:
            tier = "structural"
            severity = "medium"
            suggested_action = "Re-run --autofix; missing structural fields can be stubbed."

        hints.append(
            Hint(
                code="HINT-MISSING-REQUIRED",
                tier=tier,
                severity=severity,
                message=(
                    f"Object at {path!r} is missing required field(s): "
                    f"{', '.join(fields_sorted)}."
                ),
                paths=[path],
                suggested_action=suggested_action,
                details={
                    "missing_fields": fields_sorted,
                    "parent_segment": top,
                    "needs_llm": needs_content,
                },
            )
        )

    return hints


def _hints_wrong_type(errors: list[ValidationErrorItem]) -> list[Hint]:
    """Known type-mismatch patterns with specific guidance."""
    hints: list[Hint] = []

    for e in errors:
        if e.kind != "schema":
            continue
        msg = e.message or ""
        path = e.instance_path or ""

        if path == "/components" and "is not of type 'array'" in msg:
            hints.append(
                Hint(
                    code="HINT-COMPONENTS-NOT-ARRAY",
                    tier="structural",
                    severity="high",
                    message=(
                        "/components must be a JSON array of component objects, "
                        f"but found: {msg.split(' is')[0]}."
                    ),
                    paths=["/components"],
                    suggested_action="Re-run with --autofix; FIX-COMP-01 will convert it automatically.",
                    details={"autofix_code": "FIX-COMP-01"},
                )
            )

        elif path == "/flow_structure/nodes" and "is not of type 'object'" in msg:
            hints.append(
                Hint(
                    code="HINT-NODES-NOT-MAP",
                    tier="structural",
                    severity="high",
                    message=(
                        "/flow_structure/nodes must be a JSON object/map keyed by node id, "
                        f"but found an array or other type."
                    ),
                    paths=["/flow_structure/nodes"],
                    suggested_action="Re-run with --autofix; FIX-FLOW-01 will convert it automatically.",
                    details={"autofix_code": "FIX-FLOW-01"},
                )
            )

        elif path == "/flow_structure/entry_points" and "should be non-empty" in msg:
            hints.append(
                Hint(
                    code="HINT-EMPTY-ENTRY-POINTS",
                    tier="content",
                    severity="high",
                    message=(
                        "/flow_structure/entry_points is empty. "
                        "At least one node id must be declared as the pipeline's starting point."
                    ),
                    paths=["/flow_structure/entry_points"],
                    suggested_action=(
                        "Identify which component runs first and add its id here. "
                        "This cannot be inferred automatically — it requires knowledge "
                        "of the pipeline's intended execution order. "
                        "Example: entry_points: [\"extract_data\"]"
                    ),
                    details={"needs_human_review": True},
                )
            )

        elif "is not valid under any of the given schemas" in msg and "/flow_patterns" in path:
            hints.append(
                Hint(
                    code="HINT-INVALID-FLOW-PATTERN",
                    tier="content",
                    severity="medium",
                    message=f"Invalid flow_pattern value at {path!r}.",
                    paths=[path],
                    suggested_action=(
                        "Allowed values: sequential | parallel | dag | conditional | loop. "
                        "Correct the value manually or re-extract with the LLM tool."
                    ),
                )
            )

        elif "is not one of" in msg or "is not valid under any" in msg:
            if any(enum_path in path for enum_path in
                   ["/category", "/executor_type", "/edge_type", "/kind", "/complexity"]):
                hints.append(
                    Hint(
                        code="HINT-INVALID-ENUM",
                        tier="content",
                        severity="medium",
                        message=f"Invalid enum value at {path!r}: {msg}",
                        paths=[path],
                        suggested_action=(
                            "Check the allowed enum values in the schema and correct manually "
                            "or use the LLM escalation tool."
                        ),
                    )
                )

    return hints


def _hints_from_semantic(warnings: list[ValidationErrorItem]) -> list[Hint]:
    """
    Convert semantic rule warnings into hints with escalation guidance.
    Semantic rules only run when schema is valid, so these are post-schema hints.
    """
    hints: list[Hint] = []

    rule_guidance: dict[str, tuple[str, HintSeverity, str]] = {
        "PIPESPEC-SEM-01": (
            "HINT-DUPLICATE-IDS",
            "high",
            "Duplicate component ids cause ambiguity in flow references. "
            "Rename the duplicates manually.",
        ),
        "PIPESPEC-SEM-02": (
            "HINT-BROKEN-INTEGRATION-REF",
            "medium",
            "io_spec.connection_id or connections[].id references an integration that "
            "doesn't exist in integrations.connections[]. Add the missing connection or "
            "correct the reference.",
        ),
        "PIPESPEC-SEM-03": (
            "HINT-BROKEN-FLOW-REF",
            "high",
            "entry_points or edges reference node ids that don't exist in nodes. "
            "Re-run --autofix (FIX-FLOW-06) to remove dangling references, or "
            "add the missing nodes manually.",
        ),
        "PIPESPEC-SEM-04": (
            "HINT-BROKEN-COMPONENT-REF",
            "medium",
            "A node references a component_type_id that doesn't exist in components[].id. "
            "Check for id typos or use the LLM repair tool to re-extract flow_structure.",
        ),
        "PIPESPEC-SEM-05": (
            "HINT-EDGE-ANOMALY",
            "low",
            "Self-loop or duplicate edges detected. Remove the redundant edges manually.",
        ),
        "PIPESPEC-SEM-06": (
            "HINT-CYCLE-DETECTED",
            "high",
            "A cycle was detected in the flow graph. If this is intentional looping, "
            "set flow_structure.pattern='loop'. Otherwise, remove the cycle-causing edge.",
        ),
        "PIPESPEC-SEM-07": (
            "HINT-UNREACHABLE-NODE",
            "medium",
            "One or more nodes are unreachable from entry_points. Add missing edges or "
            "add the node to entry_points if it is a true starting point.",
        ),
    }

    for w in warnings:
        if w.kind != "semantic":
            continue
        rule_id = (w.details or {}).get("rule_id", "")
        if rule_id in rule_guidance:
            code, severity, action = rule_guidance[rule_id]
            hints.append(
                Hint(
                    code=code,
                    tier="semantic",
                    severity=severity,
                    message=w.message,
                    paths=[w.instance_path or ""],
                    suggested_action=action,
                    details={"rule_id": rule_id},
                )
            )

    return hints


def _hints_root_errors(errors: list[ValidationErrorItem]) -> list[Hint]:
    """Catch top-level missing required properties."""
    hints: list[Hint] = []

    root_missing: list[str] = []
    for e in errors:
        if e.kind != "schema":
            continue
        if (e.instance_path or "") in ("", "(root)") and "is a required property" in (e.message or ""):
            field = e.message.split("'")[1] if e.message.startswith("'") else ""
            if field:
                root_missing.append(field)

    if root_missing:
        structural = {"parameters", "integrations", "flow_structure", "pipespec_version"}
        content = {"pipeline_summary", "components", "metadata"}
        needs_content = any(f in content for f in root_missing)

        hints.append(
            Hint(
                code="HINT-MISSING-TOP-LEVEL",
                tier="content" if needs_content else "structural",
                severity="high",
                message=(
                    f"Document is missing required top-level key(s): "
                    f"{', '.join(sorted(root_missing))}."
                ),
                paths=[""],
                suggested_action=(
                    "Run --autofix for structural sections (parameters, integrations, "
                    "flow_structure). For content sections (components, pipeline_summary, "
                    "metadata), use the LLM extraction tool."
                ),
                details={"missing_keys": sorted(root_missing)},
            )
        )

    return hints


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_hints(
    errors: list[ValidationErrorItem],
    warnings: list[ValidationErrorItem] | None = None,
) -> list[Hint]:
    """
    Generate all applicable hints from schema errors and semantic warnings.
    Returns hints sorted by severity (high first).
    """
    all_hints: list[Hint] = []

    all_hints.extend(_hints_root_errors(errors))
    all_hints.extend(_hints_wrong_type(errors))
    all_hints.extend(_hints_missing_required(errors))
    if warnings:
        all_hints.extend(_hints_from_semantic(warnings))

    # Deduplicate by (code, path) to avoid repeats when multiple errors map to same hint
    seen: set[tuple[str, str]] = set()
    deduped: list[Hint] = []
    for h in all_hints:
        key = (h.code, h.paths[0] if h.paths else "")
        if key not in seen:
            seen.add(key)
            deduped.append(h)

    # Sort: high → medium → low, then by code
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(deduped, key=lambda h: (order.get(h.severity, 9), h.code))


def hints_to_dict(hints: list[Hint]) -> list[dict[str, Any]]:
    return [asdict(h) for h in hints]


def llm_escalation_needed(hints: list[Hint]) -> bool:
    """True if any hint requires LLM content knowledge to resolve."""
    return any(h.details and h.details.get("needs_llm", False) for h in hints) or \
           any(h.tier == "content" and h.severity == "high" for h in hints)


def escalation_summary(hints: list[Hint]) -> str:
    """
    Return a concise plain-text summary for CLI output when LLM escalation is needed.
    """
    content_hints = [h for h in hints if h.tier == "content" and h.severity == "high"]
    if not content_hints:
        return ""

    lines = [
        "  ⚠ LLM escalation recommended for the following content gaps:",
    ]
    for h in content_hints[:5]:
        path_str = h.paths[0] if h.paths else ""
        fields = (h.details or {}).get("missing_fields", [])
        field_str = f" [{', '.join(fields)}]" if fields else ""
        lines.append(f"    • {h.code}{field_str} at {path_str!r}")
    if len(content_hints) > 5:
        lines.append(f"    ... and {len(content_hints) - 5} more (see --report for full list)")
    lines.append(
        "  → Run: pipespec correct --description <desc.txt> --in <file> --out <fixed_file>"
    )
    return "\n".join(lines)
