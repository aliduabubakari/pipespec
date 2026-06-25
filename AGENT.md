# AGENT.md — PipeSpec Development Guide

> **Guidance for AI coding agents working on the PipeSpec project.**
>
> PipeSpec is a Python package + CLI for generating, validating, and correcting PipeSpec pipeline specification documents.
> This file covers architecture, conventions, workflows, and common tasks.

---

## 1. Project Identity

| Item | Value |
|------|-------|
| **Package name** | `pipespec-validator` |
| **Import name** | `pipespec_validator` |
| **CLI entry** | `pipespec` (unified), `pipespec-validate` / `pipespec-diff` (legacy) |
| **Python** | ≥3.10 |
| **Build system** | hatchling |
| **Schema draft** | JSON Schema Draft-07 |
| **License** | Apache-2.0 |

---

## 2. Repository Layout

```
pipespec/
├── schema/                          # Canonical schema + examples (normative)
│   ├── pipespec_schema_v1.json      #   THE normative schema (single source of truth)
│   ├── pipespec_schema.json         #   Convenience alias → latest stable
│   ├── pipespec_prompt_profile_v1.json  # Non-normative LLM prompt helper
│   ├── examples/                    #   Valid example PipeSpec documents
│   ├── fixtures/                    #   Test fixtures
│   └── semantic_fixtures/           #   Semantic-validation test fixtures
│
├── src/pipespec_validator/          # Python package (the validator + CLI)
│   ├── __init__.py                  #   Public API: validate_file, validate_dict, load_prompt_profile…
│   ├── validator.py                 #   Core: schema loading, JSON/YAML parsing, Draft7Validator wrapper
│   ├── cli.py / cli_root.py         #   CLI (Typer + Rich)
│   ├── semantic_rules.py            #   Semantic check registry (DAG, cross-refs, cycles)
│   ├── corrections.py               #   Deterministic AutoFix engine
│   ├── hints.py                     #   Hint generation from validation errors
│   ├── errors.py / models.py        #   Error types & data models (ValidationResult, etc.)
│   ├── reporting.py                 #   Structured report output (JSON/YAML/MD)
│   ├── io_utils.py                  #   File I/O helpers (load_doc, write_doc)
│   ├── resources.py                 #   Schema/prompt_profile resource loading
│   ├── generate.py                  #   LLM-based generation from natural language
│   ├── correct_llm.py               #   LLM-assisted correction
│   ├── llm_runtime.py               #   LLM provider abstraction (openai, claude, ollama, …)
│   ├── diffing.py                   #   Semantic diff between two PipeSpec documents
│   └── data/                        #   Bundled copies of schema + prompt_profile (shipped with package)
│       ├── pipespec_schema_v1.json
│       └── pipespec_prompt_profile_v1.json
│
├── tests/                           # Pytest test suite
├── tools/                           # Build/maintenance scripts
│   ├── sync_schema_into_package.py  #   Copy schema/ → src/…/data/
│   ├── check_schema_sync.py         #   Assert bundled schema matches canonical
│   ├── validate_examples.py         #   Validate all schema/examples/* against schema
│   ├── make_prompt_profile.py       #   Generate prompt profile from canonical schema
│   ├── normalize_schema.py          #   Schema normalization utilities
│   └── schema_to_markdown.py        #   Schema → Markdown doc generator
│
├── docs/                            # MkDocs documentation site
├── Pipeline_Description_Dataset/    # Sample NL pipeline descriptions
├── pyproject.toml                   # Project config, dependencies, tool settings
├── Makefile                         # Dev convenience targets
└── mkdocs.yml                       # MkDocs config
```

---

## 3. Key Architecture Concepts

### 3.1 Normative vs Non-Normative

- **`schema/pipespec_schema_v1.json`** is the single normative source of truth. A document is "valid PipeSpec v1" if and only if it validates against this schema.
- **`schema/pipespec_prompt_profile_v1.json`** is non-normative. It is a dereferenced, size-reduced copy of the schema used in LLM prompts. It MUST NOT be used for validation.
- **`src/pipespec_validator/data/`** contains bundled copies used at runtime. These are synced from `schema/` via `make sync-schema`.

### 3.2 Validation Layers

1. **Syntactic** — JSON parse → dict; YAML parse → dict
2. **Schema** — `jsonschema.Draft7Validator` against canonical schema. Returns `ok=True/False` + error list.
3. **Semantic** — `semantic_rules.py` runs structural/DAG checks. Emits **warnings** (does not change `ok` flag in v1). Rule IDs: `PIPESPEC-SEM-01` through `PIPESPEC-SEM-07`.

### 3.3 AutoFix Design

`corrections.py` provides conservative, deterministic fixes for structurally malformed documents:

- **SAFE**: Only fixes that can be inferred from the document's own structure. Never invents domain content.
- **ITERATIVE**: Runs fix rounds until no progress (error count stops decreasing or no new actions).
- **BOUNDED**: Hard cap of `MAX_ROUNDS = 5`.
- **AUDITABLE**: Every fix action is recorded as a `FixAction` dataclass.

Fix codes: `FIX-TOP-*` (top-level), `FIX-COMP-*` (components), `FIX-FLOW-*` (flow structure), `FIX-PARAM-*` (parameters), `FIX-INTEG-*` (integrations).

### 3.4 Public API Surface

From `src/pipespec_validator/__init__.py`:

```python
validate_file    # Validate a .pipespec.json/.yaml file
validate_dict    # Validate an in-memory dict
load_schema      # Load canonical schema (lru_cached)
load_prompt_profile  # Load prompt profile for LLM prompts
```

---

## 4. Development Setup

```bash
# Install dev dependencies (editable install)
make install
# Equivalent to: pip install -e ".[dev]"

# Run tests
make test
# Equivalent to: pytest

# Lint
make lint
# Equivalent to: ruff check .

# Format + auto-fix
make format
# Equivalent to: ruff format . && ruff check . --fix

# Sync canonical schema into package data dir
make sync-schema
# Also regenerates prompt profile
```

---

## 5. Code Conventions

### 5.1 Python Style

| Tool | Config |
|------|--------|
| **Formatter** | ruff format (line length 100) |
| **Linter** | ruff (rules: E, F, I, B, UP, N) |
| **Type checker** | mypy (mypy>=1.8.0) |

### 5.2 Module Conventions

- Use `from __future__ import annotations` in all modules.
- Use `from typing import Any` for generic dict handling (the schema/validation domain is inherently dynamic).
- Docstrings: concise module-level description, then function-level for public APIs.
- Keep `__init__.py` as the public API re-export surface. Implementation details go in sub-modules.

### 5.3 CLI Conventions

- Uses **Typer** (`cli_root.py` is the unified `pipespec` CLI; `cli.py` is the legacy `pipespec-validate` entry).
- Uses **Rich** for console output (tables, colors, JSON pretty-print).
- Exit codes: `0` = valid, `2` = invalid (parse/schema failure).

---

## 6. Testing

```bash
pytest                          # Full suite
pytest tests/test_validator_unit.py   # Unit tests for validator
pytest tests/test_semantic_dag_checks.py  # Semantic check tests
pytest tests/test_cli_e2e.py    # End-to-end CLI tests
```

Test fixtures live in:
- `schema/examples/` — valid PipeSpec documents used by `test_validate_examples.py`
- `schema/fixtures/` — test fixtures for validation
- `schema/semantic_fixtures/` — documents that trigger specific semantic warnings

### Test conventions

- Use `pytest` fixtures rather than setup/teardown.
- For schema validation tests, use the bundled schema (not the canonical path) to catch sync issues.
- Semantic rule tests should check for specific `rule_id` values in the warning output.

---

## 7. Schema Workflow

The schema lifecycle involves several files and sync steps. **Do not edit the bundled copies in `src/…/data/` directly.**

### When changing the schema

1. Edit `schema/pipespec_schema_v1.json` (the normative schema)
2. Run `make sync-schema` — this:
   - Regenerates `schema/pipespec_prompt_profile_v1.json` from the canonical schema
   - Copies both files into `src/pipespec_validator/data/`
3. Run `make check-schema-sync` to verify the sync is clean
4. Run `make test` to ensure nothing breaks
5. Run `make validate-examples` to verify examples still conform
6. If the schema change is a breaking change, bump the version

### Versioning

- Schema version string in the schema's `$id` and `SCHEMA_VERSION` constant must match.
- Top-level `pipespec_version` field in documents is `"1.0"`.
- Tool `tools/check_schema_id_and_version.py` verifies consistency.

---

## 8. Common Agent Tasks

### Add a new semantic rule

1. Add a function in `src/pipespec_validator/semantic_rules.py`:
   ```python
   def rule_my_new_check(doc: dict[str, Any]) -> list[ValidationErrorItem]:
       ...
   ```
2. Register it in `SEMANTIC_RULES` with a unique `rule_id` (format: `PIPESPEC-SEM-NN`).
3. Add a test fixture in `schema/semantic_fixtures/` that triggers the new rule.
4. Add a test in `tests/test_semantic_dag_checks.py`.

### Add a new AutoFix rule

1. Add the fix logic in `src/pipespec_validator/corrections.py`.
2. Use a new fix code following the `FIX-<CATEGORY>-NN` naming convention.
3. Ensure the fix is safe (no domain-content invention) and deterministic.
4. Add tests in `tests/test_autofix_yaml_json.py`.

### Add a new CLI command

1. Add the command function in a new or existing module under `src/pipespec_validator/`.
2. Register it in `cli_root.py` via `app.command("name")(function)`.
3. Add e2e tests in `tests/test_cli_e2e.py`.

### Change the component taxonomy or executor types

1. These are defined as `enum` constraints in the JSON Schema (`ComponentCategory`, `ExecutorType`).
2. Edit `schema/pipespec_schema_v1.json`.
3. Update `corrections.py` if any alias normalizations need changing (`FIX-COMP-03`).
4. Update `semantic_rules.py` if new categories affect semantic checks.
5. Run the full schema workflow (sync → test → validate-examples).

### Update dependencies

1. Edit `pyproject.toml` under `[project.dependencies]` or `[dependency-groups].dev`.
2. No lockfile; dependencies are resolved at install time.
3. LLM-related extras (`openai`, `anthropic`) are in `[project.optional-dependencies].llm`.

---

## 9. Design Constraints (DO NOT VIOLATE)

1. **Normative schema is king.** `schema/pipespec_schema_v1.json` is the only source of truth. All validation MUST use it.
2. **Never embed secrets.** PipeSpec documents MUST NOT contain secret values. The validator, AutoFix, and generation tools must uphold this.
3. **AutoFix is SAFE and conservative.** Do not add AutoFix rules that invent domain content. If content is missing, escalate via hints, not fix code.
4. **Semantic checks are warnings.** In v1, semantic violations do NOT change `ok=True/False`. Do not make them fail the validation result.
5. **Bundled schema must stay in sync.** Always run `make sync-schema` after schema changes. CI runs `make check-schema-sync` to catch drift.
6. **`additionalProperties: false`** on all schema objects — the schema is strict. Do not relax this without a schema version bump.
7. **Public API stability.** `__init__.py` exports are the public contract. Internal modules may change freely.

---

## 10. Files to Read Before Major Changes

| File | When to read |
|------|-------------|
| `technical.md` | Understanding the full design rationale and bounded scope |
| `docs/schema/overview.md` | Schema structure overview |
| `docs/schema/semantic-validation.md` | Semantic rule reference |
| `docs/schema/taxonomy.md` | Component category definitions |
| `README.md` | User-facing documentation and CLI usage |
| `skills.md` | How LLMs should generate PipeSpec documents (for generation tooling work) |
