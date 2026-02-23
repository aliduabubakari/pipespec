from __future__ import annotations

from pathlib import Path

from pipespec_validator.validator import validate_file


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "schema" / "examples"


def test_examples_are_valid():
    files = sorted([p for p in EXAMPLES.iterdir() if p.is_file() and p.suffix.lower() in {".json", ".yaml", ".yml"}])
    assert files, "No example files found under schema/examples"

    for f in files:
        res = validate_file(f, semantic_checks=True)
        assert res.ok, f"Example should be valid but failed: {f} errors={res.errors}"