from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from pipespec_validator.cli_root import app
from pipespec_validator.diffing import semantic_diff


def _example_doc() -> dict:
    path = Path("schema/examples/airvisual_pipeline.pipespec.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_semantic_diff_detects_component_change():
    left = _example_doc()
    right = json.loads(json.dumps(left))
    right["components"][0]["category"] = "Transformer"

    report = semantic_diff(left, right, left_path="left.json", right_path="right.json")
    assert report.has_changes is True
    assert report.sections["components"]["changed"]


def test_diff_command_json_no_changes(tmp_path):
    runner = CliRunner()
    doc = _example_doc()
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    left.write_text(json.dumps(doc), encoding="utf-8")
    right.write_text(json.dumps(doc), encoding="utf-8")

    res = runner.invoke(
        app,
        [
            "diff",
            "--left",
            str(left),
            "--right",
            str(right),
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["has_changes"] is False


def test_diff_command_json_has_changes(tmp_path):
    runner = CliRunner()
    left_doc = _example_doc()
    right_doc = json.loads(json.dumps(left_doc))
    right_doc["components"][0]["executor_type"] = "bash"

    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    left.write_text(json.dumps(left_doc), encoding="utf-8")
    right.write_text(json.dumps(right_doc), encoding="utf-8")

    res = runner.invoke(
        app,
        [
            "diff",
            "--left",
            str(left),
            "--right",
            str(right),
            "--json",
        ],
    )
    assert res.exit_code == 1, res.output
    payload = json.loads(res.output)
    assert payload["has_changes"] is True
    assert payload["sections"]["components"]["changed"]
