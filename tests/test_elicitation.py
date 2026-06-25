from __future__ import annotations

import json

from typer.testing import CliRunner

from pipespec_validator import validate_file
from pipespec_validator.cli_root import app
from pipespec_validator.elicitation import build_coverage_matrix, plan_questions, profile_data_paths


def test_profile_csv_flags_sensitive_columns(tmp_path):
    sample = tmp_path / "customers.csv"
    sample.write_text(
        "customer_id,email,signup_date,amount\n1,a@example.com,2026-01-01,10.5\n2,,2026-01-02,20\n",
        encoding="utf-8",
    )

    [profile] = profile_data_paths([sample])

    assert profile.format == "csv"
    assert profile.row_count == 2
    columns = {column.name: column for column in profile.columns}
    assert columns["customer_id"].possible_key
    assert columns["email"].possible_sensitive
    assert columns["signup_date"].inferred_type == "datetime"


def test_coverage_question_planner_prioritizes_user_control(tmp_path):
    sample = tmp_path / "customers.csv"
    sample.write_text("customer_id,email\n1,a@example.com\n", encoding="utf-8")
    profiles = profile_data_paths([sample])
    coverage = build_coverage_matrix(
        description_text="Load customer CSV data into the analytics warehouse daily.",
        data_profiles=profiles,
    )

    questions = plan_questions(coverage, max_questions=5)
    question_slots = {question.slot for question in questions}

    assert "approval" in question_slots
    assert "pii_sensitive_data" in question_slots
    assert "write_mode" in question_slots


def test_elicit_command_writes_valid_draft_without_llm(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("PIPESPEC_LLM_API_KEY", raising=False)

    description = tmp_path / "description.md"
    description.write_text(
        "Load customer CSV data, clean invalid emails, validate required fields, "
        "and prepare it for the analytics warehouse daily.\n",
        encoding="utf-8",
    )
    sample = tmp_path / "sample.csv"
    sample.write_text(
        "customer_id,email,signup_date,amount\n1,a@example.com,2026-01-01,10.5\n2,,2026-01-02,20\n",
        encoding="utf-8",
    )
    out = tmp_path / "draft.pipespec.json"

    runner = CliRunner()
    res = runner.invoke(
        app,
        ["elicit", "--in", str(description), "--data", str(sample), "--out", str(out)],
    )

    assert res.exit_code == 0, res.output
    assert out.exists()
    assert validate_file(out, semantic_checks=True).ok

    session_path = tmp_path / "draft.pipespec.json.elicitation.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    assert session["approval_state"] == "questions_pending"
    assert any(question["slot"] == "approval" for question in session["questions"])
