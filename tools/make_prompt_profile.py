from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


# Keep a small set of keys that are useful for prompting.
# We preserve:
# - structural keys: type/properties/items/required
# - constraint keys that help LLMs: enum/const
# - limited descriptions (truncated)
# - additionalProperties (important for shape)
KEEP_KEYS = {
    "type",
    "properties",
    "required",
    "items",
    "enum",
    "const",
    "additionalProperties",
    "anyOf",
    "oneOf",
    "allOf",
    "description",
    "$ref",
}

# These are often large and not very helpful to LLM prompting.
DROP_KEYS = {
    "$id",
    "$schema",
    "title",
    "examples",
    "default",
    "pattern",
    "format",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_local_ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"Only local refs supported for prompt profile generation: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    if not isinstance(node, dict):
        raise TypeError(f"$ref did not resolve to an object schema: {ref}")
    return node


def deref(root: dict[str, Any], schema: Any) -> Any:
    """
    Inline local $ref (#/definitions/...) recursively.

    This is for generating a self-contained prompt profile; it is not a formal schema processor.
    """
    if isinstance(schema, list):
        return [deref(root, x) for x in schema]
    if not isinstance(schema, dict):
        return schema

    if "$ref" in schema:
        ref_schema = deref(root, resolve_local_ref(root, schema["$ref"]))
        merged = dict(ref_schema)
        for k, v in schema.items():
            if k == "$ref":
                continue
            merged[k] = deref(root, v)
        schema = merged

    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k == "definitions":
            continue
        out[k] = deref(root, v)
    return out


def strip_for_prompting(schema: Any, *, max_description_len: int) -> Any:
    if isinstance(schema, list):
        return [strip_for_prompting(x, max_description_len=max_description_len) for x in schema]
    if not isinstance(schema, dict):
        return schema

    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k in DROP_KEYS:
            continue
        if k not in KEEP_KEYS and k != "definitions":
            # drop unknown keys to keep profile small/stable
            continue

        if k == "description" and isinstance(v, str):
            vv = v.strip()
            if len(vv) > max_description_len:
                vv = vv[:max_description_len].rstrip() + "…"
            out[k] = vv
            continue

        out[k] = strip_for_prompting(v, max_description_len=max_description_len)

    return out


def add_banner(profile: dict[str, Any]) -> dict[str, Any]:
    banner = {
        "x_non_normative": True,
        "x_purpose": (
            "Prompt profile for LLM extraction. "
            "Generated from the canonical PipeSpec schema. "
            "Not intended for validation; use pipespec_schema_v1.json for validation."
        ),
        "x_generation": {
            "source_schema": "schema/pipespec_schema_v1.json",
            "deref_refs": True,
            "notes": [
                "This file is dereferenced (no $ref dependencies).",
                "Descriptions are truncated; some constraints are removed to reduce size.",
            ],
        },
    }
    return {**banner, **profile}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate non-normative PipeSpec prompt profile from canonical schema.")
    parser.add_argument(
        "--in",
        dest="inp",
        type=Path,
        default=Path("schema/pipespec_schema_v1.json"),
        help="Input canonical schema path",
    )
    parser.add_argument(
        "--out",
        dest="out",
        type=Path,
        default=Path("schema/pipespec_prompt_profile_v1.json"),
        help="Output prompt profile path",
    )
    parser.add_argument(
        "--max-desc",
        type=int,
        default=160,
        help="Max length for description fields",
    )
    args = parser.parse_args()

    root = load_json(args.inp)
    deref_profile = deref(root, root)
    stripped = strip_for_prompting(deref_profile, max_description_len=args.max_desc)

    # Ensure we keep the core title/description if present, but not required.
    profile = add_banner(stripped)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote prompt profile: {args.out}")


if __name__ == "__main__":
    main()