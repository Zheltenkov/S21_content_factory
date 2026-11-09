"""What the methodologist's dialogue stores: the thread of messages, the editable fields of the brief, and the
model's answer schemas. Messages and decisions are appended, never rewritten.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, get_args
from uuid import UUID, uuid4

from pydantic import Field, ValidationError

from ..core.access import current_owner
from ..core.contracts import STORED_SCHEMA
from ..core.intents import MessageKind
from ..core.storage import Locks, read_owned, write_model
from .collaboration import Resolution
from .models import Brief, Contract, Run

lock = Locks()


EditableField = Literal[
    "title", "description", "outcomes", "audience", "start_level", "start_skills", "learner_term", "program_outcomes",
    "customer_requirements", "format", "group_size", "hours", "tasks_count", "skills", "tools",
    "storytelling_type", "storytelling", "student_result", "checking", "constraints", "output_package",
    "references", "notes", "concept_feedback", "excluded_sources", "block_scope", "diagrams", "tables", "formulas", "bonus", "curriculum_policy",
]


FIELDS: dict[str, str] = {
    "curriculum_policy": "Как учитывать учебный план",
    "title": "Название проекта", "description": "Краткое описание", "outcomes": "Образовательные результаты",
    "audience": "Аудитория", "start_level": "Стартовый уровень", "start_skills": "Что участники уже умеют",
    "learner_term": "Как называем участника",
    "program_outcomes": "ОР программы", "customer_requirements": "Требования заказчика",
    "format": "Тип проекта", "group_size": "Размер группы", "hours": "Трудоёмкость, астр. ч",
    "tasks_count": "Число основных заданий", "skills": "Получаемые навыки", "tools": "Инструменты и ПО",
    "storytelling_type": "Тип сторителлинга", "storytelling": "Сторителлинг",
    "student_result": "Что участник сдаёт в итоге", "checking": "Кто и как проверяет работу",
    "constraints": "Ограничения", "output_package": "Комплект генератора", "references": "Опорные материалы и ссылки",
    "notes": "Уточнения методолога", "concept_feedback": "Замечания к замыслу", "excluded_sources": "Не учитывать фрагменты файлов",
    "block_scope": "Цели блока: кто закрывает", "diagrams": "Диаграммы в учебном тексте", "tables": "Таблицы в учебном тексте",
    "formulas": "Формулы в учебном тексте", "bonus": "Бонусное задание",
}


assert set(FIELDS) == set(get_args(EditableField))


Value = str | int | float | bool | None


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProposedChange(Contract):
    field: EditableField
    value: Value
    reason: str = Field(min_length=1, max_length=600)
    origin: Literal["answer", "request", "proposal"]
    question_numbers: list[int] = Field(default_factory=list)


class Answered(Contract):
    number: int = Field(ge=1)
    answer: str = Field(min_length=1, max_length=600)


class Proposal(Contract):
    """Model output: a short reply and full new values of the fields it proposes to change."""

    # What the message is, decided first: code holds the rest of the answer to it (see by_kind).
    kind: MessageKind = "request"

    # First in the schema, so it is decided before the reply is written: a model that writes «зафиксирую» in the reply
    # tends to consider the answer handled and leave a trailing optional list empty.
    # Questions of the card this message answers, in any wording: code makes sure each answer reaches the brief.
    answered: list[Answered] = Field(default_factory=list, max_length=12)
    reply: str = Field(min_length=1, max_length=2000)
    changes: list[ProposedChange] = Field(default_factory=list, max_length=8)
    # The methodologist asks to move on to the next stage; code decides what that stage is.
    proceed: bool = False


class AnswerCheck(Contract):
    """A narrow second look: which open questions the message answers. Asked only when the first call left them open."""

    answered: list[Answered] = Field(default_factory=list, max_length=12)


class Patch(Contract):
    field: str
    label: str
    before: Value
    after: Value
    reason: str = ""
    origin: Literal["answer", "request", "proposal", "manual"]
    question_numbers: list[int] = Field(default_factory=list)
    edited: bool = False


class Message(Contract):
    id: str = Field(default_factory=lambda: uuid4().hex)
    role: Literal["methodologist", "assistant", "system"]
    text: str
    at: str = Field(default_factory=now)
    run_id: str = ""
    patches: list[Patch] = Field(default_factory=list)
    status: Literal["pending", "applied", "rejected"] | None = None
    notes: list[str] = Field(default_factory=list)
    model: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    # How the assistant understood the methodologist's message (request, answer, suggestion, question, off_topic).
    kind: str = ""
    answers: list[Resolution] = Field(default_factory=list)


class Thread(Contract):
    id: str
    owner_id: str
    # The release shape that wrote the file (core.contracts.STORED_SCHEMA): a rollback reads newer files by it.
    schema_version: int = STORED_SCHEMA
    runs: list[str] = Field(min_length=1)
    draft: Brief
    messages: list[Message] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class ThreadStart(Contract):
    run_id: UUID


class MessageRequest(Contract):
    text: str = Field(min_length=1, max_length=4000)
    target_field: EditableField | None = None


class DecisionRequest(Contract):
    verdict: Literal["apply", "reject", "revert"]
    values: dict[int, Value] = Field(default_factory=dict)


class FieldEdit(Contract):
    field: EditableField
    value: Value


Action = Literal["apply_and_continue", "new_version", "confirm_inputs", "keep_hours", "approve", "resume"]


class AdvanceRequest(Contract):
    # The action the methodologist saw on the button: a stale page must not trigger a different step.
    action: Action


def api():  # noqa: ANN201 - lazy import breaks the app ↔ dialogue cycle
    from . import runs as module

    return module


def thread_path(thread_id: str) -> Path:
    path: Path = api().RUNS_DIR / "threads" / f"{thread_id}.json"
    return path


def save_thread(thread: Thread) -> None:
    write_model(thread_path(thread.id), thread)


def load_thread(thread_id: UUID) -> Thread:
    return read_owned(thread_path(str(thread_id)), Thread, current_owner.get(), "Диалог не найден",
                      "Файл этого диалога не читается текущей версией. Начните подготовку заново; файл сохранён.")


def latest_run(thread: Thread) -> Run:
    run: Run = api().load_run(UUID(thread.runs[-1]))
    return run


def latest_pending(thread: Thread) -> Message | None:
    return next((m for m in reversed(thread.messages) if m.status == "pending"), None)


def readable(error: ValidationError) -> str:
    first = error.errors()[0]
    return str(first.get("msg", "недопустимое значение")).removeprefix("Value error, ")
