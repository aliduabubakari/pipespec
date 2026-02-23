from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


@dataclass(frozen=True)
class ValidationErrorItem:
    kind: Literal["parse", "schema", "semantic"]
    message: str
    instance_path: str = ""  # JSON Pointer, e.g. /components/0/id
    schema_path: str = ""    # JSON Pointer-ish for schema, e.g. /properties/components/type
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    schema_version: str
    errors: list[ValidationErrorItem] = field(default_factory=list)
    warnings: list[ValidationErrorItem] = field(default_factory=list)

    def raise_for_errors(self) -> None:
        if not self.ok:
            # Keep this minimal; callers can display rich output themselves.
            msgs = "\n".join(
                f"- [{e.kind}] {e.instance_path}: {e.message}".strip()
                for e in self.errors
            )
            raise ValueError(f"PipeSpec validation failed:\n{msgs}")