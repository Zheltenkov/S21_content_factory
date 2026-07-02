"""Typed methodology assistant commands for human-in-the-loop checkpoints."""

from __future__ import annotations

import os
import re
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .change_request import ChangeScope, ChangeTargetStage, MethodologistChangeRequest
from .target_registry import SectionTarget, SectionTargetRegistry

MethodologyAssistantCommandType = Literal[
    "approve",
    "request_changes",
    "simplify_task",
    "add_example",
    "fix_failed_criteria",
    "regenerate_section",
]
MethodologyAssistantSource = Literal["deterministic", "pydantic_ai"]

_TARGET_STAGES = set(get_args(ChangeTargetStage))
_SCOPES = set(get_args(ChangeScope))
_STAGE_ALIASES = {
    "structure": "skeleton",
    "quality": "final",
    "evaluation": "final",
    "readme": "final",
    "README": "final",
}

_APPROVE_RE = re.compile(
    r"^\s*/?(?:approve|continue|go|ok|продолж(?:ить|ай)?|подтверждаю|утверждаю|готово|все\s+ок|всё\s+ок)\b",
    re.IGNORECASE,
)
_CHANGE_MARKERS_RE = re.compile(
    r"(?:но|исправ|измени|правк|упрост|проще|добав|пример|критер|непройден|failed|fix|change|simplify|example|диаграм|mermaid|схем|таблиц|markdown\s*table|\btable\b)",
    re.IGNORECASE,
)
_DISPLAY_BLOCK_RE = re.compile(
    r"(?:диаграм|mermaid|схем|таблиц|markdown\s*table|\btable\b)",
    re.IGNORECASE,
)
_SIMPLIFY_RE = re.compile(r"(?:упрост|проще|simplify|make\s+.+?simpler)", re.IGNORECASE)
_ADD_EXAMPLE_RE = re.compile(r"(?:пример|examples?|добавь\s+.{0,80}пример)", re.IGNORECASE)
_FIX_FAILED_RE = re.compile(r"(?:критер|criteria|failed|не\s*пройден|непройден|провален|warning)", re.IGNORECASE)
_REGENERATE_RE = re.compile(
    r"(?:перегенер|сгенерируй\s+заново|пересобер|regenerate|rerun|rebuild)",
    re.IGNORECASE,
)
_TASK_NUMBER_RE = re.compile(r"(?:задач[аеиу]?|задани[еяю]|task)\s*#?\s*(\d+)", re.IGNORECASE)

_STAGE_HINTS: tuple[tuple[str, ChangeTargetStage], ...] = (
    (r"(?:глава\s*3|chapter\s*3|практик|задани|задач|practice)", "practice"),
    (r"(?:глава\s*2|chapter\s*2|теор|theory)", "theory"),
    (r"(?:глава\s*1|chapter\s*1|структур|каркас|skeleton|intro)", "skeleton"),
    (r"(?:план\s+практик|task\s+plan|planning)", "task_planning"),
    (r"(?:датасет|данн|dataset|materials)", "dataset"),
    (r"(?:финал|оценк|критер|quality|evaluation|final)", "final"),
)

_STAGE_TO_WORKFLOW_NODE = {
    "context": "context",
    "task_planning": "task_planning",
    "title": "title_annotation",
    "annotation": "title_annotation",
    "skeleton": "skeleton",
    "theory": "theory",
    "practice": "practice",
    "dataset": "practice",
    "final": "evaluation",
}


class MethodologyAssistantParseContext(BaseModel):
    """Runtime context used to bind a free-form chat message to a checkpoint."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    checkpoint: dict[str, Any] = Field(default_factory=dict)
    target_registry: dict[str, Any] | SectionTargetRegistry = Field(default_factory=dict)
    review_state: dict[str, Any] = Field(default_factory=dict)
    selected_target_id: str = ""


class MethodologyAssistantCommand(BaseModel):
    """Validated command produced from the methodologist chat message."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    command: MethodologyAssistantCommandType
    raw_text: str = Field(min_length=1, max_length=4000)
    checkpoint_id: str = ""
    checkpoint_stage: str = ""
    node_id: str = ""
    workflow_node_id: str = ""
    target_stage: ChangeTargetStage = "final"
    target_selector: str = Field(default="", max_length=300)
    target_id: str = Field(default="", max_length=300)
    scope: ChangeScope = "local_section_only"
    instruction: str = Field(default="", max_length=4000)
    issue_codes: list[str] = Field(default_factory=list)
    forbidden_changes: list[str] = Field(default_factory=list)
    expected_outcome: str = Field(default="", max_length=1000)
    confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    source: MethodologyAssistantSource = "deterministic"

    def to_change_request(self) -> MethodologistChangeRequest:
        """Convert a non-approve assistant command into the existing scoped edit contract."""
        if self.command in {"approve", "regenerate_section"}:
            raise ValueError(f"{self.command} command is not a scoped change request")
        instruction = self.instruction.strip() or self.raw_text.strip()
        return MethodologistChangeRequest(
            target_stage=self.target_stage,
            target_selector=self.target_selector,
            scope=self.scope,
            instruction=instruction,
            issue_codes=self.issue_codes,
            forbidden_changes=self.forbidden_changes,
            expected_outcome=self.expected_outcome,
        )


class MethodologyAssistantCommandParser:
    """Parse methodologist chat text into typed commands with deterministic fallback."""

    def __init__(
        self,
        *,
        pydantic_ai_model: str | None = None,
        enable_pydantic_ai: bool | None = None,
    ) -> None:
        self._pydantic_ai_model = pydantic_ai_model or os.getenv("METHODOLOGY_ASSISTANT_MODEL", "")
        if enable_pydantic_ai is None:
            enable_pydantic_ai = os.getenv("METHODOLOGY_ASSISTANT_PYDANTIC_AI", "").lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        self._enable_pydantic_ai = bool(enable_pydantic_ai and self._pydantic_ai_model)

    async def parse_async(
        self,
        message: str,
        context: MethodologyAssistantParseContext | None = None,
    ) -> MethodologyAssistantCommand:
        """Parse a message, using Pydantic AI only when explicitly enabled."""
        parse_context = context or MethodologyAssistantParseContext()
        if self._enable_pydantic_ai and self._should_try_pydantic_ai(message):
            ai_command = await self._parse_with_pydantic_ai(message, parse_context)
            if ai_command is not None:
                return ai_command
        return self.parse(message, parse_context)

    def parse(
        self,
        message: str,
        context: MethodologyAssistantParseContext | None = None,
    ) -> MethodologyAssistantCommand:
        """Deterministically parse the main command types used in methodology review."""
        parse_context = context or MethodologyAssistantParseContext()
        text = message.strip()
        if not text:
            raise ValueError("assistant command message is empty")

        command_type = self._detect_command(text)
        target = self._target_for_command(command_type, text, parse_context)
        issue_codes = self._failed_issue_codes(parse_context) if command_type == "fix_failed_criteria" else []
        command = MethodologyAssistantCommand(
            command=command_type,
            raw_text=text,
            target_stage=self._target_stage(target, parse_context),
            target_selector=self._selector_for_target(target, parse_context),
            target_id=target.id if target else "",
            scope=self._target_scope(target, command_type, text),
            instruction=self._instruction_for(command_type, text, issue_codes),
            issue_codes=issue_codes,
            forbidden_changes=self._forbidden_changes(command_type, text),
            expected_outcome=self._expected_outcome(command_type),
            confidence=self._confidence(command_type, target),
            source="deterministic",
        )
        return self._bind_checkpoint(command, parse_context)

    def _detect_command(self, text: str) -> MethodologyAssistantCommandType:
        lowered = text.lower()
        if _APPROVE_RE.search(lowered) and not _CHANGE_MARKERS_RE.search(lowered):
            return "approve"
        if _SIMPLIFY_RE.search(lowered):
            return "simplify_task"
        if _ADD_EXAMPLE_RE.search(lowered):
            return "add_example"
        if _REGENERATE_RE.search(lowered):
            return "regenerate_section"
        if _FIX_FAILED_RE.search(lowered) and re.search(r"(?:исправ|fix|заполн|подтян|пройден|failed)", lowered):
            return "fix_failed_criteria"
        return "request_changes"

    def _target_for_command(
        self,
        command_type: MethodologyAssistantCommandType,
        text: str,
        context: MethodologyAssistantParseContext,
    ) -> SectionTarget | None:
        registry = self._registry(context)
        selected = registry.find(context.selected_target_id) if context.selected_target_id else None
        if selected:
            return selected
        if command_type == "simplify_task":
            return self._practice_target(registry, text) or self._target_by_stage(registry, "practice")
        if command_type == "add_example":
            return self._target_by_stage(registry, self._current_stage(context), allowed={"theory", "practice"}) or self._target_by_stage(registry, "theory")
        if command_type == "fix_failed_criteria":
            return self._target_by_stage(registry, "final") or self._target_by_stage(registry, self._current_stage(context))
        if command_type == "regenerate_section":
            hinted_stage = self._stage_from_text(text) or self._current_stage(context)
            return self._target_by_stage(registry, hinted_stage) or self._target_by_stage(registry, self._current_stage(context))
        if self._is_display_block_request(text):
            hinted_stage = self._stage_from_text(text) or self._current_stage(context)
            return (
                self._target_by_stage(registry, hinted_stage, allowed={"theory", "practice", "final", "skeleton"})
                or self._target_by_stage(registry, self._current_stage(context))
                or self._target_by_stage(registry, "final")
            )
        return self._target_by_stage(registry, self._current_stage(context)) or self._target_by_stage(registry, "final")

    def _registry(self, context: MethodologyAssistantParseContext) -> SectionTargetRegistry:
        registry_payload = context.target_registry
        if isinstance(registry_payload, SectionTargetRegistry):
            return registry_payload
        if isinstance(registry_payload, dict):
            try:
                return SectionTargetRegistry.model_validate(registry_payload)
            except ValidationError:
                return SectionTargetRegistry()
        return SectionTargetRegistry()

    def _practice_target(self, registry: SectionTargetRegistry, text: str) -> SectionTarget | None:
        number_match = _TASK_NUMBER_RE.search(text)
        if not number_match:
            return None
        number = number_match.group(1)
        for target in registry.targets:
            if target.stage != "practice":
                continue
            haystack = " ".join([target.id, target.label, target.selector]).lower()
            if re.search(rf"(?:задач[аеиу]?|задани[еяю]|task)[^\d]{{0,12}}{re.escape(number)}\b", haystack):
                return target
            if re.search(rf"(?:^|[._\-\s]){re.escape(number)}(?:$|[._\-\s])", haystack):
                return target
        return None

    def _target_by_stage(
        self,
        registry: SectionTargetRegistry,
        stage: str,
        *,
        allowed: set[str] | None = None,
    ) -> SectionTarget | None:
        normalized_stage = self._normalize_stage(stage)
        if allowed and normalized_stage not in allowed:
            normalized_stage = "theory" if "theory" in allowed else sorted(allowed)[0]
        for target in registry.targets:
            if target.stage == normalized_stage:
                return target
        if allowed:
            for target in registry.targets:
                if target.stage in allowed:
                    return target
        return None

    def _current_stage(self, context: MethodologyAssistantParseContext) -> str:
        checkpoint = context.checkpoint or {}
        stage = str(checkpoint.get("stage") or checkpoint.get("resume_from_node") or "")
        return self._normalize_stage(stage)

    def _target_stage(
        self,
        target: SectionTarget | None,
        context: MethodologyAssistantParseContext,
    ) -> ChangeTargetStage:
        stage = target.stage if target else self._current_stage(context)
        return self._normalize_stage(stage)

    def _normalize_stage(self, value: str) -> ChangeTargetStage:
        normalized = str(value or "").strip()
        normalized = _STAGE_ALIASES.get(normalized, normalized).lower()
        normalized = _STAGE_ALIASES.get(normalized, normalized)
        if normalized in _TARGET_STAGES:
            return normalized  # type: ignore[return-value]
        return "final"

    def _stage_from_text(self, text: str) -> ChangeTargetStage | None:
        lowered = text.lower()
        for pattern, stage in _STAGE_HINTS:
            if re.search(pattern, lowered, re.IGNORECASE):
                return stage
        return None

    def _workflow_node_for_stage(self, stage: str) -> str:
        return _STAGE_TO_WORKFLOW_NODE.get(self._normalize_stage(stage), "evaluation")

    def _target_scope(
        self,
        target: SectionTarget | None,
        command_type: MethodologyAssistantCommandType,
        text: str = "",
    ) -> ChangeScope:
        if self._is_display_block_request(text):
            return "local_section_only"
        if command_type == "simplify_task":
            return "task_only"
        if target and target.scope in _SCOPES:
            return target.scope  # type: ignore[return-value]
        return "local_section_only"

    def _default_selector(self, context: MethodologyAssistantParseContext) -> str:
        checkpoint = context.checkpoint or {}
        for key in ("node_id", "stage", "id", "resume_from_node"):
            value = checkpoint.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:300]
        return ""

    def _selector_for_target(
        self,
        target: SectionTarget | None,
        context: MethodologyAssistantParseContext,
    ) -> str:
        if target:
            return (target.selector or target.id or "")[:300]
        return self._default_selector(context)

    def _instruction_for(
        self,
        command_type: MethodologyAssistantCommandType,
        text: str,
        issue_codes: list[str],
    ) -> str:
        if command_type == "approve":
            return ""
        if command_type == "simplify_task":
            return f"Упрости выбранную практическую задачу: {text}"
        if command_type == "add_example":
            return f"Добавь короткий учебный пример в выбранный блок: {text}"
        if command_type == "fix_failed_criteria":
            code_text = f" ({', '.join(issue_codes)})" if issue_codes else ""
            return f"Исправь непройденные критерии{code_text}: {text}"
        if command_type == "regenerate_section":
            return f"Перегенерируй выбранный раздел с учетом комментария методолога: {text}"
        if self._is_display_block_request(text):
            return (
                "Исправь таблицы и/или Mermaid-диаграммы в выбранном фрагменте. "
                "Если проблема в диаграмме, убери ручные темы и стили Mermaid "
                "(%%{init}, classDef, class, style, linkStyle, fill/stroke/color/background), "
                "оставь корректную структуру диаграммы и читаемые подписи. "
                "Если проблема в таблице, сохрани Markdown-таблицу и исправь только нужные строки/колонки. "
                f"Комментарий методолога: {text}"
            )
        return text

    def _forbidden_changes(self, command_type: MethodologyAssistantCommandType, text: str = "") -> list[str]:
        if command_type == "approve":
            return []
        base = ["не менять соседние разделы"]
        if command_type in {"simplify_task", "fix_failed_criteria"} and not self._is_display_block_request(text):
            base.append("не добавлять готовые ответы")
        if command_type == "regenerate_section":
            base.append("не менять входные параметры проекта")
        return base

    def _expected_outcome(self, command_type: MethodologyAssistantCommandType) -> str:
        return {
            "approve": "",
            "request_changes": "Локальная правка сохранена без изменения соседних блоков.",
            "simplify_task": "Задача стала проще и атомарнее, но учебная цель сохранилась.",
            "add_example": "В блоке появился короткий пример без готового решения практики.",
            "fix_failed_criteria": "Непройденные критерии закрыты точечными правками.",
            "regenerate_section": "Выбранный раздел перегенерирован от ближайшего durable checkpoint.",
        }[command_type]

    def _confidence(self, command_type: MethodologyAssistantCommandType, target: SectionTarget | None) -> float:
        if command_type == "request_changes" and target is None:
            return 0.58
        if target is None:
            return 0.65
        return 0.86

    def _failed_issue_codes(self, context: MethodologyAssistantParseContext) -> list[str]:
        checkpoint = context.checkpoint or {}
        artifact = checkpoint.get("artifact") if isinstance(checkpoint.get("artifact"), dict) else {}
        matrix = artifact.get("requirements_matrix") if isinstance(artifact, dict) else None
        if not isinstance(matrix, list):
            matrix = context.review_state.get("requirements_matrix") if isinstance(context.review_state, dict) else []
        codes: list[str] = []
        for item in matrix if isinstance(matrix, list) else []:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or item.get("result") or "").lower()
            passed = item.get("passed")
            failed = passed is False or status in {"fail", "failed", "not_passed", "missing", "error", "warning"}
            code = item.get("id") or item.get("code") or item.get("criterion")
            if failed and code:
                codes.append(str(code))
        return codes

    def _bind_checkpoint(
        self,
        command: MethodologyAssistantCommand,
        context: MethodologyAssistantParseContext,
    ) -> MethodologyAssistantCommand:
        checkpoint = context.checkpoint or {}
        return command.model_copy(
            update={
                "checkpoint_id": str(checkpoint.get("id") or ""),
                "checkpoint_stage": self._normalize_stage(str(checkpoint.get("stage") or "")),
                "node_id": str(checkpoint.get("node_id") or checkpoint.get("resume_from_node") or ""),
                "workflow_node_id": self._workflow_node_for_stage(command.target_stage),
            }
        )

    def _should_try_pydantic_ai(self, message: str) -> bool:
        lowered = message.lower()
        return not (
            _APPROVE_RE.search(lowered)
            or _SIMPLIFY_RE.search(lowered)
            or _ADD_EXAMPLE_RE.search(lowered)
            or _FIX_FAILED_RE.search(lowered)
            or _REGENERATE_RE.search(lowered)
        )

    async def _parse_with_pydantic_ai(
        self,
        message: str,
        context: MethodologyAssistantParseContext,
    ) -> MethodologyAssistantCommand | None:
        try:
            from pydantic_ai import Agent
        except Exception:
            return None

        prompt = self._pydantic_ai_prompt(message, context)
        try:
            agent = Agent(
                self._pydantic_ai_model,
                output_type=MethodologyAssistantCommand,
                instructions=(
                    "Parse a Russian methodologist review chat message into a strict command. "
                    "Use only the provided command enum. Do not invent target ids."
                ),
            )
            result = await agent.run(prompt)
            output = result.output
            if not isinstance(output, MethodologyAssistantCommand):
                return None
            output = output.model_copy(update={"raw_text": message.strip(), "source": "pydantic_ai"})
            return self._bind_checkpoint(self._normalize_ai_command(output, context), context)
        except Exception:
            return None

    def _pydantic_ai_prompt(self, message: str, context: MethodologyAssistantParseContext) -> str:
        checkpoint = context.checkpoint or {}
        registry = self._registry(context)
        targets = [
            {
                "id": target.id,
                "label": target.label,
                "stage": target.stage,
                "selector": target.selector,
                "scope": target.scope,
            }
            for target in registry.targets[:40]
        ]
        return (
            "Message:\n"
            f"{message.strip()}\n\n"
            "Checkpoint:\n"
            f"{checkpoint}\n\n"
            "Available targets:\n"
            f"{targets}\n\n"
            "Commands: approve, request_changes, simplify_task, add_example, fix_failed_criteria, regenerate_section."
        )

    def _normalize_ai_command(
        self,
        command: MethodologyAssistantCommand,
        context: MethodologyAssistantParseContext,
    ) -> MethodologyAssistantCommand:
        registry = self._registry(context)
        target = registry.find(command.target_id) or registry.find(command.target_selector)
        if target is None:
            target = self._target_for_command(command.command, command.raw_text, context)
        issue_codes = command.issue_codes or (
            self._failed_issue_codes(context) if command.command == "fix_failed_criteria" else []
        )
        return command.model_copy(
            update={
                "target_stage": self._target_stage(target, context),
                "target_selector": self._selector_for_target(target, context) if target else command.target_selector[:300],
                "target_id": target.id if target else command.target_id,
                "scope": self._target_scope(target, command.command, command.raw_text),
                "instruction": command.instruction or self._instruction_for(command.command, command.raw_text, issue_codes),
                "issue_codes": issue_codes,
                "forbidden_changes": command.forbidden_changes or self._forbidden_changes(command.command, command.raw_text),
                "expected_outcome": command.expected_outcome or self._expected_outcome(command.command),
            }
        )

    @staticmethod
    def _is_display_block_request(text: str) -> bool:
        return bool(_DISPLAY_BLOCK_RE.search(text or ""))
