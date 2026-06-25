from .models import ValidationErrorItem, ValidationResult
from .validator import validate_dict, validate_file
from .resources import load_canonical_schema, load_prompt_profile
from .reporting import make_report, write_report
from .hints import Hint, generate_hints, hints_to_dict, llm_escalation_needed, escalation_summary
from .semantic_rules import run_semantic_checks, SEMANTIC_RULES, SemanticRule
from .corrections import (
    FixAction,
    autofix_dict,
    autofix_multi_round,
    autofix_file,
    MAX_ROUNDS,
)
from .elicitation import (
    AuthorityDecision,
    AuthorityPolicy,
    CoverageMatrix,
    CoverageSlot,
    DataProfile,
    ElicitationSession,
    build_coverage_matrix,
    plan_questions,
    profile_data_paths,
    summarize_profiles,
)

__all__ = [
    # Core validation
    "ValidationErrorItem",
    "ValidationResult",
    "validate_dict",
    "validate_file",
    # Schema / prompt resources
    "load_canonical_schema",
    "load_prompt_profile",
    # Reporting
    "make_report",
    "write_report",
    # Hints
    "Hint",
    "generate_hints",
    "hints_to_dict",
    "llm_escalation_needed",
    "escalation_summary",
    # Semantic rules
    "run_semantic_checks",
    "SEMANTIC_RULES",
    "SemanticRule",
    # AutoFix / corrections
    "FixAction",
    "autofix_dict",
    "autofix_multi_round",
    "autofix_file",
    "MAX_ROUNDS",
    # Elicitation
    "AuthorityDecision",
    "AuthorityPolicy",
    "CoverageMatrix",
    "CoverageSlot",
    "DataProfile",
    "ElicitationSession",
    "build_coverage_matrix",
    "plan_questions",
    "profile_data_paths",
    "summarize_profiles",
]
