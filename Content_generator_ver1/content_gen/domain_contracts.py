"""Explicit domain contracts and context policies for project generation."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field


_WORD_RE = re.compile(r"[А-Яа-яЁёA-Za-z0-9]+")

_STOP_WORDS = {
    "это",
    "для",
    "как",
    "что",
    "или",
    "при",
    "над",
    "под",
    "про",
    "без",
    "его",
    "ее",
    "её",
    "они",
    "она",
    "оно",
    "так",
    "уже",
    "ещё",
    "этот",
    "эта",
    "эти",
    "тот",
    "если",
    "только",
    "будет",
    "проект",
    "проекта",
    "проекте",
    "студент",
    "студента",
    "нужно",
    "важно",
}


STATIC_INSTRUCTION_MARKER_GROUPS: dict[str, re.Pattern[str]] = {
    "repository": re.compile(r"(?i)\b(репозитор\w*|gitlab|github|repo\b)\b"),
    "p2p": re.compile(r"(?i)\b(p2p|peer[- ]?to[- ]?peer|пир[- ]?ту[- ]?пир)\b"),
    "check_environment": re.compile(
        r"(?i)\b(сред[аые]\s+проверки|итогов\w+\s+провер\w+|правил[ао]\s+сдачи|чек[- ]?лист\s+сдачи)\b"
    ),
}


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_text(value)
        key = cleaned.lower()
        if cleaned and key not in seen:
            result.append(cleaned)
            seen.add(key)
    return result


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return _unique([str(item) for item in value if str(item or "").strip()])
    if isinstance(value, str) and value.strip():
        return _unique([part.strip() for part in re.split(r"[;\n]", value) if part.strip()])
    return []


def _sentences(text: str) -> list[str]:
    chunks = re.split(r"(?<=[.!?])\s+", _clean_text(text))
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def semantic_tokens(text: str) -> set[str]:
    """Normalize text into coarse tokens for deterministic semantic overlap checks."""
    tokens: set[str] = set()
    for raw_token in _WORD_RE.findall((text or "").lower()):
        if len(raw_token) <= 3 or raw_token in _STOP_WORDS:
            continue
        token = raw_token
        for suffix in (
            "иями",
            "ями",
            "ами",
            "ого",
            "ему",
            "ими",
            "ыми",
            "ое",
            "ая",
            "ые",
            "ый",
            "ий",
            "ой",
            "ов",
            "ев",
            "ах",
            "ях",
            "ам",
            "ям",
            "ом",
            "ем",
            "а",
            "я",
            "ы",
            "и",
            "е",
        ):
            if len(token) > 6 and token.endswith(suffix):
                token = token[: -len(suffix)]
                break
        tokens.add(token)
    return tokens


def semantic_overlap_ratio(candidate: str, reference: str) -> float:
    """Estimate meaning overlap without embeddings, using normalized token coverage."""
    candidate_tokens = semantic_tokens(candidate)
    reference_tokens = semantic_tokens(reference)
    if not candidate_tokens or not reference_tokens:
        return 0.0
    intersection = candidate_tokens & reference_tokens
    candidate_coverage = len(intersection) / max(1, len(candidate_tokens))
    reference_coverage = len(intersection) / max(1, len(reference_tokens))
    jaccard = len(intersection) / max(1, len(candidate_tokens | reference_tokens))
    return max(candidate_coverage, reference_coverage * 0.85, jaccard)


class NarrativeContract(BaseModel):
    """Stable narrative contract shared by theory, practice and methodology checks."""

    storytelling_type: str = "sjm"
    student_role: str = ""
    working_case: str = ""
    product_or_project: str = ""
    constraints: list[str] = Field(default_factory=list)
    data_sources: list[str] = Field(default_factory=list)
    artifact_chain: list[str] = Field(default_factory=list)
    source: str = "generated"

    @property
    def is_actionable(self) -> bool:
        """Check whether the contract is concrete enough for downstream generation."""
        return bool(
            self.student_role.strip()
            and self.working_case.strip()
            and self.product_or_project.strip()
            and self.data_sources
            and len(self.artifact_chain) >= 2
        )

    def to_prompt_context(self) -> str:
        """Render a compact prompt-safe narrative section."""
        lines = [
            "NARRATIVE CONTRACT",
            f"- Тип сторителлинга: {self.storytelling_type or 'sjm'}",
            f"- Роль студента: {self.student_role or 'не задана'}",
            f"- Рабочий кейс: {self.working_case or 'не задан'}",
            f"- Продукт/проект: {self.product_or_project or 'не задан'}",
        ]
        if self.constraints:
            lines.append(f"- Ограничения: {'; '.join(self.constraints[:5])}")
        if self.data_sources:
            lines.append(f"- Источники данных: {'; '.join(self.data_sources[:5])}")
        if self.artifact_chain:
            lines.append(f"- Цепочка артефактов: {' -> '.join(self.artifact_chain[:6])}")
        return "\n".join(lines)


class SectionContextPolicy(BaseModel):
    """Defines which context types may enter a generation section."""

    section: str
    allowed_contexts: list[str] = Field(default_factory=list)
    static_instruction_keys: list[str] = Field(default_factory=list)
    forbidden_markers: list[str] = Field(default_factory=list)
    allows_static_instruction: bool = False

    @classmethod
    def for_theory(cls) -> "SectionContextPolicy":
        """Theory may consume curriculum/narrative context, but not submission instructions."""
        return cls(
            section="theory",
            allowed_contexts=[
                "curriculum_context",
                "narrative_contract",
                "story_map_contract",
                "practice_plan_contract",
                "artifact_chain_plan",
                "sjm_context",
                "storytelling_type",
                "learning_outcomes",
                "skills",
                "required_tools",
                "project_description",
                "context_summary",
                "narrative_anchor",
            ],
            static_instruction_keys=[
                "static_instruction_context",
                "instruction_text",
                "intro_instruction",
                "p2p_instruction",
                "repo_instruction",
                "submission_rules",
            ],
            forbidden_markers=list(STATIC_INSTRUCTION_MARKER_GROUPS),
            allows_static_instruction=False,
        )

    @classmethod
    def for_practice(cls) -> "SectionContextPolicy":
        """Practice may see instruction context because it must avoid duplicating it."""
        return cls(
            section="practice",
            allowed_contexts=[
                "curriculum_context",
                "narrative_contract",
                "story_map_contract",
                "practice_plan_contract",
                "instruction_text",
                "theory_summary",
                "artifact_chain_plan",
                "storytelling_type",
                "learning_outcomes",
                "skills",
                "required_tools",
                "project_description",
            ],
            static_instruction_keys=[],
            forbidden_markers=[],
            allows_static_instruction=True,
        )

    @classmethod
    def for_dataset(cls) -> "SectionContextPolicy":
        """Dataset generation receives raw-evidence specs, but not final markdown or static instructions."""
        return cls(
            section="dataset",
            allowed_contexts=[
                "curriculum_context",
                "narrative_contract",
                "story_map_contract",
                "practice_plan_contract",
                "artifact_chain_plan",
                "evidence_specs",
                "practice_tasks",
                "learning_outcomes",
                "skills",
                "required_tools",
                "project_description",
            ],
            static_instruction_keys=[
                "static_instruction_context",
                "instruction_text",
                "intro_instruction",
                "p2p_instruction",
                "repo_instruction",
                "submission_rules",
                "markdown",
            ],
            forbidden_markers=list(STATIC_INSTRUCTION_MARKER_GROUPS),
            allows_static_instruction=False,
        )

    @classmethod
    def for_finalize(cls) -> "SectionContextPolicy":
        """Finalize may assemble generated artifacts, but should expose only report-safe context."""
        return cls(
            section="finalize",
            allowed_contexts=[
                "curriculum_context",
                "narrative_contract",
                "story_map_contract",
                "practice_plan_contract",
                "artifact_chain_plan",
                "evidence_specs",
                "dataset_files",
                "theory_parts",
                "practice_tasks",
                "learning_outcomes",
                "skills",
                "required_tools",
                "project_description",
                "rubric_json",
                "warnings",
                "issues",
            ],
            static_instruction_keys=[],
            forbidden_markers=[],
            allows_static_instruction=True,
        )

    def filter_context_payload(self, payload: dict[str, Any], *, topic_text: str = "") -> dict[str, Any]:
        """Return only whitelisted context fields after applying section text filters."""
        filtered: dict[str, Any] = {}
        for key in self.allowed_contexts:
            if key not in payload:
                continue
            filtered[key] = self.filter_context_value(key, payload[key], topic_text=topic_text)
        return filtered

    def find_forbidden_markers(self, text: str, *, topic_text: str = "") -> list[str]:
        """Find static-instruction markers that are not part of the project topic."""
        if self.allows_static_instruction:
            return []
        found: list[str] = []
        for group, pattern in STATIC_INSTRUCTION_MARKER_GROUPS.items():
            if pattern.search(text or "") and not pattern.search(topic_text or ""):
                found.append(group)
        return found

    def strip_forbidden_sentences(self, text: str, *, topic_text: str = "") -> str:
        """Remove sentences that leak static instruction context into this section."""
        if self.allows_static_instruction or not text:
            return text
        kept = [
            sentence
            for sentence in _sentences(text)
            if not self.find_forbidden_markers(sentence, topic_text=topic_text)
        ]
        return " ".join(kept).strip()

    def filter_context_value(self, key: str, value: Any, *, topic_text: str = "") -> Any:
        """Filter one context value according to the section policy."""
        if not self.allows_static_instruction and key in self.static_instruction_keys:
            return ""
        if isinstance(value, str):
            return self.strip_forbidden_sentences(value, topic_text=topic_text)
        if isinstance(value, list):
            return [self._filter_nested(item, topic_text=topic_text) for item in value]
        if isinstance(value, dict):
            return {str(k): self._filter_nested(v, topic_text=topic_text) for k, v in value.items()}
        return value

    def _filter_nested(self, value: Any, *, topic_text: str = "") -> Any:
        if isinstance(value, str):
            return self.strip_forbidden_sentences(value, topic_text=topic_text)
        if isinstance(value, list):
            return [self._filter_nested(item, topic_text=topic_text) for item in value]
        if isinstance(value, dict):
            return {str(k): self._filter_nested(v, topic_text=topic_text) for k, v in value.items()}
        return value


class StaticInstructionLeakGuard:
    """Guard for accidental Chapter 2 leaks from static submission instructions."""

    def __init__(self, policy: SectionContextPolicy | None = None) -> None:
        self.policy = policy or SectionContextPolicy.for_theory()

    def find_leaks(self, text: str, *, topic_text: str = "") -> list[str]:
        return self.policy.find_forbidden_markers(text, topic_text=topic_text)

    def strip(self, text: str, *, topic_text: str = "") -> str:
        return self.policy.strip_forbidden_sentences(text, topic_text=topic_text)


class LearningActivityIssue(BaseModel):
    """One deterministic learning-activity contract violation."""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class LearningActivityContract(BaseModel):
    """Practice contract: inputs are raw evidence and tasks chain through artifacts."""

    require_raw_evidence: bool = True
    require_artifact_chain: bool = True

    def check_task(self, task: Any, *, task_index: int, previous_task: Any | None = None) -> list[LearningActivityIssue]:
        from .practice_contract import find_non_raw_material_issues, task_uses_previous_artifact

        issues: list[LearningActivityIssue] = []
        input_data = str(getattr(task, "input_data", "") or "")
        if self.require_raw_evidence:
            material_issues = find_non_raw_material_issues(input_data)
            if material_issues:
                issues.append(
                    LearningActivityIssue(
                        code="practice.non_raw_input_materials",
                        message="Practice input materials must be raw evidence, not classified learning drafts.",
                        details={"task": task_index, "material_issues": material_issues},
                    )
                )

        if self.require_artifact_chain and previous_task is not None:
            previous_location = str(getattr(previous_task, "artifact_location", "") or "")
            if previous_location and not task_uses_previous_artifact(task, previous_task):
                issues.append(
                    LearningActivityIssue(
                        code="practice.task_dependency_missing",
                        message="Task does not consume the artifact produced by the previous task.",
                        details={"task": task_index, "previous_artifact": previous_location},
                    )
                )
        return issues


def build_narrative_contract(
    seed: Any,
    curriculum_ctx: dict[str, Any] | None = None,
    previous_projects: list[dict[str, Any]] | None = None,
) -> NarrativeContract:
    """Derive a narrative contract from seed, curriculum context and SJM."""
    ctx = curriculum_ctx or {}
    storytelling_type = _clean_text(getattr(seed, "storytelling_type", None) or ctx.get("storytelling_type") or "sjm")
    story = "" if storytelling_type == "none" else _clean_text(getattr(seed, "sjm", None) or ctx.get("sjm_context"))
    description = _clean_text(ctx.get("current_project_description") or getattr(seed, "project_description", ""))
    product = _clean_text(
        getattr(seed, "platform_name", None)
        or getattr(seed, "title_seed", None)
        or ctx.get("current_project_title")
        or description[:120]
    )
    working_case = story or description or f"Рабочая ситуация вокруг проекта «{product}»."

    role = _extract_student_role(story, getattr(seed, "direction", "") or getattr(seed, "thematic_block", ""))
    constraints = _extract_constraints(" ".join([story, description]))
    data_sources = _derive_data_sources(seed, ctx, previous_projects or [])
    artifact_chain = _derive_artifact_chain(product, getattr(seed, "required_tools", []) or [])

    return NarrativeContract(
        storytelling_type=storytelling_type,
        student_role=role,
        working_case=working_case,
        product_or_project=product,
        constraints=constraints,
        data_sources=data_sources,
        artifact_chain=artifact_chain,
        source="curriculum_sjm" if story else "curriculum_generated",
    )


def render_narrative_contract_section(contract: NarrativeContract | dict[str, Any] | None) -> str:
    """Render a narrative contract from either typed or serialized form."""
    if not contract:
        return ""
    if isinstance(contract, NarrativeContract):
        return contract.to_prompt_context()
    if isinstance(contract, dict):
        return NarrativeContract(**contract).to_prompt_context()
    return ""


def _extract_student_role(story: str, direction: str) -> str:
    match = re.search(
        r"\bты\s*(?:—|-|:|будешь|выступаешь|работаешь\s+как|работаешь\s+в\s+роли|в\s+роли)\s*([^.;\n]+)",
        story or "",
        flags=re.I,
    )
    if match:
        return _clean_text(match.group(1))[:160]

    normalized_direction = (direction or "").upper()
    if normalized_direction in {"PJM", "PM", "PROJECT MANAGER"}:
        return "project manager / координатор проекта"
    if normalized_direction in {"BSA", "BA"}:
        return "бизнес-аналитик"
    if normalized_direction in {"QA", "TESTING"}:
        return "QA-инженер"
    if normalized_direction in {"DS", "ML", "DATA"}:
        return "data specialist"
    return "участник проекта в рабочей роли"


def _extract_constraints(text: str) -> list[str]:
    signals = (
        "срок",
        "дедлайн",
        "бюджет",
        "огранич",
        "ресурс",
        "требован",
        "команд",
        "заказчик",
        "клиент",
        "риск",
        "таймбокс",
    )
    return _unique([
        sentence
        for sentence in _sentences(text)
        if any(signal in sentence.lower() for signal in signals)
    ])[:5]


def _derive_data_sources(seed: Any, ctx: dict[str, Any], previous_projects: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    values.extend(_as_list(getattr(seed, "additional_materials", None)))
    values.extend(_as_list(ctx.get("additional_materials") or ctx.get("current_project_materials")))
    story_text = _clean_text(getattr(seed, "sjm", None) or ctx.get("sjm_context"))
    values.extend(re.findall(r"`?(materials/[A-Za-z0-9_.-]+\.[A-Za-z0-9]+)`?", story_text))
    if previous_projects:
        values.append("контекст предыдущих проектов блока")
    if not values:
        values.append("сырые заметки, переписка, требования или наблюдения из рабочего кейса")
    return _unique(values)[:5]


def _derive_artifact_chain(product: str, required_tools: list[str]) -> list[str]:
    chain = ["сырой контекст кейса", "промежуточные артефакты практических задач"]
    if required_tools:
        chain.append(f"оформление через инструменты: {', '.join(required_tools[:3])}")
    chain.append(f"итоговый артефакт проекта «{product or 'project'}»")
    return chain
