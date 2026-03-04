from __future__ import annotations

"""
tests/test_hints.py
-------------------
Tests for the hints engine, covering the exact error patterns seen in practice.
"""

import pytest

from pipespec_validator.hints import (
    Hint,
    generate_hints,
    hints_to_dict,
    llm_escalation_needed,
    escalation_summary,
)
from pipespec_validator.models import ValidationErrorItem


# ---------------------------------------------------------------------------
# Fixtures — mirror the exact errors from the post-autofix run in the logs
# ---------------------------------------------------------------------------

def _schema_error(message: str, instance_path: str, schema_path: str = "") -> ValidationErrorItem:
    return ValidationErrorItem(
        kind="schema",
        message=message,
        instance_path=instance_path,
        schema_path=schema_path,
        details={"validator": "required", "validator_value": ["id", "name", "category", "executor_type", "io_spec"]},
    )


def _semantic_warning(rule_id: str, message: str, instance_path: str) -> ValidationErrorItem:
    return ValidationErrorItem(
        kind="semantic",
        message=message,
        instance_path=instance_path,
        schema_path="",
        details={"rule_id": rule_id},
    )


# Post-autofix errors from the actual log output (FIX-COMP-01 ran, but content gaps remain)
POST_AUTOFIX_ERRORS = [
    _schema_error("'category' is a required property", "/components/0"),
    _schema_error("'executor_type' is a required property", "/components/0"),
    _schema_error("'io_spec' is a required property", "/components/0"),
]


# ---------------------------------------------------------------------------
# Test: missing required fields generate HINT-MISSING-REQUIRED
# ---------------------------------------------------------------------------

class TestMissingRequiredHints:

    def test_generates_hint_for_post_autofix_errors(self):
        hints = generate_hints(POST_AUTOFIX_ERRORS)
        codes = [h.code for h in hints]
        assert "HINT-MISSING-REQUIRED" in codes

    def test_single_grouped_hint_for_same_path(self):
        """All three errors are at /components/0 — should produce ONE grouped hint."""
        hints = generate_hints(POST_AUTOFIX_ERRORS)
        missing_hints = [h for h in hints if h.code == "HINT-MISSING-REQUIRED"]
        assert len(missing_hints) == 1
        assert missing_hints[0].paths == ["/components/0"]

    def test_missing_fields_listed_in_details(self):
        hints = generate_hints(POST_AUTOFIX_ERRORS)
        h = next(h for h in hints if h.code == "HINT-MISSING-REQUIRED")
        assert sorted(h.details["missing_fields"]) == ["category", "executor_type", "io_spec"]

    def test_content_fields_trigger_high_severity(self):
        """'category' and 'executor_type' are content fields — hint should be high severity."""
        hints = generate_hints(POST_AUTOFIX_ERRORS)
        h = next(h for h in hints if h.code == "HINT-MISSING-REQUIRED")
        assert h.severity == "high"

    def test_content_tier_set(self):
        hints = generate_hints(POST_AUTOFIX_ERRORS)
        h = next(h for h in hints if h.code == "HINT-MISSING-REQUIRED")
        assert h.tier == "content"

    def test_llm_escalation_flagged(self):
        hints = generate_hints(POST_AUTOFIX_ERRORS)
        assert llm_escalation_needed(hints) is True

    def test_escalation_summary_non_empty(self):
        hints = generate_hints(POST_AUTOFIX_ERRORS)
        summary = escalation_summary(hints)
        assert "LLM escalation" in summary
        assert "pipespec correct" in summary


# ---------------------------------------------------------------------------
# Test: wrong type errors generate specific HINT-* codes
# ---------------------------------------------------------------------------

class TestWrongTypeHints:

    def test_components_not_array(self):
        errors = [
            ValidationErrorItem(
                kind="schema",
                message="{'oops': 'should be an array'} is not of type 'array'",
                instance_path="/components",
                schema_path="/properties/components/type",
                details={"validator": "type"},
            )
        ]
        hints = generate_hints(errors)
        codes = [h.code for h in hints]
        assert "HINT-COMPONENTS-NOT-ARRAY" in codes

    def test_components_not_array_structural_tier(self):
        errors = [
            ValidationErrorItem(
                kind="schema",
                message="{'oops': 'x'} is not of type 'array'",
                instance_path="/components",
                schema_path="",
                details={},
            )
        ]
        hints = generate_hints(errors)
        h = next(h for h in hints if h.code == "HINT-COMPONENTS-NOT-ARRAY")
        assert h.tier == "structural"
        assert "FIX-COMP-01" in (h.details or {}).get("autofix_code", "")

    def test_nodes_not_map(self):
        errors = [
            ValidationErrorItem(
                kind="schema",
                message="[1, 2, 3] is not of type 'object'",
                instance_path="/flow_structure/nodes",
                schema_path="",
                details={},
            )
        ]
        hints = generate_hints(errors)
        codes = [h.code for h in hints]
        assert "HINT-NODES-NOT-MAP" in codes

    def test_nodes_not_map_structural_tier(self):
        errors = [
            ValidationErrorItem(
                kind="schema",
                message="[] is not of type 'object'",
                instance_path="/flow_structure/nodes",
                schema_path="",
                details={},
            )
        ]
        hints = generate_hints(errors)
        h = next(h for h in hints if h.code == "HINT-NODES-NOT-MAP")
        assert h.tier == "structural"

    def test_empty_entry_points(self):
        """
        Mirrors the exact error produced after FIX-FLOW-06 removes a dangling
        entry_point, leaving entry_points: [] which fails minItems=1.
        """
        errors = [
            ValidationErrorItem(
                kind="schema",
                message="[] should be non-empty",
                instance_path="/flow_structure/entry_points",
                schema_path="/properties/flow_structure/properties/entry_points/minItems",
                details={"validator": "minItems", "validator_value": 1},
            )
        ]
        hints = generate_hints(errors)
        codes = [h.code for h in hints]
        assert "HINT-EMPTY-ENTRY-POINTS" in codes

    def test_empty_entry_points_is_content_tier(self):
        """entry_points cannot be filled automatically — requires human knowledge."""
        errors = [
            ValidationErrorItem(
                kind="schema",
                message="[] should be non-empty",
                instance_path="/flow_structure/entry_points",
                schema_path="",
                details={},
            )
        ]
        hints = generate_hints(errors)
        h = next(h for h in hints if h.code == "HINT-EMPTY-ENTRY-POINTS")
        assert h.tier == "content"
        assert h.severity == "high"
        assert h.details.get("needs_human_review") is True

    def test_empty_entry_points_suggested_action_is_concrete(self):
        """Suggested action should give a concrete example, not vague guidance."""
        errors = [
            ValidationErrorItem(
                kind="schema",
                message="[] should be non-empty",
                instance_path="/flow_structure/entry_points",
                schema_path="",
                details={},
            )
        ]
        hints = generate_hints(errors)
        h = next(h for h in hints if h.code == "HINT-EMPTY-ENTRY-POINTS")
        assert "entry_points" in h.suggested_action
        assert "extract_data" in h.suggested_action  # concrete example in action text


# ---------------------------------------------------------------------------
# Test: root-level missing keys
# ---------------------------------------------------------------------------

class TestRootLevelHints:

    def test_missing_top_level_keys(self):
        errors = [
            _schema_error("'components' is a required property", ""),
            _schema_error("'flow_structure' is a required property", ""),
        ]
        hints = generate_hints(errors)
        codes = [h.code for h in hints]
        assert "HINT-MISSING-TOP-LEVEL" in codes

    def test_missing_top_level_content_fields_trigger_high(self):
        errors = [
            _schema_error("'components' is a required property", ""),
        ]
        hints = generate_hints(errors)
        h = next((h for h in hints if h.code == "HINT-MISSING-TOP-LEVEL"), None)
        assert h is not None
        assert h.severity == "high"


# ---------------------------------------------------------------------------
# Test: semantic warnings generate hints
# ---------------------------------------------------------------------------

class TestSemanticHints:

    def test_cycle_detected_hint(self):
        warnings = [
            _semantic_warning(
                "PIPESPEC-SEM-06",
                "Cycle detected in flow_structure",
                "/flow_structure/edges",
            )
        ]
        hints = generate_hints([], warnings)
        codes = [h.code for h in hints]
        assert "HINT-CYCLE-DETECTED" in codes

    def test_unreachable_node_hint(self):
        warnings = [
            _semantic_warning(
                "PIPESPEC-SEM-07",
                "Unreachable node(s): ['load_data']",
                "/flow_structure/nodes",
            )
        ]
        hints = generate_hints([], warnings)
        codes = [h.code for h in hints]
        assert "HINT-UNREACHABLE-NODE" in codes

    def test_semantic_tier_set(self):
        warnings = [
            _semantic_warning("PIPESPEC-SEM-01", "Duplicate ids", "/components")
        ]
        hints = generate_hints([], warnings)
        assert all(h.tier == "semantic" for h in hints)

    def test_unknown_semantic_rule_ignored(self):
        warnings = [
            _semantic_warning("PIPESPEC-SEM-99", "Unknown future rule", "/somewhere")
        ]
        hints = generate_hints([], warnings)
        # Should not crash and should produce no hints for unknown rules
        assert len(hints) == 0


# ---------------------------------------------------------------------------
# Test: deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:

    def test_duplicate_errors_produce_single_hint(self):
        """Same error repeated twice → one hint."""
        errors = [
            _schema_error("'category' is a required property", "/components/0"),
            _schema_error("'category' is a required property", "/components/0"),
        ]
        hints = generate_hints(errors)
        missing = [h for h in hints if h.code == "HINT-MISSING-REQUIRED"]
        assert len(missing) == 1

    def test_different_paths_produce_separate_hints(self):
        """Same missing field but different component indices → separate hints."""
        errors = [
            _schema_error("'category' is a required property", "/components/0"),
            _schema_error("'category' is a required property", "/components/1"),
        ]
        hints = generate_hints(errors)
        missing = [h for h in hints if h.code == "HINT-MISSING-REQUIRED"]
        assert len(missing) == 2


# ---------------------------------------------------------------------------
# Test: serialization
# ---------------------------------------------------------------------------

class TestSerialization:

    def test_hints_to_dict_is_json_serialisable(self):
        import json
        hints = generate_hints(POST_AUTOFIX_ERRORS)
        d = hints_to_dict(hints)
        # Should not raise
        serialised = json.dumps(d)
        assert "HINT-MISSING-REQUIRED" in serialised

    def test_hint_dict_has_required_keys(self):
        hints = generate_hints(POST_AUTOFIX_ERRORS)
        for h_dict in hints_to_dict(hints):
            assert "code" in h_dict
            assert "tier" in h_dict
            assert "severity" in h_dict
            assert "message" in h_dict
            assert "paths" in h_dict
            assert "suggested_action" in h_dict


# ---------------------------------------------------------------------------
# Test: sorting
# ---------------------------------------------------------------------------

class TestHintSorting:

    def test_high_severity_first(self):
        errors = [
            ValidationErrorItem(
                kind="schema",
                message="[] is not of type 'object'",
                instance_path="/flow_structure/nodes",
                schema_path="",
                details={},
            ),
            *POST_AUTOFIX_ERRORS,
        ]
        hints = generate_hints(errors)
        severities = [h.severity for h in hints]
        order = {"high": 0, "medium": 1, "low": 2}
        assert severities == sorted(severities, key=lambda s: order.get(s, 9))


# ---------------------------------------------------------------------------
# Test: no errors → no hints (clean case)
# ---------------------------------------------------------------------------

class TestCleanDocument:

    def test_no_errors_no_hints(self):
        hints = generate_hints([])
        assert hints == []

    def test_no_escalation_needed_for_empty(self):
        assert llm_escalation_needed([]) is False

    def test_empty_escalation_summary(self):
        assert escalation_summary([]) == ""
