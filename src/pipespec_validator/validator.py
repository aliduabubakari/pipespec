from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import yaml
from jsonschema import Draft7Validator

from .errors import PipeSpecParseError, PipeSpecSchemaLoadError
from .models import ValidationErrorItem, ValidationResult
from .semantic_rules import run_semantic_checks


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
        from importlib import resources

        schema_text = (
            resources.files("pipespec_validator.data")
            .joinpath(BUNDLED_SCHEMA_NAME)
            .read_text(encoding="utf-8")
        )
        return json.loads(schema_text)
    except Exception as e:  # pragma: no cover
        raise PipeSpecSchemaLoadError(
            f"Failed to load bundled schema '{BUNDLED_SCHEMA_NAME}': {e}"
        ) from e


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

    # Semantic checks assume the document already satisfies the schema.
    # If schema errors exist, skip semantic checks to avoid noise.
    if semantic_checks and not errors:
        warnings.extend(run_semantic_checks(doc))

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