# Integration Guide

## End-to-end flow

1. Generate a PipeSpec from free-form description text.
2. Validate the generated document.
3. Correct the document if validation fails.

## Generate

```bash
pipespec generate \
  --in Pipeline_Description_Dataset/sample.txt \
  --out /tmp/sample.pipespec.yaml \
  --provider openai \
  --model gpt-4o-mini \
  --api-key-env OPENAI_API_KEY
```

## Validate

```bash
pipespec validate /tmp/sample.pipespec.yaml --semantic
```

## Correct

Structural correction only:

```bash
pipespec correct --in /tmp/sample.pipespec.yaml --out /tmp/sample.fixed.yaml
```

LLM-assisted correction:

```bash
pipespec correct \
  --in /tmp/sample.pipespec.yaml \
  --out /tmp/sample.repaired.yaml \
  --description Pipeline_Description_Dataset/sample.txt \
  --provider claude \
  --api-key-env ANTHROPIC_API_KEY
```

## Diff

Compare two specs semantically (useful for auditing different extraction runs):

```bash
pipespec diff --left /tmp/sample_run_a.yaml --right /tmp/sample_run_b.yaml
pipespec diff --left /tmp/sample_run_a.yaml --right /tmp/sample_run_b.yaml --json
```

## Provider credentials

- `openai`: `OPENAI_API_KEY`
- `claude`/`anthropic`: `ANTHROPIC_API_KEY`
- `deepinfra`: `DEEPINFRA_API_TOKEN` or `DEEPINFRA_API_KEY`
- `deepseek`: `DEEPSEEK_API_KEY`
- `openrouter`: `OPENROUTER_API_KEY`
- `ollama`: no key required by default local setup

Inspect provider defaults and current credential detection:

```bash
pipespec providers
pipespec providers --provider openai
pipespec providers --json
```

Optional OpenRouter headers:
- `PIPESPEC_OPENROUTER_SITE_URL` -> sent as `HTTP-Referer`
- `PIPESPEC_OPENROUTER_APP_NAME` -> sent as `X-Title`
