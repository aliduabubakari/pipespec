from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

ApprovalState = Literal["drafting", "questions_pending", "draft_ready", "approved"]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class Assumption:
    slot: str
    value: Any
    rationale: str
    risk: str = "low"
    created_at: str = field(default_factory=utc_now)


@dataclass
class SessionQuestion:
    id: str
    slot: str
    question: str
    risk: str
    reason: str
    blocks_valid_pipespec: bool = False
    blocks_safe_codegen: bool = False
    status: Literal["open", "answered", "skipped"] = "open"
    answer: str | None = None
    created_at: str = field(default_factory=utc_now)


@dataclass
class ElicitationSession:
    session_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    description_path: str | None = None
    data_paths: list[str] = field(default_factory=list)
    approval_state: ApprovalState = "drafting"
    known_facts: dict[str, Any] = field(default_factory=dict)
    inferred_facts: dict[str, Any] = field(default_factory=dict)
    assumptions: list[Assumption] = field(default_factory=list)
    eda_profiles: list[dict[str, Any]] = field(default_factory=list)
    coverage_slots: list[dict[str, Any]] = field(default_factory=list)
    questions: list[SessionQuestion] = field(default_factory=list)
    validation_messages: list[dict[str, Any]] = field(default_factory=list)
    draft_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def touch(self) -> None:
        self.updated_at = utc_now()

    def add_assumption(self, slot: str, value: Any, rationale: str, risk: str = "low") -> None:
        self.assumptions.append(Assumption(slot=slot, value=value, rationale=rationale, risk=risk))
        self.touch()

    def add_questions(self, questions: list[SessionQuestion]) -> None:
        existing = {(q.slot, q.question) for q in self.questions}
        for question in questions:
            key = (question.slot, question.question)
            if key not in existing:
                self.questions.append(question)
                existing.add(key)
        self.touch()

    def open_questions(self) -> list[SessionQuestion]:
        return [question for question in self.questions if question.status == "open"]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ElicitationSession:
        session = cls(
            session_id=data.get("session_id") or str(uuid4()),
            created_at=data.get("created_at") or utc_now(),
            updated_at=data.get("updated_at") or utc_now(),
            description_path=data.get("description_path"),
            data_paths=list(data.get("data_paths") or []),
            approval_state=data.get("approval_state") or "drafting",
            known_facts=dict(data.get("known_facts") or {}),
            inferred_facts=dict(data.get("inferred_facts") or {}),
            eda_profiles=list(data.get("eda_profiles") or []),
            coverage_slots=list(data.get("coverage_slots") or []),
            validation_messages=list(data.get("validation_messages") or []),
            draft_path=data.get("draft_path"),
            metadata=dict(data.get("metadata") or {}),
        )
        session.assumptions = [
            Assumption(**item) for item in data.get("assumptions", []) if isinstance(item, dict)
        ]
        session.questions = [
            SessionQuestion(**item) for item in data.get("questions", []) if isinstance(item, dict)
        ]
        return session

    @classmethod
    def load(cls, path: str | Path) -> ElicitationSession:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Elicitation session must be a JSON object.")
        return cls.from_dict(payload)

    def save(self, path: str | Path) -> None:
        self.touch()
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
