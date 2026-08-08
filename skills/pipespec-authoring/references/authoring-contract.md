# PipeSpec authoring contract

## Inputs

- source pipeline request or description;
- source identifier and version when available;
- applicable project constraints;
- finite clarification and revision budget.

## Outputs

- candidate PipeSpec v1 document;
- schema and semantic validation results;
- assumptions and unresolved items;
- provenance linking the candidate to its source evidence;
- explicit status: `candidate`, `review_required`, or `stopped`.

## Stopping conditions

Stop when validation succeeds and the evidence bundle is complete, the revision budget is exhausted, required information remains unavailable, or satisfying validation would materially change source intent.

Acceptance belongs to a human or an explicitly configured policy gate outside this skill.
