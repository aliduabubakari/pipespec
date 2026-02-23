# Contributing

This is a schema-first repository.

Guidelines:
- Changes to `schema/pipespec_schema_v1.json` should include:
  - rationale
  - updated examples (if applicable)
  - updated fixtures (if applicable)

Always run:
- `make check-schema-sync`
- `pytest`