# PipeSpec JSON Schema (v1)

This folder contains the canonical JSON Schema for **PipeSpec v1**.

- Canonical versioned schema: `pipespec_schema_v1.json`
- "Latest" alias: `pipespec_schema.json` (currently refs v1)

## What is PipeSpec?

PipeSpec is a platform-agnostic, LLM-friendly extraction format describing a pipeline:
- pipeline summary metadata
- component (task) catalogue
- flow topology (nodes + edges)
- parameters and environment secret references
- external integrations catalogue + lineage

PipeSpec does **not** include full business logic payloads (SQL bodies, Python code).
It captures the orchestration skeleton, I/O, and operational metadata.

## JSON Schema details

- Draft: JSON Schema Draft-07
- Primary entrypoint: `pipespec_schema_v1.json`
- PipeSpec documents should use extension: `*.pipespec.json`

## Versioning rules (summary)

- `pipespec_version` is required and currently must be `"1.0"`.
- Backwards-compatible improvements (documentation clarifications, optional fields)
  may be released as `1.0.x` at the tooling level, but the schema remains `v1`
  unless the document contract changes.

## Examples

See `examples/` for valid PipeSpec documents.

## Fixtures

See `fixtures/` for intentionally invalid documents used by validator tests.

## Referencing

Consumers should reference the versioned schema for stability:

- `schema/pipespec_schema_v1.json`

The `pipespec_schema.json` file exists for convenience and may change to reference
a newer version in the future.

## JSON vs YAML

PipeSpec v1 is **defined as a JSON document format** (`*.pipespec.json`) validated against the JSON Schema.

However, the `pipespec-validate` CLI and Python API accept **either JSON or YAML input** as a convenience for users.
YAML input is parsed into an in-memory object and then validated against the same JSON Schema.