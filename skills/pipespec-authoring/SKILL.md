---
name: pipespec-authoring
description: Create a reviewable PipeSpec v1 candidate from a pipeline request or source description, validate it, and report assumptions and unresolved information. Use when an agent must capture pipeline intent before deterministic compilation to OrchSpec.
---

# PipeSpec Authoring

Create a candidate PipeSpec that preserves the supplied pipeline intent. Treat review and acceptance as external decisions.

## Required workflow

1. Read the complete request and any source evidence.
2. Identify components, data artifacts, dependencies, integrations, parameters, constraints, and unresolved information.
3. Ask for information that is required for a valid or materially faithful design when the harness permits interaction. Otherwise, record the gap as an assumption or unresolved item. Never invent credentials, endpoints, legal obligations, or domain rules.
4. Draft a `*.pipespec.json` document using the repository-level `skills.md` as the detailed schema reference.
5. Keep secrets outside the document. Use environment or secret references where the schema permits them.
6. Run schema validation and then semantic validation:

```bash
pipespec-validate candidate.pipespec.json
pipespec-validate --semantic candidate.pipespec.json
```

7. Repair only the reported candidate-document faults. Repeat validation within the controller's finite attempt budget.
8. Return the candidate, validator output, assumptions, unresolved items, and source provenance. Mark its status as `candidate` or `review_required`.

## Authority boundary

- Do not label a PipeSpec as accepted.
- Do not compile to OrchSpec in this stage.
- Do not render target workflow code.
- Do not modify source evidence.
- Stop and request design review when a valid document would require changing the supplied pipeline intent.

Read `references/authoring-contract.md` for the expected evidence bundle and stopping conditions.
