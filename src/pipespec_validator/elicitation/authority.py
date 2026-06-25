from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AuthorityMode = Literal["infer", "ask", "approval_required"]
RiskLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class AuthorityDecision:
    slot: str
    mode: AuthorityMode
    risk: RiskLevel
    reason: str
    blocks_valid_pipespec: bool = False
    blocks_safe_codegen: bool = False

    @property
    def can_infer(self) -> bool:
        return self.mode == "infer"

    @property
    def requires_user(self) -> bool:
        return self.mode in {"ask", "approval_required"}


class AuthorityPolicy:
    """
    Decides who is allowed to fill a pipeline-design slot.

    The policy is intentionally deterministic because it is the user-control
    boundary. The LLM can propose text or a draft, but this module decides
    whether a missing fact can be inferred or must be escalated.
    """

    HIGH_CONTROL_PATTERNS = (
        "secret",
        "credential",
        "authentication",
        "auth",
        "password",
        "token",
        "api_key",
        "pii",
        "sensitive",
        "destructive",
        "write_mode",
        "overwrite",
        "upsert",
        "delete",
        "approval",
        "deployment",
        "production",
    )
    USER_REQUIRED_PATTERNS = (
        "business_goal",
        "target",
        "sink",
        "schedule",
        "transformation",
        "quality",
        "owner",
        "domain",
        "sla",
        "freshness",
        "retention",
        "error_handling",
        "notification",
    )
    INFERABLE_PATTERNS = (
        "pipeline_name",
        "component_ids",
        "flow_structure",
        "component_categories",
        "executor_types",
        "source_schema",
        "input_format",
        "data_profile",
        "retry_defaults",
        "concurrency_defaults",
    )

    def decide(
        self,
        slot: str,
        *,
        blocks_valid_pipespec: bool = False,
        blocks_safe_codegen: bool = False,
    ) -> AuthorityDecision:
        key = slot.lower()

        if any(pattern in key for pattern in self.HIGH_CONTROL_PATTERNS):
            return AuthorityDecision(
                slot=slot,
                mode="approval_required",
                risk="high",
                reason="This slot affects credentials, sensitive data, writes, or deployment safety.",
                blocks_valid_pipespec=blocks_valid_pipespec,
                blocks_safe_codegen=True,
            )

        if any(pattern in key for pattern in self.USER_REQUIRED_PATTERNS):
            return AuthorityDecision(
                slot=slot,
                mode="ask",
                risk="medium" if not blocks_safe_codegen else "high",
                reason="This slot carries business or operational intent that should come from the user.",
                blocks_valid_pipespec=blocks_valid_pipespec,
                blocks_safe_codegen=blocks_safe_codegen,
            )

        if any(pattern in key for pattern in self.INFERABLE_PATTERNS):
            return AuthorityDecision(
                slot=slot,
                mode="infer",
                risk="low",
                reason="This slot can usually be inferred from the description, draft, or data profile.",
                blocks_valid_pipespec=blocks_valid_pipespec,
                blocks_safe_codegen=blocks_safe_codegen,
            )

        if blocks_valid_pipespec:
            return AuthorityDecision(
                slot=slot,
                mode="ask",
                risk="medium",
                reason="This slot is required to produce a valid PipeSpec document.",
                blocks_valid_pipespec=True,
                blocks_safe_codegen=blocks_safe_codegen,
            )

        return AuthorityDecision(
            slot=slot,
            mode="ask",
            risk="medium",
            reason="No safe deterministic inference rule exists for this slot.",
            blocks_valid_pipespec=blocks_valid_pipespec,
            blocks_safe_codegen=blocks_safe_codegen,
        )

    def requires_approval_before_codegen(self, approval_state: str) -> bool:
        return approval_state != "approved"
