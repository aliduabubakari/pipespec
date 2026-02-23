from __future__ import annotations

import json
from pathlib import Path

from pipespec_validator import validate_file
from pipespec_validator.reporting import make_report, write_report


def test_report_writes_json(tmp_path: Path):
    res = validate_file("schema/fixtures/invalid_type_mismatch.pipespec.json", semantic_checks=True)
    assert not res.ok

    report = make_report(res, source_path="schema/fixtures/invalid_type_mismatch.pipespec.json")
    out = tmp_path / "report.json"
    write_report(report, out, fmt="json")

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["ok"] is False
    assert data["summary"]["error_count"] > 0
    assert isinstance(data["errors"], list)