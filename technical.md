# PipeSpec v1 — Technical Specification and Design Rationale (Prompt2Kube)

**Document status:** Living technical document  
**PipeSpec version:** 1.0 (public v1)  
**Schema draft:** JSON Schema Draft-07  
**Repository:** `pipespec`  
**Last updated:** 2026-02-23

---

## 1. Purpose and Scope

PipeSpec (Pipeline Specification) is a platform-agnostic, LLM-friendly extraction format for describing data
pipelines as structured documents. PipeSpec is designed as the **first step** in a deterministic compilation
toolchain:

1. Natural-language pipeline description (or source code / DAG / YAML)
2. **PipeSpec extraction (LLM)**
3. **PipeSpec validation (+ optional deterministic correction)**
4. PipeSpec → Universal Abstraction Layer (UAL) / OPOS (deterministic compiler)
5. OPOS → platform artefacts (deterministic compilers for target orchestrators)

This document defines PipeSpec v1’s **normative contract**, **validation model**, and **reference tooling**.
It also documents the design philosophy and the intended usage pattern in LLM-driven generation systems.

### 1.1 Non-goals

PipeSpec v1 explicitly does NOT aim to:

- encode the full business logic payload (SQL query bodies, Python script contents, etc.)
- guarantee runtime correctness or task success
- model all dynamic orchestration features (e.g., dynamic mapping) as first-class semantics
- replace platform-native execution specifications (it is an extraction and compilation input)

PipeSpec captures the orchestration skeleton: task boundaries, I/O declarations, topology, and operational metadata.

---

## 2. Design Assumptions, Applicability, and Boundaries (Bounded System Definition)

PipeSpec v1 is intentionally bounded. It is designed to describe a specific class of pipelines with high
portability and high extraction feasibility. This section defines the assumptions that bound PipeSpec v1’s
scope and clarifies what is out-of-scope unless the schema and tooling are extended.

### 2.1 Intended pipeline class (what PipeSpec v1 targets)

PipeSpec v1 targets **data engineering pipelines** whose primary purpose is **data preparation and movement**
(e.g., ETL/ELT-style workflows). Concretely, it targets pipelines where:

- work can be decomposed into discrete **components** (tasks/steps) with clear boundaries,
- components can be classified into the v1 taxonomy:
  - `Extractor`, `Transformer`, `Loader`, `Reconciliator`, `QualityCheck`, `Notifier`, `Sensor`, `Custom`,
- components interact with external systems via an **integrations catalogue** (APIs, databases, filesystems, etc.),
- the pipeline can be represented as a directed graph via `flow_structure` (`nodes` + `edges`).

These assumptions align PipeSpec with typical orchestration systems used in data engineering (Airflow/Kestra/Argo/etc.)
and enable deterministic downstream compilation.

### 2.2 Batch-first execution assumption

PipeSpec v1 is **batch-first**. It assumes that the pipeline is executed in **runs** (scheduled or manual)
and that each run processes a bounded unit of work (a time window, a partition, a snapshot, etc.).

- PipeSpec v1 MAY represent “stream-like” assets in `io_spec.kind` for descriptive purposes, but the execution
  semantics remain batch-oriented.
- Event-driven or unbounded streaming semantics are not first-class in v1.

Rationale: batch semantics are significantly easier to extract reliably from natural language and map to a wide range
of orchestrators using a deterministic compiler pipeline.

### 2.3 Deterministic, orchestration-level representation (not business logic)

PipeSpec v1 models the **orchestration skeleton**, not the full payload logic. As such:

- PipeSpec does not attempt to store complete SQL statements, Python scripts, or transformation code.
- Component logic is represented by metadata only (e.g., executor type and I/O boundaries).
- PipeSpec is intended to be paired with external code/artifacts that implement the task payloads.

This is an intentional design choice to prevent the specification from becoming a code container and to keep extraction
and validation tractable and reproducible.

### 2.4 Component classification assumption (taxonomy is prescriptive)

PipeSpec v1 includes a prescriptive component taxonomy to support downstream compilation. This implies:

- Each component SHOULD map reasonably to one of the v1 categories.
- If the pipeline contains work that does not fit the taxonomy, it SHOULD use `category: Custom` and/or
  `executor_type: custom`.

Important boundary: if a pipeline’s tasks are not meaningfully representable as Extractor/Transformer/Loader-style
units (e.g., training loops, hyperparameter tuning sweeps), the taxonomy will not provide strong guidance and the
resulting PipeSpec may be low quality without schema extensions.

### 2.5 DAG/topology assumption

PipeSpec v1 assumes the pipeline is representable as a directed graph:

- `flow_structure.nodes` is a node map keyed by node id.
- `flow_structure.edges` defines connectivity.
- For patterns `sequential`, `parallel`, `dag`, `conditional`, the topology is expected to be DAG-like
  (acyclic). If looping is intentional, the pattern SHOULD be declared as `loop`.

PipeSpec v1’s semantic validator provides warnings for cycles and reachability issues. Semantic checks are warnings-only
in v1 to support LLM-extracted, partially incomplete documents.

### 2.6 Environment and secrets assumption

PipeSpec v1 assumes secrets are not embedded. Secret values MUST NOT appear in the PipeSpec document. Instead, secrets
are represented symbolically (e.g., environment parameter names with null default values).

This boundary is necessary for safe open-source datasets and for compilation into platform secret systems.

---

## 2.7 Out-of-scope pipeline classes (v1)

The following pipeline classes are out-of-scope for PipeSpec v1 *unless the schema and tooling are extended*:

1. **Machine learning training pipelines (MLOps)**  
   Examples: model training/evaluation loops, hyperparameter tuning, feature store materialization, model registry workflows.  
   Why out-of-scope: require first-class modeling of datasets vs features, metrics, artifacts, training runs, model lineage,
   and often dynamic parallelism beyond DAG semantics.

2. **True streaming / event-driven pipelines**  
   Examples: Kafka/Flink/Spark Structured Streaming pipelines with unbounded processing, watermarking, exactly-once semantics.  
   Why out-of-scope: require continuous execution semantics and streaming-specific operators and state models.

3. **Interactive analytics / notebooks as orchestration units**  
   Examples: multi-user notebook-based workflows with interactive state.  
   Why out-of-scope: PipeSpec assumes non-interactive, run-based execution.

4. **Complex control-plane orchestration or infrastructure workflows**  
   Examples: cluster provisioning, infra-as-code workflows.  
   Why out-of-scope: requires a different integration and resource model; PipeSpec focuses on data movement and preparation.

---

## 2.8 Extension points (how the system can evolve)

PipeSpec v1 is designed to be extended without breaking the core architecture:

- **Schema extensions**:
  - new `executor_type` values (e.g., `spark`, `dbt`, `kafka_consumer`) can be introduced in a future version
  - new component categories can be added if a broader pipeline class is targeted
  - richer topology constructs can be added (e.g., dynamic mapping, fan-in/fan-out semantics)
- **Semantic rules**:
  - additional semantic rules can be added via the semantic rule registry (`semantic_rules.py`)
- **Tooling**:
  - deterministic AutoFix can grow to cover additional safe structural normalizations
  - optional tool-based LLM repair can be adapted to new schema versions

Any extension that changes the normative contract MUST be released as a new schema version (e.g., v1 → v2) with explicit
migration guidance.

---

## 2.9 Practical implication for LLM extraction

PipeSpec-guided extraction assumes the input description is sufficiently detailed to:
- identify components and their roles (taxonomy)
- describe at least basic I/O boundaries and external integrations
- describe high-level dependency order / topology

If a description does not contain enough information, the extractor SHOULD use null/empty values rather than inventing
details. Downstream deterministic tooling (validation + hints + correction) is used to surface gaps and guide either
manual completion or optional LLM escalation.

## 3. Normative vs Non-normative Artefacts

### 3.1 Normative artefact (the standard)

The following file is **normative** for PipeSpec v1 conformance:

- `schema/pipespec_schema_v1.json`

A document is “PipeSpec v1 conformant” if it validates successfully against this schema.

> Note: A convenience alias is provided as `schema/pipespec_schema.json`, which may reference the newest stable version.
> For production and reproducible research, consumers SHOULD pin to `pipespec_schema_v1.json`.

### 3.2 Tooling convenience formats (non-normative)

PipeSpec v1 is defined as JSON (`*.pipespec.json`). For user convenience, the validator tooling accepts YAML
(`*.pipespec.yaml` / `*.yml`) as an input syntax. YAML input is parsed into an in-memory object and validated
against the same JSON Schema.

### 3.3 Prompt profile (non-normative)

The following file is **non-normative** and exists purely to improve LLM prompting ergonomics:

- `schema/pipespec_prompt_profile_v1.json`

This file is generated from the canonical schema and is dereferenced and size-reduced. It MUST NOT be used for validation.
Validation MUST be performed against `pipespec_schema_v1.json`.

---

## 4. PipeSpec Document Model (Overview)

A PipeSpec v1 document is a single JSON object with these top-level keys:

- `pipespec_version` (must be `"1.0"`)
- `metadata` (extraction provenance)
- `pipeline_summary` (human-level summary)
- `components[]` (task catalogue)
- `flow_structure` (topology: nodes + edges)
- `parameters` (pipeline / schedule / execution / component / environment inputs)
- `integrations` (external system catalogue + coarse lineage)

### 4.1 Why both `components` and `flow_structure`?

- `components[]` defines what each unit of work is.
- `flow_structure` defines how those units connect and execute (graph topology).

This separation is deliberate:
- it avoids implicit task ordering assumptions,
- it makes DAG topology explicit,
- and it allows validation of cross-references and reachability.

---

## 5. Component Taxonomy and Executor Types

### 5.1 Component categories (normative enum)

PipeSpec v1 defines a fixed taxonomy to enable consistent extraction and downstream compilation:

- `Extractor`
- `Transformer`
- `Loader`
- `Reconciliator`
- `QualityCheck`
- `Notifier`
- `Sensor`
- `Custom`

These categories are intended to be inferable from natural-language descriptions and act as a stable abstraction layer.
Downstream compilers MAY use categories to infer defaults (resources, timeouts, templates).

### 5.2 Executor types (normative enum)

PipeSpec v1 defines a high-level executor taxonomy:

- `python`
- `http`
- `sql`
- `bash`
- `email`
- `docker`
- `custom`

This is intentionally coarse. The goal is portability and extraction feasibility, not runtime fidelity.

---

## 6. I/O Specification (io_spec) and Lineage

PipeSpec v1 requires structured I/O declarations per component via `io_spec[]` items:

- direction: `input | output`
- kind: `file | table | api | object | stream`
- format: free-form string (e.g., `json`, `csv`, `parquet`, `sql`)
- path_pattern: path/uri/pattern or null for in-memory objects
- connection_id: optional reference to an integration connection

Rationale:
- encourages explicit lineage and interoperability
- prevents compilers from inferring data flow from task order alone
- enables static reasoning about pipeline dependencies and artefacts

---

## 7. Integrations Catalogue

PipeSpec’s `integrations.connections[]` defines external system touchpoints:

- type: `api | database | filesystem | object_store | message_queue | smtp | other`
- config: free-form object (varies by type)
- authentication: free-form object (symbolic only; no secret values)
- used_by_components: component id list
- direction: `input | output | both`

PipeSpec also includes `integrations.data_lineage` as a coarse, human-readable lineage summary:
- sources[]
- sinks[]
- intermediate_datasets[]

---

## 8. Parameters and Secret Hygiene

PipeSpec defines `parameters` in five groups:

- `pipeline`
- `schedule`
- `execution`
- `components` (per-component keyed by component id)
- `environment` (environment variables, including secret references)

### 8.1 Secret values are out of scope

PipeSpec documents MUST NOT embed secret values. Instead:
- secret-like parameters are expressed as `parameters.environment.<NAME>.default = null`
- and `required = true` where applicable

This enables:
- secure templating at deployment time
- deterministic compilation without leaking credentials into artefacts

---

## 9. Validation Model

PipeSpec uses a multi-layer validation approach:

1. **Schema validation** (normative): JSON Schema Draft-07  
2. **Semantic validation** (reference rules): structural / DAG checks and cross-reference integrity  
3. **Reporting** (reference tooling): structured validation reports for CI/repair loops  
4. **Deterministic correction (AutoFix)** (reference tooling): safe structural normalization  
5. **Optional LLM repair escalation** (tooling): for content gaps that cannot be inferred deterministically

### 9.1 Schema validation (normative)

A conformant PipeSpec v1 document MUST validate against:

- `schema/pipespec_schema_v1.json`

### 9.2 Semantic validation (reference rules; warnings)

Some correctness rules cannot be represented cleanly in Draft-07 JSON Schema (e.g., graph cycles, reachability).
The validator provides semantic checks as warnings (v1).

Semantic rules are implemented as a rule registry in:

- `src/pipespec_validator/semantic_rules.py`

Warnings include stable rule identifiers (`PIPESPEC-SEM-XX`) in `details.rule_id`.

Key semantic checks include:
- duplicate component ids
- broken cross-references (integration IDs, node references)
- DAG acyclicity when `flow_structure.pattern != loop`
- unreachable nodes from entry_points

### 9.3 Validation reports

The CLI and Python API can emit structured validation reports containing:
- schema id + schema version
- error/warning counts
- detailed error items (paths, messages, schema_path, rule_id)
- optional correction actions and post-correction validation results

This is intended to support:
- CI artifacts
- human debugging
- LLM repair loops (error feedback)

---

## 10. Deterministic Correction (AutoFix)

PipeSpec ships a conservative deterministic correction mechanism:

- Module: `src/pipespec_validator/corrections.py`
- API: `autofix_dict`, `autofix_multi_round`, `autofix_file`

Design constraints:
- AutoFix MUST NOT invent domain content.
- AutoFix SHOULD only apply structure-preserving transformations and safe stubs.
- AutoFix emits an auditable list of `FixAction` records.

AutoFix is iterative (bounded) and stops when:
- the schema error count stops decreasing, or
- no new fix actions are produced, or
- a maximum number of rounds is reached (`MAX_ROUNDS`)

Typical safe fixes include:
- converting `components` object maps into arrays and injecting missing `id` keys
- adding missing top-level containers (`parameters`, `integrations`, `flow_structure`)
- normalizing common category aliases to the normative taxonomy
- adding stub nodes when components exist but nodes are missing

Remaining schema errors after AutoFix typically indicate **content gaps** (e.g., missing category/executor_type)
that require either a human decision or LLM escalation based on the original description.

---

## 11. Hint Generation and Escalation Guidance

The hints engine derives deterministic, human-readable hints from:
- schema validation errors
- semantic warnings

Module:
- `src/pipespec_validator/hints.py`

Hints are classified into tiers:
- **structural**: can likely be addressed by AutoFix
- **content**: requires domain knowledge (often requires LLM repair or human authoring)
- **semantic**: topology/cross-reference guidance

The validator may recommend “LLM escalation” when high-severity content gaps remain after AutoFix.

---

## 12. Optional LLM Tooling (Non-normative tools/)

PipeSpec provides optional tool scripts (non-normative) for LLM-driven workflows:

- extraction from descriptions to PipeSpec (including parallel extraction strategy)
- LLM-based repair escalation for content gaps after deterministic AutoFix

These tools:
- require explicit user configuration (`--model`, `--base-url`, `--api-key`)
- SHOULD require an explicit consent flag for any tool that sends pipeline data to third-party APIs
- SHOULD be considered advisory; final validation remains schema-driven

---

## 13. Relationship to OPOS / Universal Abstraction Layer (UAL) (brief)

PipeSpec is an extraction artefact optimized for LLM generation and validation. It is not the final compilation target.

A deterministic compiler transforms PipeSpec into a normative Universal Abstraction Layer (UAL) document (OPOS),
which:
- canonicalizes enums and resource defaults
- resolves secret manifests symbolically
- normalizes executor models for platform compilation
- serves as a single stable source for downstream platform compilers

In the overall Prompt2Kube architecture:

- PipeSpec is authored by an LLM (plus optional deterministic corrections)
- OPOS is authored by deterministic compilation logic

This separation improves reproducibility, testability, and portability.

---

## 14. Limitations and Future Work

Planned improvements:
- strict semantic mode (`--semantic-strict`) for environments requiring topology enforcement
- expanded AutoFix rules for additional common extraction failure modes
- richer support for conditional and loop semantics beyond v1 graph checks
- additional export/interop layers for downstream abstractions (OPOS)

---

## Appendix A — Reference CLI usage

Validate (JSON or YAML input):
```bash
pipespec-validate pipeline.pipespec.json
pipespec-validate pipeline.pipespec.yaml
```

Validate + semantic warnings:
```bash
pipespec-validate pipeline.pipespec.json --semantic
```

Write a report:
```bash
pipespec-validate pipeline.pipespec.json --semantic --report report.json
pipespec-validate pipeline.pipespec.json --semantic --report report.yaml
pipespec-validate pipeline.pipespec.json --semantic --report report.md
```

Apply deterministic autofix:
```bash
pipespec-validate pipeline.pipespec.yaml --autofix --autofix-out pipeline.fixed.pipespec.yaml --report report.json
```