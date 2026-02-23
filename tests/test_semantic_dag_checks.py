from __future__ import annotations

from pipespec_validator import validate_dict


def _minimal_valid_doc():
    return {
        "pipespec_version": "1.0",
        "metadata": {"analysis_timestamp": "2026-02-23T00:00:00Z"},
        "pipeline_summary": {
            "name": "t",
            "description": "t",
            "flow_patterns": ["dag"],
            "task_executors": ["python"],
            "complexity": "low",
        },
        "components": [
            {
                "id": "a",
                "name": "A",
                "category": "Extractor",
                "description": "",
                "executor_type": "python",
                "executor_config": None,
                "io_spec": [
                    {
                        "name": "o",
                        "direction": "output",
                        "kind": "object",
                        "format": "json",
                        "path_pattern": None,
                        "connection_id": None,
                    }
                ],
            },
            {
                "id": "b",
                "name": "B",
                "category": "Transformer",
                "description": "",
                "executor_type": "python",
                "executor_config": None,
                "io_spec": [
                    {
                        "name": "o2",
                        "direction": "output",
                        "kind": "object",
                        "format": "json",
                        "path_pattern": None,
                        "connection_id": None,
                    }
                ],
            },
        ],
        "flow_structure": {
            "pattern": "dag",
            "entry_points": ["a"],
            "nodes": {
                "a": {
                    "kind": "Task",
                    "component_type_id": "a",
                    "upstream_policy": {"type": "all_success", "timeout_seconds": None},
                    "next_nodes": ["b"],
                    "branch_config": None,
                    "sensor_config": None,
                    "parallel_config": None,
                },
                "b": {
                    "kind": "Task",
                    "component_type_id": "b",
                    "upstream_policy": {"type": "all_success", "timeout_seconds": None},
                    "next_nodes": [],
                    "branch_config": None,
                    "sensor_config": None,
                    "parallel_config": None,
                },
            },
            "edges": [
                {"from": "a", "to": "b", "edge_type": "success", "condition": None, "metadata": {}},
            ],
        },
        "parameters": {
            "pipeline": {},
            "schedule": {},
            "execution": {},
            "components": {},
            "environment": {},
        },
        "integrations": {
            "connections": [],
            "data_lineage": {"sources": [], "sinks": [], "intermediate_datasets": []},
        },
    }


def test_cycle_warns_when_not_loop():
    doc = _minimal_valid_doc()
    # create cycle a->b and b->a
    doc["flow_structure"]["edges"].append({"from": "b", "to": "a", "edge_type": "success", "condition": None, "metadata": {}})
    res = validate_dict(doc, semantic_checks=True)
    assert res.ok  # schema ok
    msgs = [w.message for w in res.warnings]
    assert any("Cycle detected" in m for m in msgs)


def test_cycle_not_warn_when_loop_pattern():
    doc = _minimal_valid_doc()
    doc["flow_structure"]["pattern"] = "loop"
    doc["flow_structure"]["edges"].append({"from": "b", "to": "a", "edge_type": "success", "condition": None, "metadata": {}})
    res = validate_dict(doc, semantic_checks=True)
    msgs = [w.message for w in res.warnings]
    assert not any("Cycle detected" in m for m in msgs)


def test_unreachable_node_warns():
    doc = _minimal_valid_doc()
    # add a third node 'c' but no edges from entry point to it
    doc["components"].append(
        {
            "id": "c",
            "name": "C",
            "category": "Loader",
            "description": "",
            "executor_type": "python",
            "executor_config": None,
            "io_spec": [
                {
                    "name": "o3",
                    "direction": "output",
                    "kind": "object",
                    "format": "json",
                    "path_pattern": None,
                    "connection_id": None,
                }
            ],
        }
    )
    doc["flow_structure"]["nodes"]["c"] = {
        "kind": "Task",
        "component_type_id": "c",
        "upstream_policy": {"type": "all_success", "timeout_seconds": None},
        "next_nodes": [],
        "branch_config": None,
        "sensor_config": None,
        "parallel_config": None,
    }
    res = validate_dict(doc, semantic_checks=True)
    msgs = [w.message for w in res.warnings]
    assert any("Unreachable node" in m or "Unreachable node(s)" in m for m in msgs)