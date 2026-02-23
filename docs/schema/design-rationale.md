# Design Rationale

## Why JSON Schema?

PipeSpec is designed for:

- machine validation (schema conformance)
- LLM-guided structured extraction (schema-as-constraint)
- deterministic downstream compilation

## Why allow YAML in tooling but not the spec?

YAML input improves user ergonomics (hand-editing, readability) without complicating the standard.
The validator parses YAML into an object and validates it against the same JSON Schema.