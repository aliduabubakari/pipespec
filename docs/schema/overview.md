# Schema Overview

A PipeSpec v1 document has the following top-level structure:

- `pipespec_version`
- `metadata`
- `pipeline_summary`
- `components[]`
- `flow_structure`
- `parameters`
- `integrations`

The canonical schema is:

- `schema/pipespec_schema_v1.json`

## Validation

PipeSpec validation is defined at two levels:

1. **Schema validation** (JSON Schema Draft-07): required fields, types, enums.
2. **Semantic validation** (reference implementation): graph/DAG structural checks and cross-reference integrity.

See: `Semantic validation`.