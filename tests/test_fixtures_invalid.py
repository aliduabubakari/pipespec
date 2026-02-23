from __future__ import annotations

from pathlib import Path

from pipespec_validator.validator import validate_file


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "schema" / "fixtures"


def test_invalid_fixtures_fail():
    files = sorted([p for p in FIXTURES.iterdir() if p.is_file() and p.suffix.lower() in {".json", ".yaml", ".yml"}])
    assert files, "No fixture files found under schema/fixtures"

    for f in files:
        res = validate_file(f, semantic_checks=True)
        assert not res.ok, f"Fixture should be invalid but passed: {f}"
        assert res.errors, f"Fixture should have errors: {f}"