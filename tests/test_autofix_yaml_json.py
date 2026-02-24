from __future__ import annotations

from pathlib import Path

from pipespec_validator.corrections import autofix_dict


def test_autofix_components_dict_to_list():
    doc = {
        "pipespec_version": "1.0",
        "metadata": {"analysis_timestamp": "2026-02-23T00:00:00Z"},
        "pipeline_summary": {"name": "x", "description": "x", "flow_patterns": ["sequential"], "task_executors": [], "complexity": "low"},
        "components": {"task_a": {"name": "A", "category": "Extractor", "executor_type": "python", "io_spec": []}},
        "flow_structure": {"pattern": "sequential", "entry_points": [], "nodes": {}, "edges": []},
        "parameters": {"pipeline": {}, "schedule": {}, "execution": {}, "components": {}, "environment": {}},
        "integrations": {"connections": [], "data_lineage": {"sources": [], "sinks": [], "intermediate_datasets": []}},
    }

    fixed, actions = autofix_dict(doc)
    assert isinstance(fixed["components"], list)
    assert fixed["components"][0]["id"] == "task_a"
    assert any(a.code == "FIX-COMP-01" for a in actions)