from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

from pipespec_validator import load_prompt_profile, validate_dict


LOGGER = logging.getLogger("pipespec.llm_extract")


def setup_logging(verbose: bool, debug: bool) -> None:
    level = logging.WARNING
    if verbose:
        level = logging.INFO
    if debug:
        level = logging.DEBUG

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def build_system_prompt() -> str:
    return (
        "You are a pipeline extraction engine.\n"
        "You MUST output a PipeSpec v1 JSON document.\n"
        "\n"
        "Hard rules:\n"
        "- Output MUST be a single valid JSON object.\n"
        "- Output MUST conform to PipeSpec v1 structure.\n"
        "- Do NOT output markdown, code fences, or extra commentary.\n"
        "- Do NOT invent unknown details; use null or empty arrays/objects.\n"
        "\n"
        "Critical structure rules:\n"
        "- components MUST be an array of component objects (NOT an object/map).\n"
        "- flow_structure.nodes MUST be an object/map keyed by node id (NOT an array).\n"
        "- integrations MUST contain keys: connections (array) and data_lineage (object).\n"
        "\n"
        "Allowed enums:\n"
        "- flow_patterns / pattern: sequential | parallel | dag | conditional | loop\n"
        "- executor_type: python | http | sql | bash | email | docker | custom\n"
        "- category: Extractor | Transformer | Loader | Reconciliator | QualityCheck | Notifier | Sensor | Custom\n"
        "- edge_type: success | failure | always | conditional\n"
        "- node.kind: Task | Group | Branch | Sensor | ParallelGroup\n"
        "- upstream_policy.type: all_success | none_failed | one_success | all_done\n"
    )


def build_user_prompt(description_text: str) -> str:
    # This skeleton forces the right *shapes* (array vs object map etc.)
    skeleton = {
        "pipespec_version": "1.0",
        "metadata": {
            "analysis_timestamp": "2026-01-01T00:00:00Z",
            "source_file": None,
            "llm_provider": None,
            "llm_model": None,
            "schema_version": "1.0"
        },
        "pipeline_summary": {
            "name": "",
            "description": "",
            "flow_patterns": ["sequential"],
            "task_executors": [],
            "complexity": "low"
        },
        "components": [
            {
                "id": "component_id",
                "name": "Human readable name",
                "category": "Extractor",
                "description": "",
                "executor_type": "python",
                "executor_config": None,
                "io_spec": [
                    {
                        "name": "io_name",
                        "direction": "input",
                        "kind": "api",
                        "format": "json",
                        "path_pattern": None,
                        "connection_id": None
                    }
                ],
                "upstream_policy": {"type": "none_failed", "description": "", "timeout_seconds": None},
                "retry_policy": {"max_attempts": 0, "delay_seconds": 0, "exponential_backoff": False, "retry_on": []},
                "concurrency": {
                    "supports_parallelism": False,
                    "supports_dynamic_mapping": False,
                    "map_over_param": None,
                    "max_parallel_instances": None
                },
                "connections": [],
                "datasets": {"consumes": [], "produces": []}
            }
        ],
        "flow_structure": {
            "pattern": "sequential",
            "entry_points": ["first_component_id"],
            "nodes": {
                "first_component_id": {
                    "kind": "Task",
                    "component_type_id": "first_component_id",
                    "upstream_policy": {"type": "all_success", "timeout_seconds": None},
                    "next_nodes": [],
                    "branch_config": None,
                    "sensor_config": None,
                    "parallel_config": None
                }
            },
            "edges": [
                {"from": "a", "to": "b", "edge_type": "success", "condition": None, "metadata": {}}
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
                    "id": "integration_id",
                    "name": "Integration name",
                    "type": "api",
                    "config": {},
                    "authentication": {},
                    "used_by_components": [],
                    "direction": "input",
                    "rate_limit": None,
                    "datasets": None
                }
            ],
            "data_lineage": {
                "sources": [],
                "sinks": [],
                "intermediate_datasets": []
            }
        }
    }

    return (
        "Extract a PipeSpec v1 JSON document from this pipeline description.\n"
        "Use the skeleton below as a SHAPE TEMPLATE.\n"
        "You must keep the same data types (arrays vs objects) and required keys.\n"
        "Replace placeholder values with correct values from the description.\n"
        "Return ONLY the JSON.\n\n"
        "PIPELINE DESCRIPTION:\n"
        f"{description_text}\n\n"
        "SKELETON SHAPE TEMPLATE:\n"
        f"{json.dumps(skeleton, indent=2)}\n"
    )


def call_openai_compatible_json_mode(
    *,
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    temperature: float,
) -> str:
    """
    Calls an OpenAI-compatible Chat Completions endpoint in JSON mode.
    """
    from openai import OpenAI  # optional dependency for this tool script

    client = OpenAI(api_key=api_key, base_url=base_url)

    LOGGER.info("Calling model=%s base_url=%s", model, base_url)

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=max_tokens,
        temperature=temperature,
    )
    msg = resp.choices[0].message
    return msg.content or ""


def _compact_error_feedback(errors, max_items: int = 30) -> str:
    lines = []
    for e in errors[:max_items]:
        path = e.instance_path or "(root)"
        lines.append(f"- {path}: {e.message}")
    if len(errors) > max_items:
        lines.append(f"... plus {len(errors) - max_items} more errors")
    return "\n".join(lines)


def extract_with_repair(
    *,
    description_text: str,
    base_url: str,
    api_key: str,
    model: str,
    max_tokens: int,
    temperature: float,
    attempts: int,
    semantic_checks: bool,
    print_model_output: bool,
    artifacts_dir: Path | None,
) -> dict[str, Any]:
    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(description_text)

    feedback = ""
    last_error_summary = None

    for i in range(1, attempts + 1):
        LOGGER.info("Attempt %d/%d", i, attempts)
        if feedback:
            LOGGER.info("Repair feedback included (%d chars).", len(feedback))

        content = call_openai_compatible_json_mode(
            base_url=base_url,
            api_key=api_key,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt + feedback,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        LOGGER.debug("Model output length: %d chars", len(content))
        LOGGER.debug("Model output preview: %r", content[:400])

        if print_model_output:
            LOGGER.info("Full model output:\n%s", content)

        if artifacts_dir is not None:
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            (artifacts_dir / f"attempt_{i:02d}_raw.txt").write_text(content, encoding="utf-8")

        # 1) Parse JSON
        try:
            doc = json.loads(content)
        except Exception as e:
            last_error_summary = f"JSON parse error: {e}"
            LOGGER.warning("%s", last_error_summary)

            feedback = (
                "\n\nYour previous output was not valid JSON.\n"
                f"JSON parse error: {e}\n"
                "Return ONLY a valid JSON object.\n"
            )
            continue

        if not isinstance(doc, dict):
            last_error_summary = f"Top-level JSON is not an object; got {type(doc).__name__}"
            LOGGER.warning("%s", last_error_summary)
            feedback = (
                "\n\nYour previous output was valid JSON, but not a JSON object.\n"
                "Return ONLY a single JSON object.\n"
            )
            continue

        if artifacts_dir is not None:
            (artifacts_dir / f"attempt_{i:02d}_parsed.json").write_text(
                json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

        # 2) Validate
        result = validate_dict(doc, semantic_checks=semantic_checks)
        if result.ok:
            LOGGER.info("Success: schema-valid PipeSpec produced on attempt %d.", i)
            if artifacts_dir is not None:
                (artifacts_dir / "final_valid.pipespec.json").write_text(
                    json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
            return doc

        err_feedback = _compact_error_feedback(result.errors, max_items=30)
        last_error_summary = f"{len(result.errors)} schema error(s). First few:\n{err_feedback}"
        LOGGER.warning("Schema validation failed: %s", last_error_summary)

        if artifacts_dir is not None:
            (artifacts_dir / f"attempt_{i:02d}_schema_errors.txt").write_text(
                err_feedback + "\n", encoding="utf-8"
            )

        feedback = (
            "\n\nThe previous JSON does not conform to the PipeSpec schema.\n"
            "Fix ONLY what is necessary to satisfy the schema.\n"
            "Preserve correct information.\n"
            "Return ONLY the corrected JSON object.\n\n"
            f"Schema errors:\n{err_feedback}\n"
        )

    raise RuntimeError(
        f"Failed to produce valid PipeSpec after {attempts} attempts. "
        f"Last error summary: {last_error_summary}"
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="inp", type=Path, required=True, help="Text file with pipeline description.")
    p.add_argument("--out", dest="out", type=Path, required=True, help="Output path for PipeSpec JSON.")

    p.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", "https://api.deepinfra.com/v1/openai"))
    p.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY"))
    p.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "Qwen/Qwen2.5-Coder-32B-Instruct"))

    p.add_argument("--max-tokens", type=int, default=4096)
    p.add_argument("--temperature", type=float, default=0.1)
    p.add_argument("--attempts", type=int, default=5, help="Number of repair attempts (schema validation + feedback).")

    p.add_argument("--semantic", action="store_true", help="Enable semantic checks (warnings only).")

    p.add_argument("--verbose", action="store_true", help="Enable INFO logging.")
    p.add_argument("--debug", action="store_true", help="Enable DEBUG logging (includes output previews).")
    p.add_argument(
        "--print-model-output",
        action="store_true",
        help="Print full model output to logs (can be large).",
    )
    p.add_argument(
        "--artifacts-dir",
        type=Path,
        default=None,
        help="If set, write per-attempt outputs and errors to this directory.",
    )

    args = p.parse_args()
    setup_logging(args.verbose, args.debug)

    if not args.api_key:
        raise SystemExit("Missing --api-key (or set OPENAI_API_KEY).")

    desc = args.inp.read_text(encoding="utf-8")

    doc = extract_with_repair(
        description_text=desc,
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        attempts=args.attempts,
        semantic_checks=args.semantic,
        print_model_output=args.print_model_output,
        artifacts_dir=args.artifacts_dir,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    LOGGER.info("Wrote PipeSpec: %s", args.out)
    print(f"Wrote PipeSpec: {args.out}")


if __name__ == "__main__":
    main()