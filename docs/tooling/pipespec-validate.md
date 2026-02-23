# pipespec-validate CLI

## Validate a file

```bash
pipespec-validate path/to/file.pipespec.json
pipespec-validate path/to/file.pipespec.yaml
```

## Semantic checks

Enable semantic cross-reference checks (warnings):

```bash
pipespec-validate file.pipespec.json --semantic
```

Semantic checks run only if the document passes schema validation.