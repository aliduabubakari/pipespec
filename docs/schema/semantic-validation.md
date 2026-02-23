# Semantic Validation (Structural Rules)

PipeSpec v1 is defined primarily by a JSON Schema (Draft-07). The schema guarantees **syntactic validity**
(field presence, types, enums, and allowed properties). However, some correctness requirements are **semantic**
and cannot be fully expressed in JSON Schema alone—especially graph/DAG integrity constraints.

The PipeSpec project therefore defines a set of **semantic validation rules** and ships a reference
implementation in `pipespec-validator`:

- CLI: `pipespec-validate FILE --semantic`
- Python API: `validate_file(..., semantic_checks=True)` / `validate_dict(..., semantic_checks=True)`

Semantic validation emits **warnings** by default (i.e., it does not change `ok=True/False`), because PipeSpec
documents are frequently LLM-extracted and may be partially incomplete. Consumers who need stricter enforcement
can treat warnings as failures in downstream tooling (future work may add a `--semantic-strict` mode).

---

## Concepts and terminology

- **Component**: an entry in `components[]` describing a unit of work.
- **Node**: an entry in `flow_structure.nodes` representing a runtime node in the pipeline graph.
- **Edge**: an entry in `flow_structure.edges[]` connecting two nodes.
- **Node ID**: the key in `flow_structure.nodes` (e.g. `"extract_api": {...}`).
- **Graph**: a directed graph `(V, E)` where `V = node_ids` and `E = {(from,to) for edges}`.

---

## Semantic rule identifiers

The reference implementation assigns stable rule identifiers to warnings via `details.rule_id`, e.g.:

- `PIPESPEC-SEM-06` (cycle detected when DAG is expected)

This supports auditing and consistent reporting across tools.

---

## Semantic rules (reference implementation)

### PIPESPEC-SEM-01 — Duplicate component IDs

**Goal:** detect duplicate component identifiers.

- Every `components[].id` **SHOULD** be unique.
- Duplicate IDs are emitted as a warning.

---

### PIPESPEC-SEM-02 — Integration references resolve

**Goal:** detect broken cross-references to integrations.

- For any `components[].io_spec[].connection_id` that is not null, the value **SHOULD** exist in
  `integrations.connections[].id`.
- For any `components[].connections[].id`, the value **SHOULD** exist in `integrations.connections[].id`.

Broken references are emitted as warnings.

---

### PIPESPEC-SEM-03 — Flow cross-references resolve (entry points + edges)

**Goal:** ensure the flow topology refers only to declared nodes.

- Each `flow_structure.entry_points[]` **SHOULD** be present as a key in `flow_structure.nodes`.
- For each edge in `flow_structure.edges[]`:
  - `edge.from` **SHOULD** exist in `flow_structure.nodes`
  - `edge.to` **SHOULD** exist in `flow_structure.nodes`

Violations are emitted as warnings.

---

### PIPESPEC-SEM-04 — Nodes reference existing components

**Goal:** ensure nodes refer to valid components.

- Each `flow_structure.nodes[<node_id>].component_type_id` **SHOULD** exist among `components[].id`.

Violations are emitted as warnings.

---

### PIPESPEC-SEM-05 — Self-loop edges and duplicate edges

**Goal:** detect suspicious or redundant edges.

- An edge with `from == to` is a self-loop and **SHOULD NOT** exist for DAG-like patterns.
- Duplicate edges (same `from`, `to`, `edge_type`, `condition`) **SHOULD NOT** exist.

Violations are emitted as warnings.

---

### PIPESPEC-SEM-06 — Cycle detection (DAG constraint)

**Goal:** enforce that pipelines declared as DAG-like are acyclic.

PipeSpec supports multiple topology patterns via `flow_structure.pattern`:

- If `flow_structure.pattern` is one of:
  - `sequential`, `parallel`, `dag`, `conditional`

  then the directed graph **SHOULD** be **acyclic** (a DAG).

- If `flow_structure.pattern` is `loop`, cycles **MAY** be present and no cycle warning is emitted.

If a cycle is detected while the pattern is not `loop`, a warning is emitted suggesting either:

- remove the cycle (fix the edges), or
- set `flow_structure.pattern: loop` if looping behavior is intentional.

---

### PIPESPEC-SEM-07 — Reachability from entry points + entry point sanity

**Goal:** detect disconnected subgraphs and suspicious entry point declarations.

- Every node in `flow_structure.nodes` **SHOULD** be reachable by directed traversal from at least one node in
  `flow_structure.entry_points`.
- Entry point nodes **SHOULD** have in-degree 0 (sanity/lint rule).

Violations are emitted as warnings.

---

## Extending semantic rules

Semantic validation is implemented as a rule registry in:

- `src/pipespec_validator/semantic_rules.py`

To add a new rule:

1. Implement a function:

   ```python
   def rule_my_new_rule(doc: dict[str, Any]) -> list[ValidationErrorItem]:
       ...
   ```

2. Register it in `SEMANTIC_RULES` with a unique `rule_id` and description.

Rules should be:
- deterministic
- side-effect free
- non-crashing (exceptions are caught and converted into a semantic warning)

---

## Compatibility and stability

- The **JSON Schema** is the normative syntax contract.
- Semantic rules are part of the PipeSpec v1 conformance guidance and are provided as a reference implementation.
- Rule identifiers (`PIPESPEC-SEM-XX`) are intended to be stable across v1.
