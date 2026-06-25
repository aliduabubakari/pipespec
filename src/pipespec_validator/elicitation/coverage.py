from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

from pipespec_validator.models import ValidationResult

from .authority import AuthorityPolicy
from .eda import DataProfile

SlotStatus = Literal["known", "inferred", "missing", "blocked", "risky"]
RiskLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class CoverageSlot:
    slot: str
    label: str
    status: SlotStatus
    risk: RiskLevel
    can_infer: bool
    question: str
    reason: str
    blocks_valid_pipespec: bool = False
    blocks_safe_codegen: bool = False
    value_preview: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CoverageMatrix:
    slots: list[CoverageSlot]

    def to_dict(self) -> dict[str, Any]:
        return {"slots": [slot.to_dict() for slot in self.slots], "summary": self.summary()}

    def summary(self) -> dict[str, int]:
        counts = {"known": 0, "inferred": 0, "missing": 0, "blocked": 0, "risky": 0}
        for slot in self.slots:
            counts[slot.status] += 1
        return counts

    def open_slots(self) -> list[CoverageSlot]:
        return [slot for slot in self.slots if slot.status in {"missing", "blocked", "risky"}]

    def safe_for_draft(self) -> bool:
        return not any(slot.blocks_valid_pipespec for slot in self.open_slots())

    def safe_for_codegen(self, *, approval_state: str) -> bool:
        if approval_state != "approved":
            return False
        return not any(slot.blocks_safe_codegen for slot in self.open_slots())


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _preview(value: Any, max_len: int = 120) -> str:
    text = str(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _draft_has_path(draft: dict[str, Any] | None, path: tuple[str, ...]) -> bool:
    cur: Any = draft or {}
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    if cur in (None, "", [], {}):
        return False
    return True


def _status_for(
    *,
    slot: str,
    known: bool,
    inferred: bool = False,
    risky: bool = False,
    question: str,
    label: str,
    reason: str,
    blocks_valid_pipespec: bool = False,
    blocks_safe_codegen: bool = False,
    value_preview: str | None = None,
    policy: AuthorityPolicy,
) -> CoverageSlot:
    decision = policy.decide(
        slot,
        blocks_valid_pipespec=blocks_valid_pipespec,
        blocks_safe_codegen=blocks_safe_codegen,
    )
    if risky:
        status: SlotStatus = "risky"
    elif known:
        status = "inferred" if inferred else "known"
    elif decision.mode == "approval_required":
        status = "blocked"
    else:
        status = "missing"
    return CoverageSlot(
        slot=slot,
        label=label,
        status=status,
        risk=decision.risk,
        can_infer=decision.can_infer,
        question=question,
        reason=reason if reason else decision.reason,
        blocks_valid_pipespec=decision.blocks_valid_pipespec,
        blocks_safe_codegen=decision.blocks_safe_codegen,
        value_preview=value_preview,
    )


def _validation_slots(
    validation_result: ValidationResult | None,
    policy: AuthorityPolicy,
) -> list[CoverageSlot]:
    if validation_result is None:
        return []

    slots: list[CoverageSlot] = []
    for item in validation_result.errors + validation_result.warnings:
        path = item.instance_path or "(root)"
        rule_id = item.details.get("rule_id") if isinstance(item.details, dict) else None
        slot = f"validation.{item.kind}.{path}"
        question = f"How should we resolve this PipeSpec issue: {item.message}"
        if rule_id:
            question += f" ({rule_id})"
        slots.append(
            _status_for(
                slot=slot,
                known=False,
                question=question,
                label=f"Validation issue at {path}",
                reason=item.message,
                blocks_valid_pipespec=item.kind != "semantic",
                blocks_safe_codegen=True,
                policy=policy,
            )
        )
    return slots


def build_coverage_matrix(
    *,
    description_text: str,
    data_profiles: list[DataProfile] | None = None,
    draft: dict[str, Any] | None = None,
    validation_result: ValidationResult | None = None,
    known_facts: dict[str, Any] | None = None,
    approval_state: str = "drafting",
    policy: AuthorityPolicy | None = None,
) -> CoverageMatrix:
    policy = policy or AuthorityPolicy()
    known_facts = known_facts or {}
    profiles = data_profiles or []
    text = description_text.lower()
    slots: list[CoverageSlot] = []

    source_known = bool(profiles) or _contains_any(
        text,
        (
            r"\bfrom\b",
            r"\bsource\b",
            r"\bextract\b",
            r"\bread\b",
            r"\bapi\b",
            r"\bcsv\b",
            r"\bjson\b",
            r"\bs3\b",
            r"\bdatabase\b",
            r"\bpostgres\b",
            r"\bsnowflake\b",
        ),
    )
    sink_known = _contains_any(
        text,
        (
            r"\bload\b",
            r"\bwrite\b",
            r"\bsink\b",
            r"\bwarehouse\b",
            r"\btarget\b",
            r"\bpostgres\b",
            r"\bbigquery\b",
            r"\bsnowflake\b",
            r"\btable\b",
            r"\bs3\b",
        ),
    ) or _draft_has_path(draft, ("integrations", "data_lineage"))
    transform_known = _contains_any(
        text,
        (
            r"\btransform\b",
            r"\bclean\b",
            r"\benrich\b",
            r"\bjoin\b",
            r"\baggregate\b",
            r"\bfilter\b",
            r"\bmap\b",
            r"\bno transform",
        ),
    )
    schedule_known = _contains_any(
        text,
        (
            r"\bdaily\b",
            r"\bhourly\b",
            r"\bweekly\b",
            r"\bcron\b",
            r"\bschedule\b",
            r"\bon demand\b",
            r"\bmanual\b",
        ),
    )
    quality_known = _contains_any(
        text,
        (r"\bvalidate\b", r"\bquality\b", r"\bnull\b", r"\bschema\b", r"\bcheck\b", r"\banomaly\b"),
    )
    auth_needed = _contains_any(
        text,
        (
            r"\bapi\b",
            r"\bdatabase\b",
            r"\bpostgres\b",
            r"\bsnowflake\b",
            r"\bbigquery\b",
            r"\bs3\b",
        ),
    )
    auth_known = _contains_any(
        text,
        (r"\benv var\b", r"\benvironment variable\b", r"\bsecret\b", r"\btoken\b", r"\bapi key\b"),
    )
    write_mode_known = _contains_any(
        text, (r"\bappend\b", r"\boverwrite\b", r"\bupsert\b", r"\bmerge\b")
    )
    sensitive_columns = [
        {"path": profile.path, "column": column.name}
        for profile in profiles
        for column in profile.columns
        if column.possible_sensitive
    ]

    slots.extend(
        [
            _status_for(
                slot="business_goal",
                label="Business goal",
                known=bool(description_text.strip()) or "business_goal" in known_facts,
                question="What outcome should this pipeline deliver, and how will success be judged?",
                reason="The pipeline description is the primary source of intent.",
                blocks_safe_codegen=True,
                value_preview=_preview(
                    description_text.strip().splitlines()[0] if description_text.strip() else ""
                ),
                policy=policy,
            ),
            _status_for(
                slot="sources",
                label="Source systems",
                known=source_known,
                inferred=bool(profiles),
                question="Which source systems or files should the pipeline read from?",
                reason="Sources define extractor components and lineage.",
                blocks_valid_pipespec=not source_known,
                blocks_safe_codegen=True,
                value_preview=", ".join(profile.path for profile in profiles) if profiles else None,
                policy=policy,
            ),
            _status_for(
                slot="source_schema",
                label="Source schema",
                known=bool(profiles),
                inferred=bool(profiles),
                question="Can you provide sample data or a schema for the source?",
                reason="EDA can infer input formats, columns, likely keys, and quality risks.",
                policy=policy,
            ),
            _status_for(
                slot="target_sink",
                label="Target sink",
                known=sink_known,
                question="Where should the pipeline write its final output?",
                reason="The target controls loader components and output integrations.",
                blocks_safe_codegen=True,
                policy=policy,
            ),
            _status_for(
                slot="transformation_rules",
                label="Transformation rules",
                known=transform_known,
                question="What cleaning, mapping, joins, aggregations, or enrichments should happen?",
                reason="Transformations are business semantics and should be user-confirmed.",
                blocks_safe_codegen=True,
                policy=policy,
            ),
            _status_for(
                slot="quality_checks",
                label="Quality checks",
                known=quality_known,
                inferred=bool(profiles) and not quality_known,
                question="Which data quality checks should block or warn the pipeline?",
                reason="Quality expectations decide whether to add QualityCheck components.",
                policy=policy,
            ),
            _status_for(
                slot="schedule",
                label="Schedule",
                known=schedule_known,
                question="Should the pipeline run manually, on a schedule, or when data arrives?",
                reason="Scheduling is not required by PipeSpec v1, but it affects OPOS/codegen.",
                blocks_safe_codegen=True,
                policy=policy,
            ),
            _status_for(
                slot="authentication",
                label="Authentication and secrets",
                known=(not auth_needed) or auth_known,
                question="Which environment variable or secret reference should be used for each authenticated system?",
                reason="Secrets must never be embedded in PipeSpec.",
                blocks_safe_codegen=auth_needed,
                policy=policy,
            ),
            _status_for(
                slot="write_mode",
                label="Write semantics",
                known=(not sink_known) or write_mode_known,
                question="Should target writes append, overwrite, upsert, or merge records?",
                reason="Write semantics can be destructive and must be user-controlled.",
                blocks_safe_codegen=sink_known,
                policy=policy,
            ),
            _status_for(
                slot="pii_sensitive_data",
                label="Sensitive data handling",
                known=not sensitive_columns
                or _contains_any(text, (r"\bpii\b", r"\bsensitive\b", r"\bmask\b")),
                risky=bool(sensitive_columns)
                and not _contains_any(text, (r"\bpii\b", r"\bsensitive\b", r"\bmask\b")),
                question="The sample data appears to contain sensitive columns. How should they be handled?",
                reason="Potential sensitive data was detected from column names.",
                blocks_safe_codegen=bool(sensitive_columns),
                value_preview=_preview(sensitive_columns),
                policy=policy,
            ),
            _status_for(
                slot="owner_domain",
                label="Owner and domain",
                known=_contains_any(text, (r"\bowner\b", r"\bdomain\b", r"\bteam\b"))
                or "owner" in known_facts,
                question="Who owns this pipeline, and what business/data domain should it belong to?",
                reason="OPOS requires ownership and domain metadata.",
                blocks_safe_codegen=True,
                policy=policy,
            ),
            _status_for(
                slot="approval",
                label="Approval gate",
                known=approval_state == "approved",
                question="Do you approve this PipeSpec draft for OPOS compilation and code generation?",
                reason="Downstream compilation/codegen should wait for explicit user approval.",
                blocks_safe_codegen=True,
                policy=policy,
            ),
        ]
    )

    if draft is not None:
        slots.append(
            _status_for(
                slot="draft_pipespec",
                label="Draft PipeSpec",
                known=True,
                inferred=True,
                question="",
                reason="A draft PipeSpec document exists.",
                value_preview=_preview(draft.get("pipeline_summary", {}).get("name", "")),
                policy=policy,
            )
        )

    slots.extend(_validation_slots(validation_result, policy))
    return CoverageMatrix(slots=slots)
