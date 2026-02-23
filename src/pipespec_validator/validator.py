from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import yaml
from jsonschema import Draft7Validator

from .errors import PipeSpecParseError, PipeSpecSchemaLoadError
from .models import ValidationErrorItem, ValidationResult


SCHEMA_VERSION = "1.0"
BUNDLED_SCHEMA_NAME = "pipespec_schema_v1.json"


def _json_pointer(path_parts: Iterable[Any]) -> str:
    """
    Convert jsonschema error.absolute_path (deque-like) into JSON Pointer.
    """
    parts = []
    for p in path_parts:
        if isinstance(p, int):
            parts.append(str(p))
        else:
            s = str(p).replace("~", "~0").replace("/", "~1")
            parts.append(s)
    return "/" + "/".join(parts) if parts else ""


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    """
    Load the PipeSpec v1 schema.

    We keep a copy of the schema inside the Python package at:
      src/pipespec_validator/data/pipespec_schema_v1.json
    """
    try:
        # Python 3.10+ importlib.resources usage
        from importlib import resources
        schema_text = resources.files("pipespec_validator.data").joinpath(BUNDLED_SCHEMA_NAME).read_text(encoding="utf-8")
        return json.loads(schema_text)
    except Exception as e:  # pragma: no cover
        raise PipeSpecSchemaLoadError(f"Failed to load bundled schema '{BUNDLED_SCHEMA_NAME}': {e}") from e


def _load_json_or_yaml(path: Path) -> Any:
    """
    Parse JSON or YAML into Python objects.

    - If extension is .json => parse as JSON only
    - If extension is .yaml/.yml => parse as YAML only
    - Otherwise: try JSON then YAML
    """
    raw = path.read_text(encoding="utf-8")

    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            return json.loads(raw)
        except Exception as e:
            raise PipeSpecParseError(f"Failed to parse JSON file: {path}: {e}") from e

    if suffix in {".yaml", ".yml"}:
        try:
            return yaml.safe_load(raw)
        except Exception as e:
            raise PipeSpecParseError(f"Failed to parse YAML file: {path}: {e}") from e

    # fallback autodetect
    try:
        return json.loads(raw)
    except Exception:
        try:
            return yaml.safe_load(raw)
        except Exception as e:
            raise PipeSpecParseError(f"Failed to parse as JSON or YAML: {path}: {e}") from e


def validate_dict(
    doc: dict[str, Any],
    *,
    semantic_checks: bool = False,
) -> ValidationResult:
    schema = load_schema()
    validator = Draft7Validator(schema)

    errors: list[ValidationErrorItem] = []
    warnings: list[ValidationErrorItem] = []

    for err in sorted(validator.iter_errors(doc), key=lambda e: (list(e.absolute_path), e.message)):
        errors.append(
            ValidationErrorItem(
                kind="schema",
                message=err.message,
                instance_path=_json_pointer(err.absolute_path),
                schema_path=_json_pointer(err.absolute_schema_path),
                details={
                    "validator": err.validator,
                    "validator_value": err.validator_value,
                },
            )
        )

    # Semantic checks assume the document already satisfies the schema
    # (correct types for components/nodes/edges/etc). If schema errors exist,
    # skip semantic checks to avoid secondary crashes/noise.
    if semantic_checks and not errors:
        warnings.extend(_semantic_checks(doc))

    ok = len(errors) == 0
    return ValidationResult(ok=ok, schema_version=SCHEMA_VERSION, errors=errors, warnings=warnings)


def validate_file(
    path: str | Path,
    *,
    semantic_checks: bool = False,
) -> ValidationResult:
    p = Path(path)
    if not p.exists():
        return ValidationResult(
            ok=False,
            schema_version=SCHEMA_VERSION,
            errors=[ValidationErrorItem(kind="parse", message=f"File not found: {p}", instance_path="")],
            warnings=[],
        )

    try:
        loaded = _load_json_or_yaml(p)
    except PipeSpecParseError as e:
        return ValidationResult(
            ok=False,
            schema_version=SCHEMA_VERSION,
            errors=[ValidationErrorItem(kind="parse", message=str(e), instance_path="")],
            warnings=[],
        )

    if not isinstance(loaded, dict):
        return ValidationResult(
            ok=False,
            schema_version=SCHEMA_VERSION,
            errors=[
                ValidationErrorItem(
                    kind="parse",
                    message=f"Top-level document must be a JSON/YAML object (dict), got: {type(loaded).__name__}",
                    instance_path="",
                )
            ],
            warnings=[],
        )

    return validate_dict(loaded, semantic_checks=semantic_checks)


def _semantic_checks(doc: dict[str, Any]) -> list[ValidationErrorItem]:
    """
    Non-schema checks that are still useful when authoring PipeSpec.

    These are warnings by default (do not flip ok->False), because PipeSpec is
    an extraction artefact and may be partially incomplete.
    """
    warnings: list[ValidationErrorItem] = []

    components = doc.get("components") or []
    component_ids: list[str] = []
    for idx, c in enumerate(components):
        cid = c.get("id")
        if isinstance(cid, str):
            component_ids.append(cid)
        else:
            warnings.append(
                ValidationErrorItem(
                    kind="semantic",
                    message="Component missing string 'id' (cannot do cross-reference checks).",
                    instance_path=f"/components/{idx}",
                )
            )

    # duplicate component ids
    seen: set[str] = set()
    dups: set[str] = set()
    for cid in component_ids:
        if cid in seen:
            dups.add(cid)
        seen.add(cid)
    if dups:
        warnings.append(
            ValidationErrorItem(
                kind="semantic",
                message=f"Duplicate component ids found: {sorted(dups)}",
                instance_path="/components",
            )
        )

    # integration ids set
    integration_ids: set[str] = set()
    integrations = (doc.get("integrations") or {}).get("connections") or []
    for i in integrations:
        iid = i.get("id")
        if isinstance(iid, str):
            integration_ids.add(iid)

    # io_spec connection_id references
    for cidx, c in enumerate(components):
        io_spec = c.get("io_spec") or []
        for iidx, io in enumerate(io_spec):
            conn_id = io.get("connection_id")
            if conn_id is None:
                continue
            if isinstance(conn_id, str) and conn_id not in integration_ids:
                warnings.append(
                    ValidationErrorItem(
                        kind="semantic",
                        message=f"io_spec.connection_id '{conn_id}' not found in integrations.connections[].id",
                        instance_path=f"/components/{cidx}/io_spec/{iidx}/connection_id",
                    )
                )

        conn_refs = c.get("connections") or []
        for ridx, ref in enumerate(conn_refs):
            rid = ref.get("id")
            if isinstance(rid, str) and rid not in integration_ids:
                warnings.append(
                    ValidationErrorItem(
                        kind="semantic",
                        message=f"connections[].id '{rid}' not found in integrations.connections[].id",
                        instance_path=f"/components/{cidx}/connections/{ridx}/id",
                    )
                )

    # flow cross references
    flow = doc.get("flow_structure") or {}
    nodes = flow.get("nodes") or {}
    node_ids = set(nodes.keys())

    entry_points = flow.get("entry_points") or []
    for eidx, ep in enumerate(entry_points):
        if isinstance(ep, str) and ep not in node_ids:
            warnings.append(
                ValidationErrorItem(
                    kind="semantic",
                    message=f"entry_points contains '{ep}' not present in flow_structure.nodes",
                    instance_path=f"/flow_structure/entry_points/{eidx}",
                )
            )

    edges = flow.get("edges") or []
    for eidx, edge in enumerate(edges):
        frm = edge.get("from")
        to = edge.get("to")
        if isinstance(frm, str) and frm not in node_ids:
            warnings.append(
                ValidationErrorItem(
                    kind="semantic",
                    message=f"edge.from '{frm}' not present in flow_structure.nodes",
                    instance_path=f"/flow_structure/edges/{eidx}/from",
                )
            )
        if isinstance(to, str) and to not in node_ids:
            warnings.append(
                ValidationErrorItem(
                    kind="semantic",
                    message=f"edge.to '{to}' not present in flow_structure.nodes",
                    instance_path=f"/flow_structure/edges/{eidx}/to",
                )
            )

    # node component_type_id references
    for nid, n in nodes.items():
        ctid = n.get("component_type_id")
        if isinstance(ctid, str) and ctid not in seen:
            warnings.append(
                ValidationErrorItem(
                    kind="semantic",
                    message=f"node.component_type_id '{ctid}' not found among components[].id",
                    instance_path=f"/flow_structure/nodes/{nid}/component_type_id",
                )
            )

    return warnings