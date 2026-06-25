from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from uuid import uuid4

from .coverage import CoverageMatrix, CoverageSlot
from .session import SessionQuestion


@dataclass(frozen=True)
class PlannedQuestion:
    id: str
    slot: str
    question: str
    risk: str
    reason: str
    blocks_valid_pipespec: bool = False
    blocks_safe_codegen: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_session_question(self) -> SessionQuestion:
        return SessionQuestion(
            id=self.id,
            slot=self.slot,
            question=self.question,
            risk=self.risk,
            reason=self.reason,
            blocks_valid_pipespec=self.blocks_valid_pipespec,
            blocks_safe_codegen=self.blocks_safe_codegen,
        )


def _priority(slot: CoverageSlot) -> tuple[int, int, str]:
    status_score = {"blocked": 0, "risky": 1, "missing": 2, "inferred": 3, "known": 4}
    risk_score = {"high": 0, "medium": 1, "low": 2}
    if slot.blocks_valid_pipespec:
        return (-1, risk_score.get(slot.risk, 2), slot.slot)
    if slot.blocks_safe_codegen:
        return (0, risk_score.get(slot.risk, 2), slot.slot)
    return (status_score.get(slot.status, 4), risk_score.get(slot.risk, 2), slot.slot)


def plan_questions(
    coverage: CoverageMatrix,
    *,
    existing_questions: list[SessionQuestion] | None = None,
    max_questions: int = 5,
) -> list[PlannedQuestion]:
    existing = {
        (question.slot, question.question)
        for question in existing_questions or []
        if question.status in {"open", "answered"}
    }
    candidates = sorted(coverage.open_slots(), key=_priority)
    questions: list[PlannedQuestion] = []

    for slot in candidates:
        if not slot.question:
            continue
        key = (slot.slot, slot.question)
        if key in existing:
            continue
        questions.append(
            PlannedQuestion(
                id=str(uuid4()),
                slot=slot.slot,
                question=slot.question,
                risk=slot.risk,
                reason=slot.reason,
                blocks_valid_pipespec=slot.blocks_valid_pipespec,
                blocks_safe_codegen=slot.blocks_safe_codegen,
            )
        )
        if len(questions) >= max_questions:
            break

    return questions
