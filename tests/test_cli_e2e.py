from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from pipespec_validator.cli_root import app
from pipespec_validator.validator import validate_file


def _load_example_doc() -> dict:
    example_path = Path("schema/examples/airvisual_pipeline.pipespec.json")
    return json.loads(example_path.read_text(encoding="utf-8"))


def test_generate_command_with_mocked_llm(monkeypatch, tmp_path):
    runner = CliRunner()
    inp = tmp_path / "description.txt"
    out = tmp_path / "generated.pipespec.json"
    inp.write_text("Fetch and transform daily data.", encoding="utf-8")

    expected_doc = _load_example_doc()

    def fake_call_llm_json(*, config, system_prompt, user_prompt, max_tokens, temperature):
        assert config.provider == "openai"
        assert config.api_key == "test-key"
        assert "PipeSpec v1 JSON" in system_prompt
        assert "PIPELINE DESCRIPTION" in user_prompt
        return json.dumps(expected_doc)

    monkeypatch.setenv("TEST_OPENAI_KEY", "test-key")
    monkeypatch.setattr("pipespec_validator.generate.call_llm_json", fake_call_llm_json)

    res = runner.invoke(
        app,
        [
            "generate",
            "--in",
            str(inp),
            "--out",
            str(out),
            "--provider",
            "openai",
            "--api-key-env",
            "TEST_OPENAI_KEY",
        ],
    )
    assert res.exit_code == 0, res.output
    assert out.exists()
    result = validate_file(out, semantic_checks=True)
    assert result.ok


def test_correct_command_with_mocked_llm(monkeypatch, tmp_path):
    runner = CliRunner()
    source = _load_example_doc()
    invalid_doc = json.loads(json.dumps(source))
    invalid_doc["components"][0].pop("category", None)
    invalid_doc["components"][0].pop("executor_type", None)

    inp = tmp_path / "invalid.pipespec.json"
    inp.write_text(json.dumps(invalid_doc, indent=2), encoding="utf-8")
    out = tmp_path / "repaired.pipespec.json"
    desc = tmp_path / "description.txt"
    desc.write_text("Simple ETL pipeline.", encoding="utf-8")

    def fake_call_llm_json(*, config, system_prompt, user_prompt, max_tokens, temperature):
        assert config.provider == "anthropic"
        assert config.api_key == "anthropic-test-key"
        assert "repair engine" in system_prompt
        assert "SCHEMA ERRORS TO FIX" in user_prompt
        return json.dumps(source)

    monkeypatch.setenv("TEST_ANTHROPIC_KEY", "anthropic-test-key")
    monkeypatch.setattr("pipespec_validator.correct_llm.call_llm_json", fake_call_llm_json)

    res = runner.invoke(
        app,
        [
            "correct",
            "--in",
            str(inp),
            "--out",
            str(out),
            "--description",
            str(desc),
            "--provider",
            "claude",
            "--api-key-env",
            "TEST_ANTHROPIC_KEY",
        ],
    )
    assert res.exit_code == 0, res.output
    assert out.exists()
    result = validate_file(out, semantic_checks=True)
    assert result.ok


def test_providers_command_shows_status(monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    res = runner.invoke(app, ["providers", "--provider", "openai"])
    assert res.exit_code == 0, res.output
    assert "openai" in res.output
    assert "env:OPENAI_API_KEY" in res.output


def test_providers_command_json_output(monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    res = runner.invoke(app, ["providers", "--provider", "openai", "--json"])
    assert res.exit_code == 0, res.output

    data = json.loads(res.output)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["provider"] == "openai"
    assert data[0]["api_key_source"] == "env:OPENAI_API_KEY"
