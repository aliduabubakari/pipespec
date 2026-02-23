from __future__ import annotations

from pathlib import Path

from pipespec_validator import validate_file


ROOT = Path(__file__).resolve().parents[1]
SEMANTIC_FIXTURES = ROOT / "schema" / "semantic_fixtures"


def _rule_ids(res) -> list[str]:
    out: list[str] = []
    for w in res.warnings:
        if isinstance(w.details, dict) and w.details.get("rule_id"):
            out.append(w.details["rule_id"])
    return out


def test_semantic_fixtures_directory_exists():
    assert SEMANTIC_FIXTURES.exists(), "Missing schema/semantic_fixtures directory"


def test_cycle_fixture_warns():
    f = SEMANTIC_FIXTURES / "cycle_should_warn.pipespec.json"
    assert f.exists(), f"Missing semantic fixture: {f}"

    res = validate_file(f, semantic_checks=True)
    assert res.ok, "Semantic fixture should be schema-valid (ok=True)"

    rule_ids = _rule_ids(res)
    assert "PIPESPEC-SEM-06" in rule_ids, f"Expected PIPESPEC-SEM-06 warning, got {rule_ids}"


def test_unreachable_fixture_warns():
    f = SEMANTIC_FIXTURES / "unreachable_should_warn.pipespec.json"
    assert f.exists(), f"Missing semantic fixture: {f}"

    res = validate_file(f, semantic_checks=True)
    assert res.ok, "Semantic fixture should be schema-valid (ok=True)"

    rule_ids = _rule_ids(res)
    assert "PIPESPEC-SEM-07" in rule_ids, f"Expected PIPESPEC-SEM-07 warning, got {rule_ids}"