# PipeSpec (v1)

PipeSpec is a platform-agnostic, LLM-friendly extraction format for describing data pipelines as structured documents.

This repository contains:

- The **canonical PipeSpec v1 JSON Schema**: `schema/pipespec_schema_v1.json`
- Example PipeSpec documents: `schema/examples/`
- A Python package + CLI (`pipespec-validator`) to validate PipeSpec documents

## Status

- PipeSpec schema: **v1 (stable)**
- Validator tooling: **v1**

## What PipeSpec is (and isn’t)

PipeSpec captures the *orchestration skeleton*:

- pipeline summary
- components / tasks (category taxonomy, executor types)
- flow topology (nodes + edges)
- structured I/O specs (inputs/outputs)
- parameters and environment-secret *references* (no secret values)
- external integrations catalogue + coarse lineage

PipeSpec does **not** embed business logic payloads (full SQL, Python scripts, etc.).

## JSON vs YAML

PipeSpec v1 is **defined as JSON** (`*.pipespec.json`) validated against JSON Schema Draft-07.

Tooling convenience: the validator also accepts YAML (`*.pipespec.yaml` / `*.yml`) by parsing YAML into a Python dict and validating it against the same schema.

## Install validator

```bash
python -m pip install pipespec-validator
```

Or for development:

```bash
python -m pip install -e ".[dev]"
```

## Validate a PipeSpec document

Validate JSON:

```bash
pipespec-validate schema/examples/airvisual_pipeline.pipespec.json --semantic
```

Validate YAML (tooling convenience):

```bash
pipespec-validate path/to/pipeline.pipespec.yaml --semantic
```

Exit codes:

- `0` = valid
- `2` = invalid (parse/schema failure)

## Canonical schema

- `schema/pipespec_schema_v1.json` is the normative v1 schema.
- `schema/pipespec_schema.json` is a convenience alias referencing the latest stable version.

For production / research reproducibility, pin to the versioned schema file.

## Development quickstart

```bash
make install
make check-schema-sync
make test
make validate-examples
```

## License

Apache-2.0# pipespec
