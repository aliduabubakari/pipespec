# Quickstart

## Validate a PipeSpec document

```bash
pipespec-validate schema/examples/airvisual_pipeline.pipespec.json --semantic
```

## YAML input (tooling convenience)

PipeSpec v1 is formally JSON, but the validator also accepts YAML input:

```bash
pipespec-validate pipeline.pipespec.yaml --semantic
```

## Validate all repository examples

```bash
python tools/validate_examples.py
```

or:

```bash
make validate-examples
```