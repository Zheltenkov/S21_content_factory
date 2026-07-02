"""
Агент-критик практических задач.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..config.loader import get_agent_config, prompt_trace_kwargs
from .base.llm_client import LLMClientProtocol
from ..llm.structured_output import StructuredLLMClient
from ..models.schemas import ProjectSeed
from ..observability import FallbackTraceEvent


class PracticeIssue(BaseModel):
    """Проблема, найденная в практических задачах."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    task_index: int = Field(
        ge=0,
        le=20,
        description="Индекс задачи (начиная с 0)"
    )
    kind: str = Field(
        min_length=1,
        max_length=80,
        description="Тип проблемы: 'alignment', 'complexity', 'clarity', 'p2p_checkable', etc."
    )
    severity: Literal["critical", "error", "warning", "info"] = Field(
        description="Серьезность: 'critical', 'error', 'warning', 'info'"
    )
    message: str = Field(
        min_length=1,
        max_length=600,
        description="Описание проблемы"
    )
    suggestion: str = Field(
        default="",
        max_length=800,
        description="Предложение по исправлению"
    )

    def as_dict(self) -> dict:
        """Return a plain dict for API serialization."""
        return self.model_dump(exclude_none=True)


class PracticeCriticResponse(BaseModel):
    """Ответ от PracticeCriticAgent со списком проблем."""

    model_config = ConfigDict(extra="forbid", strict=True)

    issues: list[PracticeIssue] = Field(
        default_factory=list,
        max_length=20,
        description="Список найденных проблем в практических задачах"
    )


class PracticeCriticAgent:
    """LLM-агент, проверяющий практику на связь с теорией и p2p-проверяемость."""

    CONFIG_NAME = "practice_critic"

    def __init__(self, llm: LLMClientProtocol):
        self.llm = llm
        self.structured_client = StructuredLLMClient(llm)
        self.config = get_agent_config(self.CONFIG_NAME)
        self.llm_kwargs = self.config.llm.to_kwargs() if self.config.llm else {}
        options = self.config.options or {}
        self.max_issues = options.get("max_issues", 8)
        self.fallback_traces: list[dict] = []

    def consume_fallback_traces(self) -> list[dict]:
        """Return and clear fallback events collected during the last reviews."""
        events = list(self.fallback_traces)
        self.fallback_traces.clear()
        return events

    def review(
        self,
        seed: ProjectSeed,
        practice_markdown: str,
        theory_summary: str | None = None,
    ) -> list[PracticeIssue]:
        theory_summary = theory_summary or ""
        system_prompt = self.config.get_prompt("system").format(language=seed.language)
        user_prompt = self.config.get_prompt("user_template").format(
            project_description=seed.project_description,
            skills=", ".join(seed.skills) or "—",
            learning_outcomes=", ".join(seed.learning_outcomes) or "—",
            sjm=seed.sjm or "—",
            theory_summary=theory_summary or "—",
            practice_markdown=practice_markdown,
        )

        llm_kwargs = self.llm_kwargs.copy()
        llm_kwargs.setdefault("temperature", 0.0)
        llm_kwargs.update(
            prompt_trace_kwargs(
                self.config,
                "system",
                "user_template",
                output_schema="PracticeCriticResponse",
            )
        )

        try:
            # Используем structured output
            response = self.structured_client.complete_structured(
                output_model=PracticeCriticResponse,
                system=system_prompt,
                user=user_prompt,
                **llm_kwargs,
            )
            # Ограничиваем количество issues
            issues = self._suppress_false_positives(
                seed=seed,
                practice_markdown=practice_markdown,
                theory_summary=theory_summary,
                issues=response.issues[:self.max_issues],
            )
            issues = self._merge_sjm_alignment_issues(seed, practice_markdown, issues)
            return issues
        except Exception as e:
            # Recovery path for providers that reject structured output.
            import sys
            print(f"  ⚠️  Ошибка structured output, используем fallback: {e}", file=sys.stderr, flush=True)
            self.fallback_traces.append(
                FallbackTraceEvent.from_fallback(
                    node="practice",
                    fallback_type="practice_critic_json_object_recovery",
                    reason=str(e),
                    quality_risk="low",
                    inputs={
                        "title_seed": seed.title_seed,
                        "practice_chars": len(practice_markdown or ""),
                        "theory_summary_chars": len(theory_summary or ""),
                    },
                    trace={"max_issues": self.max_issues},
                    metadata={"agent": self.__class__.__name__},
                ).model_dump(mode="json")
            )
            response_text = self.llm.complete(
                system=system_prompt,
                user=user_prompt,
                response_format="json_object",
                **llm_kwargs,
            )
            issues = self._suppress_false_positives(
                seed=seed,
                practice_markdown=practice_markdown,
                theory_summary=theory_summary,
                issues=self._parse_response_fallback(response_text),
            )
            return self._merge_sjm_alignment_issues(seed, practice_markdown, issues)

    @staticmethod
    def _split_tasks(practice_markdown: str) -> dict[int, str]:
        matches = list(re.finditer(r"^###\s+Задани(?:е|я)\s+(\d+)\.\s*(.+?)\s*$", practice_markdown or "", flags=re.M))
        blocks: dict[int, str] = {}
        if not matches:
            return blocks
        indices = [m.start() for m in matches] + [len(practice_markdown or "")]
        for idx, match in enumerate(matches):
            task_number = int(match.group(1))
            start = match.start()
            end = indices[idx + 1]
            blocks[task_number] = (practice_markdown or "")[start:end].strip()
        return blocks

    @staticmethod
    def _has_story_context(task_text: str) -> bool:
        action_match = re.search(r"\*\*Что нужно сделать:?\*\*\s*(.+?)(?=\n\*\*|\Z)", task_text or "", flags=re.S | re.I)
        action_text = action_match.group(1).strip() if action_match else ""
        situation_match = re.search(r"(?:^|\n)\s*Ситуация:\s*(.+?)(?=\n\s*(?:Исходные данные:|Цель:|Подход:)|\Z)", action_text, flags=re.S | re.I)
        text = situation_match.group(1).strip().lower() if situation_match else ""
        if not text:
            text = action_text.strip().lower()
        if not text:
            return False
        if len(text) < 25:
            return False
        actor_signals = ["ты", "команда", "заказчик", "клиент", "коллега", "ревьюер", "проект"]
        tension_signals = [
            "нужно", "необходимо", "проблем", "риск", "срок", "дедлайн", "ошиб", "конфликт",
            "неяс", "задерж", "не понима", "не хватает", "важно", "обсуждени", "согласован",
            "утверждени", "иначе", "зависит",
        ]
        return (any(signal in text for signal in actor_signals) and any(signal in text for signal in tension_signals)) or len(text) >= 60

    @staticmethod
    def _has_deterministic_p2p_signal(task_text: str) -> bool:
        criteria_match = re.search(
            r"\*\*(?:Что должно получиться|Критерии проверки.*?):?\*\*\s*\n(.*?)(?=\n\*\*|\n##|\n###|\Z)",
            task_text or "",
            flags=re.S | re.I,
        )
        if not criteria_match:
            return False
        criteria_block = criteria_match.group(1)
        checklist_items = [
            re.sub(r"^[-*]\s*\[[ x]?\]\s*", "", line.strip())
            for line in criteria_block.splitlines()
            if re.match(r"^\s*[-*]\s*(?:\[[ x]?\]\s*)?.+", line)
        ]
        checklist_items = [item for item in checklist_items if len(item) >= 10]
        if len(checklist_items) < 3:
            return False
        observable_signals = [
            "содержит", "указан", "указаны", "есть", "присутствует", "присутствуют",
            "размещен", "размещён", "путь", "файл", "раздел", "схема", "таблица",
            "перечислен", "перечислены", "учтен", "учтены", "зафиксирован", "зафиксированы",
            "оформлен", "оформлена", "по указанному пути", "в документе", "в таблице", "на схеме", "минимум",
        ]
        observable_items = [
            item for item in checklist_items
            if any(signal in item.lower() for signal in observable_signals)
        ]
        has_location = bool(re.search(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+\.[A-Za-z0-9]+", task_text or ""))
        return len(observable_items) >= 2 and has_location

    @staticmethod
    def _extract_theory_phrases(theory_summary: str) -> list[str]:
        phrases: list[str] = []
        for raw_line in (theory_summary or "").splitlines():
            line = raw_line.strip(" -\t")
            line = re.sub(r"^\d+\.\s*", "", line).strip()
            if len(line) < 6:
                continue
            if line.lower().startswith("ключевые темы") or line.lower().startswith("ключевые понятия"):
                continue
            phrases.append(line.lower())
        dedup: list[str] = []
        for phrase in phrases:
            if phrase not in dedup:
                dedup.append(phrase)
        return dedup[:12]

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        stop_words = {
            "это", "для", "как", "что", "или", "при", "над", "под", "про", "без", "его", "ее",
            "её", "они", "она", "оно", "так", "уже", "ещё", "этот", "эта", "эти", "тот",
            "который", "которая", "которые", "если", "только", "будет", "задача", "проект",
            "нужно", "важно", "часть",
        }
        tokens = re.findall(r"[А-Яа-яЁёA-Za-z0-9]+", (text or "").lower())
        return {token for token in tokens if len(token) > 3 and token not in stop_words}

    @classmethod
    def _has_theory_grounding(cls, task_text: str, theory_summary: str) -> bool:
        task_low = (task_text or "").lower()
        phrases = cls._extract_theory_phrases(theory_summary)
        if not phrases:
            return False
        if any(phrase in task_low for phrase in phrases):
            return True
        task_tokens = cls._tokenize(task_text)
        for phrase in phrases:
            phrase_tokens = cls._tokenize(phrase)
            if len(task_tokens & phrase_tokens) >= 2:
                return True
        return False

    @classmethod
    def _suppress_false_positives(
        cls,
        seed: ProjectSeed,
        practice_markdown: str,
        theory_summary: str,
        issues: list[PracticeIssue],
    ) -> list[PracticeIssue]:
        if not issues:
            return issues

        task_blocks = cls._split_tasks(practice_markdown)
        all_tasks_have_story = bool(task_blocks) and all(cls._has_story_context(block) for block in task_blocks.values())
        filtered: list[PracticeIssue] = []

        for issue in issues:
            kind = (issue.kind or "").strip().lower()
            task_idx = int(issue.task_index or 0)
            task_text = task_blocks.get(task_idx, "")

            if kind == "sjm_alignment" and not (seed.sjm or "").strip():
                continue

            if kind == "story_alignment":
                if task_idx <= 0 and all_tasks_have_story:
                    continue
                if task_text and cls._has_story_context(task_text):
                    continue

            if kind == "p2p_check" and task_text and cls._has_deterministic_p2p_signal(task_text):
                continue

            if kind == "theory_alignment" and task_text and cls._has_theory_grounding(task_text, theory_summary):
                continue

            filtered.append(issue)

        return filtered[: len(issues)]

    def _extract_sjm_anchors(self, sjm_text: str) -> list[str]:
        """Извлекает якоря из SJM (роль, бренд, числа, ключевые ограничения)."""
        import re

        if not sjm_text:
            return []

        text = sjm_text.strip()
        text_low = text.lower()
        anchors: list[str] = []

        role_match = re.search(r"ты\s+—\s+([^\.!\n]+)", text_low)
        if role_match:
            role_anchor = role_match.group(1).strip(" ,")
            if role_anchor:
                anchors.append(role_anchor)

        # Числовые ограничения (например, "2 недели", "30 минут")
        for m in re.finditer(r"\b\d+\s*(?:минут[аы]?|час(?:а|ов)?|дн(?:я|ей)?|недел[ьяи]?|месяц(?:а|ев)?)\b", text_low):
            anchors.append(m.group(0))

        # Ключевые переговорные ограничения/outcome
        keyword_patterns = [
            r"бюджет",
            r"релиз",
            r"срок",
            r"возражен",
            r"договор[её]н",
            r"реалистичн(?:ый|ого)\s+план",
            r"заказчик",
        ]
        for pattern in keyword_patterns:
            m = re.search(pattern, text_low)
            if m:
                anchors.append(m.group(0))

        # Капитализированные сущности (например, Домклик)
        for token in re.findall(r"\b[А-ЯA-Z][А-Яа-яA-Za-z0-9_-]{2,}\b", text):
            if token.lower() not in {"ты"}:
                anchors.append(token.lower())

        # Уникализация с сохранением порядка
        dedup: list[str] = []
        for a in anchors:
            a = a.strip()
            if a and a not in dedup:
                dedup.append(a)
        return dedup[:10]

    def _merge_sjm_alignment_issues(
        self,
        seed: ProjectSeed,
        practice_markdown: str,
        existing_issues: list[PracticeIssue],
    ) -> list[PracticeIssue]:
        """
        Добавляет детерминированную проверку sjm_alignment, чтобы не терять кейс.
        """
        sjm_text = (seed.sjm or "").strip()
        if not sjm_text:
            return existing_issues[: self.max_issues]

        anchors = self._extract_sjm_anchors(sjm_text)
        if not anchors:
            return existing_issues[: self.max_issues]

        md_low = (practice_markdown or "").lower()
        matched = sum(1 for a in anchors if a in md_low)

        # Требуем хотя бы часть якорей в практике (минимум 3 или половину, что меньше)
        required = min(3, max(1, len(anchors) // 2))
        if matched >= required:
            return existing_issues[: self.max_issues]

        # Не дублируем, если LLM уже сообщил про sjm_alignment
        if any((iss.kind or "").strip().lower() == "sjm_alignment" for iss in existing_issues):
            return existing_issues[: self.max_issues]

        missing = [a for a in anchors if a not in md_low][:4]
        issue = PracticeIssue(
            task_index=1,
            kind="sjm_alignment",
            severity="critical",
            message="Практические задачи слабо отражают SJM-кейс: потеряна конкретика роли/ограничений/outcome.",
            suggestion=f"Верни в формулировки задач якоря кейса: {', '.join(missing)}.",
        )
        return (existing_issues + [issue])[: self.max_issues]

    def _parse_response_fallback(self, response: str) -> list[PracticeIssue]:
        """Parse a raw JSON response when structured output is unavailable."""
        issues: list[PracticeIssue] = []
        try:
            payload = self._extract_json(response)
        except json.JSONDecodeError:
            return issues

        raw_issues = payload.get("issues", [])
        if not isinstance(raw_issues, list):
            return issues

        for item in raw_issues[: self.max_issues]:
            if not isinstance(item, dict):
                continue
            try:
                issues.append(
                    PracticeIssue(
                        task_index=int(item.get("task_index") or 0),
                        kind=item.get("kind") or "unknown",
                        severity=item.get("severity") or "warning",
                        message=item.get("message") or "",
                        suggestion=item.get("suggestion") or "",
                    )
                )
            except Exception:
                continue
        return issues

    @staticmethod
    def _extract_json(text: str) -> dict:
        """Извлекает JSON из текста (fallback метод)."""
        stripped = text.strip()
        start = stripped.find("{")
        end = stripped.rfind("}") + 1
        if start != -1 and end > start:
            stripped = stripped[start:end]
        return json.loads(stripped)
