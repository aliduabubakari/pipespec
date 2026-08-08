# PipeSpec v1 — LLM Reference Manual

> **A detailed schema and authoring reference for PipeSpec v1 pipeline documents.**
>
> PipeSpec is the source-facing layer of OPOS. It records pipeline intent, design structure, integrations, parameters, assumptions, and unresolved information in a platform-independent JSON document.
> Use this manual with the concise `pipespec-authoring` skill. The skill defines the governed authoring procedure; this manual supplies the detailed field-level guidance.

An authoring agent produces a **candidate** PipeSpec. Schema and semantic validators report document quality. A human or explicitly configured policy gate decides whether that candidate becomes the accepted input to deterministic OrchSpec compilation. The authoring agent does not grant acceptance, generate the canonical OrchSpec, or render target workflow code.

---

## 1. What PipeSpec Is

PipeSpec captures the **source-facing design** of a data pipeline:

- **What** each component does (category, executor type, I/O)
- **How** components connect (DAG topology: nodes + edges)
- **What** external systems they touch (integrations catalogue)
- **What** parameters they need (typed parameters + environment references)

PipeSpec does **NOT** embed full business logic payloads (SQL bodies, Python scripts, etc.) — it captures the component boundary, not the implementation.

---

## 2. Top-Level Document Structure

A valid PipeSpec v1 document is a single JSON object with **7 required keys**:

| Key | Type | Description |
|-----|------|-------------|
| `pipespec_version` | `string` | Must be `"1.0"` |
| `metadata` | `object` | Provenance & extraction metadata |
| `pipeline_summary` | `object` | Human-readable pipeline overview |
| `components` | `array` | List of pipeline components (min 1 item) |
| `flow_structure` | `object` | Directed graph topology (nodes + edges) |
| `parameters` | `object` | Typed pipeline parameters (5 groups) |
| `integrations` | `object` | External system catalogue + coarse lineage |

Optional extra key:

| Key | Type | Description |
|-----|------|-------------|
| `extensions` | `object` | Non-normative extension point; any properties allowed |

---

## 3. Identifier Convention

All IDs must match: `^[A-Za-z][A-Za-z0-9_\-]*$` (1–128 chars, starts with a letter).

Examples: `extract_api`, `validate_json`, `load_to_postgres`, `airvisual_api`, `local_filesystem`

---

## 4. `metadata` — Provenance & Extraction Info

```jsonc
{
  "analysis_timestamp": "2025-11-28T12:31:09Z",   // REQUIRED. ISO 8601 date-time string
  "source_file": "path/to/description.txt",         // optional
  "llm_provider": "openai",                          // optional
  "llm_model": "gpt-4o",                             // optional
  "analysis_results": {                              // optional
    "detected_patterns": ["sequential"],             // FlowPattern[]
    "task_executors_used": ["python", "http"],       // ExecutorType[]
    "has_branching": false,
    "has_parallelism": false,
    "has_sensors": false,
    "total_components": 3,
    "complexity_score": "low"                        // "low" | "medium" | "high"
  },
  "validation_warnings": [],                         // optional
  "extensions": {}                                    // optional
}
```

Only `analysis_timestamp` is required. Always include it with the current time in ISO 8601.

---

## 5. `pipeline_summary` — Human Overview

```jsonc
{
  "name": "My Pipeline",                  // REQUIRED. string
  "description": "What the pipeline does", // optional string
  "flow_patterns": ["sequential"],         // REQUIRED. FlowPattern[], min 1
  "task_executors": ["python", "sql"],     // optional ExecutorType[]
  "complexity": "medium"                   // REQUIRED. "low" | "medium" | "high"
}
```

### Flow Patterns (enum)

| Value | Meaning |
|-------|---------|
| `sequential` | Tasks run one after another |
| `parallel` | Tasks run concurrently |
| `dag` | General directed acyclic graph |
| `conditional` | Branching with conditions |
| `loop` | Intentional cycles allowed |

### Complexity (enum)

| Value | When to use |
|-------|------------|
| `low` | ≤3 components, sequential, no branching |
| `medium` | 4–10 components, some branching/parallelism |
| `high` | >10 components, complex DAG, many integrations |

---

## 6. `components[]` — The Task Catalogue

Each component describes one unit of work:

```jsonc
{
  "id": "extract_api",                          // REQUIRED. Id (pattern above)
  "name": "Extract API Data",                    // REQUIRED. string
  "category": "Extractor",                       // REQUIRED. ComponentCategory enum
  "description": "Fetches data from REST API",   // optional string
  "executor_type": "python",                     // REQUIRED. ExecutorType enum
  "executor_config": null,                       // optional, can be null

  // OPTIONAL human-readable I/O lists (informal):
  "inputs": ["raw_data.csv"],
  "outputs": ["processed_data.csv"],

  // REQUIRED structured I/O (min 1 item):
  "io_spec": [
    {
      "name": "api_response",                   // REQUIRED. Id
      "direction": "input",                     // REQUIRED. "input" | "output"
      "kind": "api",                            // REQUIRED. "file" | "table" | "api" | "object" | "stream"
      "format": "json",                         // REQUIRED. free-form string (e.g. json, csv, parquet, sql)
      "path_pattern": "https://api.example.com/v1/data", // string or null
      "connection_id": "some_api_connection"    // string (matching integration id) or null
    }
  ],

  "upstream_policy": {
    "type": "all_success",                      // REQUIRED. "all_success" | "none_failed" | "one_success" | "all_done"
    "description": "Runs after upstream ok",     // optional
    "timeout_seconds": null                      // optional integer ≥ 0 or null
  },
  "retry_policy": {
    "max_attempts": 3,                          // REQUIRED. integer ≥ 0
    "delay_seconds": 60,                         // REQUIRED. integer ≥ 0
    "exponential_backoff": true,                 // REQUIRED. boolean
    "retry_on": ["timeout", "network_error"]     // REQUIRED. string[]
  },
  "concurrency": {
    "supports_parallelism": false,               // REQUIRED. boolean
    "supports_dynamic_mapping": false,           // REQUIRED. boolean
    "map_over_param": null,                      // string or null
    "max_parallel_instances": null               // integer ≥ 1 or null
  },
  "connections": [                               // optional
    { "id": "some_api_connection", "type": "api", "purpose": "Fetch data" }
  ],
  "datasets": {                                  // optional, but include both arrays
    "consumes": ["raw_dataset"],                 // REQUIRED if present. string[]
    "produces": ["processed_dataset"]            // REQUIRED if present. string[]
  },
  "extensions": {}                                // optional
}
```

### Component Categories (enum — 8 values)

| Category | When to use |
|----------|------------|
| `Extractor` | Fetches/reads data from an external source (API call, file read, DB query for extraction) |
| `Transformer` | Transforms, enriches, or reshapes data (ETL mapping, cleaning, aggregation) |
| `Loader` | Writes/loads data into a target system (DB insert, file write, data warehouse load) |
| `Reconciliator` | Compares or reconciles datasets (source vs target row counts, schema conformance) |
| `QualityCheck` | Validates data quality (null checks, schema validation, anomaly detection) |
| `Notifier` | Sends notifications (email, Slack, webhook) — no data transformation |
| `Sensor` | Waits for an external condition or event (file arrival, API polling, time trigger) |
| `Custom` | Anything that doesn't map cleanly to the above |

### Executor Types (enum — 7 values)

| Type | When to use |
|------|------------|
| `python` | Python script or function |
| `http` | HTTP request/callout |
| `sql` | SQL execution (query, stored procedure) |
| `bash` | Shell script or command |
| `email` | Email sending |
| `docker` | Container-based execution |
| `custom` | Anything else |

### I/O Spec — `io_spec[]`

Each I/O item requires: `name`, `direction`, `kind`, `format`.

- **`kind`** enum: `file` | `table` | `api` | `object` | `stream`
- **`direction`** enum: `input` | `output`
- **`format`**: free-form (e.g., `json`, `csv`, `parquet`, `sql`, `protobuf`)
- **`path_pattern`**: path/URI/pattern, or `null` for in-memory objects
- **`connection_id`**: must reference an `integrations.connections[].id` if not null

---

## 7. `flow_structure` — Pipeline Topology

```jsonc
{
  "pattern": "sequential",                   // REQUIRED. FlowPattern enum
  "entry_points": ["first_task_id"],          // REQUIRED. Id[], min 1
  "nodes": {                                  // REQUIRED. map of node_id → FlowNode
    "first_task_id": {
      "kind": "Task",                         // REQUIRED. "Task" | "Group" | "Branch" | "Sensor" | "ParallelGroup"
      "component_type_id": "first_task_id",   // REQUIRED. must match a components[].id
      "upstream_policy": {
        "type": "all_success",                // REQUIRED
        "timeout_seconds": null
      },
      "next_nodes": ["second_task_id"],       // REQUIRED. Id[] (can be empty)
      "branch_config": null,
      "sensor_config": null,
      "parallel_config": null
    }
  },
  "edges": [                                  // REQUIRED. FlowEdge[]
    {
      "from": "first_task_id",                // REQUIRED. Id
      "to": "second_task_id",                 // REQUIRED. Id
      "edge_type": "success",                 // REQUIRED. "success" | "failure" | "always" | "conditional"
      "condition": null,                      // string or null (used when edge_type is "conditional")
      "metadata": {}
    }
  ]
}
```

### Critical Rules for flow_structure

1. **Every component MUST have a matching node.** Each `components[].id` needs a corresponding key in `flow_structure.nodes` with `component_type_id` set to that same id.
2. **Node keys = component IDs.** The key of each node entry should match the component id.
3. **entry_points** are nodes with no incoming edges that start the pipeline.
4. **next_nodes** lists the node IDs that follow this node (must be a subset of what edges say).
5. **Edges must reference valid node IDs** in both `from` and `to`.
6. **DAG constraint:** If `pattern` is NOT `loop`, the graph must be acyclic.
7. **Most nodes use `kind: "Task"`.** Use other kinds only when appropriate:
   - `Group` — a logical grouping of sub-tasks
   - `Branch` — conditional branching node
   - `Sensor` — external event sensor
   - `ParallelGroup` — parallel execution group

### Edge Types

| Type | Meaning |
|------|---------|
| `success` | Follow this edge when upstream succeeds |
| `failure` | Follow this edge when upstream fails |
| `always` | Always follow regardless of outcome |
| `conditional` | Follow based on a `condition` expression string |

---

## 8. `parameters` — Typed Parameters (5 Groups)

```jsonc
{
  "pipeline": {},          // ParameterGroup — global pipeline parameters
  "schedule": {            // ParameterGroup — schedule/runtime parameters
    "enabled": {
      "description": "Whether the pipeline runs on schedule",
      "type": "boolean",
      "default": true,
      "required": false,
      "constraints": null,
      "format": null
    }
  },
  "execution": {},         // ParameterGroup — execution-level parameters
  "components": {          // Map of component_id → ParameterGroup
    "extract_api": {
      "batch_size": {
        "description": "Number of records per batch",
        "type": "integer",
        "default": 1000,
        "required": false,
        "constraints": "Must be > 0",
        "format": null
      }
    }
  },
  "environment": {         // Map of env_var_name → EnvironmentParameterSpec
    "API_KEY": {
      "description": "API authentication key",
      "type": "string",
      "default": null,               // ⚠️ Secrets MUST have null default
      "required": true,              // ⚠️ Secrets should be required
      "constraints": null,
      "format": null,
      "associated_component_id": "extract_api"   // optional; which component uses this
    }
  }
}
```

### Parameter Types (enum)

`string` | `integer` | `float` | `boolean` | `array` | `object` | `datetime`

### ⚠️ SECRET HYGIENE — CRITICAL

- **NEVER embed actual secret values** in a PipeSpec document.
- Secrets MUST be represented as environment parameters with:
  - `"default": null`
  - `"required": true`
- Only reference the environment variable NAME (e.g., `API_KEY`, `DB_PASSWORD`), never the value.

---

## 9. `integrations` — External System Catalogue

```jsonc
{
  "connections": [                           // REQUIRED. IntegrationConnection[]
    {
      "id": "postgres_warehouse",            // REQUIRED. Id
      "name": "PostgreSQL Warehouse",         // REQUIRED. string
      "type": "database",                     // REQUIRED. "api" | "database" | "filesystem" | "object_store" | "message_queue" | "smtp" | "other"
      "config": {                             // REQUIRED. free-form object
        "protocol": "postgresql",
        "host": "db.example.com"
      },
      "authentication": {                     // REQUIRED. free-form object (NO SECRET VALUES)
        "type": "basic",
        "connection_env_var": "POSTGRES_CONN"
      },
      "used_by_components": ["load_to_db"],   // REQUIRED. Id[]
      "direction": "output"                   // REQUIRED. "input" | "output" | "both"
    }
  ],
  "data_lineage": {                          // REQUIRED
    "sources": ["External API", "SFTP Server"],       // REQUIRED. string[]
    "sinks": ["PostgreSQL Warehouse"],                 // REQUIRED. string[]
    "intermediate_datasets": ["/tmp/staging.json"]    // REQUIRED. string[]
  }
}
```

### Integration Connection Types

| Type | Typical use |
|------|------------|
| `api` | REST/GraphQL API endpoints |
| `database` | Relational databases (PostgreSQL, MySQL, etc.) |
| `filesystem` | Local/network filesystem paths |
| `object_store` | S3, GCS, Azure Blob |
| `message_queue` | Kafka, RabbitMQ, SQS |
| `smtp` | Email (SMTP) |
| `other` | Anything else |

### Integration Direction

| Value | Meaning |
|-------|---------|
| `input` | Pipeline only reads from this system |
| `output` | Pipeline only writes to this system |
| `both` | Pipeline reads and writes |

---

## 10. Complete Minimal Example

Below is the smallest valid PipeSpec v1 document. Use it as a template:

```json
{
  "pipespec_version": "1.0",
  "metadata": {
    "analysis_timestamp": "2026-05-29T00:00:00Z",
    "source_file": "description.txt",
    "llm_provider": "openai",
    "llm_model": "gpt-4o"
  },
  "pipeline_summary": {
    "name": "Simple ETL Pipeline",
    "description": "Extract CSV, transform, load to PostgreSQL.",
    "flow_patterns": ["sequential"],
    "task_executors": ["python", "sql"],
    "complexity": "low"
  },
  "components": [
    {
      "id": "extract_csv",
      "name": "Extract CSV Data",
      "category": "Extractor",
      "description": "Reads CSV from filesystem.",
      "executor_type": "python",
      "executor_config": null,
      "io_spec": [
        {
          "name": "source_csv",
          "direction": "input",
          "kind": "file",
          "format": "csv",
          "path_pattern": "/data/input.csv",
          "connection_id": "local_fs"
        },
        {
          "name": "raw_data",
          "direction": "output",
          "kind": "object",
          "format": "dataframe",
          "path_pattern": null,
          "connection_id": null
        }
      ],
      "upstream_policy": { "type": "none_failed", "description": "First task", "timeout_seconds": null },
      "retry_policy": { "max_attempts": 1, "delay_seconds": 0, "exponential_backoff": false, "retry_on": [] },
      "concurrency": { "supports_parallelism": false, "supports_dynamic_mapping": false, "map_over_param": null, "max_parallel_instances": null },
      "connections": [{ "id": "local_fs", "type": "filesystem", "purpose": "Read input CSV" }],
      "datasets": { "consumes": [], "produces": ["raw_data"] }
    },
    {
      "id": "transform_data",
      "name": "Transform Data",
      "category": "Transformer",
      "description": "Cleans and enriches the data.",
      "executor_type": "python",
      "executor_config": null,
      "io_spec": [
        {
          "name": "raw_data",
          "direction": "input",
          "kind": "object",
          "format": "dataframe",
          "path_pattern": null,
          "connection_id": null
        },
        {
          "name": "clean_data",
          "direction": "output",
          "kind": "object",
          "format": "dataframe",
          "path_pattern": null,
          "connection_id": null
        }
      ],
      "upstream_policy": { "type": "all_success", "description": "After extract", "timeout_seconds": null },
      "retry_policy": { "max_attempts": 1, "delay_seconds": 0, "exponential_backoff": false, "retry_on": [] },
      "concurrency": { "supports_parallelism": false, "supports_dynamic_mapping": false, "map_over_param": null, "max_parallel_instances": null },
      "connections": [],
      "datasets": { "consumes": ["raw_data"], "produces": ["clean_data"] }
    },
    {
      "id": "load_to_db",
      "name": "Load to PostgreSQL",
      "category": "Loader",
      "description": "Writes cleaned data to PostgreSQL.",
      "executor_type": "sql",
      "executor_config": null,
      "io_spec": [
        {
          "name": "clean_data",
          "direction": "input",
          "kind": "object",
          "format": "dataframe",
          "path_pattern": null,
          "connection_id": null
        },
        {
          "name": "target_table",
          "direction": "output",
          "kind": "table",
          "format": "sql",
          "path_pattern": "public.clean_data_table",
          "connection_id": "pg_warehouse"
        }
      ],
      "upstream_policy": { "type": "all_success", "description": "After transform", "timeout_seconds": null },
      "retry_policy": { "max_attempts": 3, "delay_seconds": 60, "exponential_backoff": true, "retry_on": ["database_error"] },
      "concurrency": { "supports_parallelism": false, "supports_dynamic_mapping": false, "map_over_param": null, "max_parallel_instances": null },
      "connections": [{ "id": "pg_warehouse", "type": "database", "purpose": "Write to warehouse" }],
      "datasets": { "consumes": ["clean_data"], "produces": ["warehouse_table"] }
    }
  ],
  "flow_structure": {
    "pattern": "sequential",
    "entry_points": ["extract_csv"],
    "nodes": {
      "extract_csv": {
        "kind": "Task",
        "component_type_id": "extract_csv",
        "upstream_policy": { "type": "all_success", "timeout_seconds": null },
        "next_nodes": ["transform_data"],
        "branch_config": null, "sensor_config": null, "parallel_config": null
      },
      "transform_data": {
        "kind": "Task",
        "component_type_id": "transform_data",
        "upstream_policy": { "type": "all_success", "timeout_seconds": null },
        "next_nodes": ["load_to_db"],
        "branch_config": null, "sensor_config": null, "parallel_config": null
      },
      "load_to_db": {
        "kind": "Task",
        "component_type_id": "load_to_db",
        "upstream_policy": { "type": "all_success", "timeout_seconds": null },
        "next_nodes": [],
        "branch_config": null, "sensor_config": null, "parallel_config": null
      }
    },
    "edges": [
      { "from": "extract_csv", "to": "transform_data", "edge_type": "success", "condition": null, "metadata": {} },
      { "from": "transform_data", "to": "load_to_db", "edge_type": "success", "condition": null, "metadata": {} }
    ]
  },
  "parameters": {
    "pipeline": {},
    "schedule": {},
    "execution": {},
    "components": {},
    "environment": {}
  },
  "integrations": {
    "connections": [
      {
        "id": "local_fs",
        "name": "Local Filesystem",
        "type": "filesystem",
        "config": { "base_path": "/data" },
        "authentication": { "type": "none" },
        "used_by_components": ["extract_csv"],
        "direction": "input"
      },
      {
        "id": "pg_warehouse",
        "name": "PostgreSQL Warehouse",
        "type": "database",
        "config": { "protocol": "postgresql" },
        "authentication": { "type": "basic", "connection_env_var": "PG_CONN" },
        "used_by_components": ["load_to_db"],
        "direction": "output"
      }
    ],
    "data_lineage": {
      "sources": ["/data/input.csv"],
      "sinks": ["PostgreSQL Warehouse"],
      "intermediate_datasets": []
    }
  }
}
```

---

## 11. Generation Checklist

When generating a PipeSpec document from a natural-language pipeline description, verify:

- [ ] `pipespec_version` is `"1.0"`
- [ ] `metadata.analysis_timestamp` is a valid ISO 8601 string
- [ ] `pipeline_summary` has `name`, `flow_patterns` (array), and `complexity`
- [ ] `components` is an array with ≥1 items, each having: `id`, `name`, `category`, `executor_type`, `io_spec` (≥1 item)
- [ ] Every I/O spec item has `name`, `direction`, `kind`, `format`
- [ ] Component IDs match the pattern `^[A-Za-z][A-Za-z0-9_\-]*$`
- [ ] Categories and executor types use exact enum values (case-sensitive: `"Extractor"` not `"extractor"`)
- [ ] `flow_structure.nodes` has a matching entry for every component (key = component id, `component_type_id` = same id)
- [ ] `flow_structure.edges` uses valid node IDs for `from` and `to`
- [ ] `flow_structure.entry_points` references valid node IDs
- [ ] `parameters` has all 5 required groups (`pipeline`, `schedule`, `execution`, `components`, `environment`) — can be empty objects
- [ ] Secrets are NEVER embedded; use `"default": null, "required": true` in `parameters.environment`
- [ ] `integrations.connections` lists every external system referenced by components
- [ ] `integrations.data_lineage` has `sources`, `sinks`, `intermediate_datasets` (all arrays, can be empty)
- [ ] Every `connection_id` reference in `io_spec[]` resolves to an `integrations.connections[].id`
- [ ] No additional properties beyond those defined — `additionalProperties: false` is enforced

---

## 12. Common Mistakes to Avoid

| Mistake | Fix |
|---------|-----|
| Using lowercase category (e.g., `"extractor"`) | Use PascalCase: `"Extractor"` |
| Forgetting `io_spec[]` on a component | Every component must have at least 1 I/O spec item |
| Embedding secret values (passwords, tokens) | Use `parameters.environment` with `"default": null` and `"required": true` |
| Missing node for a component in `flow_structure.nodes` | Every `components[].id` must have a matching node entry |
| `component_type_id` in node doesn't match component `id` | Must be exact match |
| Edge `from`/`to` references non-existent node IDs | All edge endpoints must exist in `flow_structure.nodes` |
| Missing `data_lineage` in `integrations` | Always include with `sources`, `sinks`, `intermediate_datasets` arrays |
| Forgetting `parameters.components` and `parameters.environment` keys | Include all 5 parameter groups even if empty (`{}`) |
| Using `$schema` or `$id` in the document | PipeSpec documents do NOT include JSON Schema meta-properties |
| Extra fields not in the schema | `additionalProperties: false` — only use fields defined here |

---

## 13. Validation & Iterative Repair

If validation after generation reveals errors, follow this repair loop:

1. **Read the validation errors** — each error includes a JSON path and message
2. **Fix structural issues first** (missing required fields, enum typos, array/object type confusion)
3. **Fix cross-references** (node ↔ component, io_spec `connection_id` ↔ integrations)
4. **Check DAG integrity** (cycles when pattern ≠ `loop`, unreachable nodes)
5. **Re-validate** after each fix

For tooling: use `pipespec-validate` CLI or the Python API (`validate_dict`) to check conformance.
