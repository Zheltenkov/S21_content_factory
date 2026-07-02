"""Generic artifact-chain planning for practice tasks and raw evidence files."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from .models.schemas import PracticeTask, ProjectSeed
from .practice_contract import extract_material_refs, raw_material_path_for_task, task_uses_previous_artifact


def is_generic_repo_path_template(template: str | None) -> bool:
    """Return True for placeholder repo paths that do not name the actual project."""
    normalized = (template or "").replace("\\", "/").strip().lstrip("./").lower()
    return normalized.startswith("repo/")


class EvidenceSpec(BaseModel):
    """Contract for one generated file under materials/."""

    path: str
    evidence_type: str
    contains: list[str] = Field(default_factory=list)
    excludes: list[str] = Field(default_factory=list)
    student_must_derive: list[str] = Field(default_factory=list)
    source_task_index: int | None = None

    def to_prompt_context(self) -> str:
        """Render a compact evidence contract for dataset generation prompts."""
        lines = [
            f"- Файл: `{self.path}`",
            f"  - Тип evidence: {self.evidence_type}",
        ]
        if self.contains:
            lines.append(f"  - Содержит: {'; '.join(self.contains)}")
        if self.excludes:
            lines.append(f"  - Не содержит: {'; '.join(self.excludes)}")
        if self.student_must_derive:
            lines.append(f"  - Студент должен вывести сам: {'; '.join(self.student_must_derive)}")
        return "\n".join(lines)


class ArtifactStep(BaseModel):
    """One task-level transition in the practice artifact chain."""

    task_index: int
    input_refs: list[str] = Field(default_factory=list)
    artifact_location: str
    artifact_kind: str
    depends_on: str | None = None

    def to_prompt_line(self) -> str:
        """Render a deterministic task transition."""
        inputs = ", ".join(f"`{ref}`" for ref in self.input_refs) if self.input_refs else "контекст задачи"
        dependency = f"; зависит от `{self.depends_on}`" if self.depends_on else ""
        return (
            f"{self.task_index}. input: {inputs}{dependency} -> "
            f"artifact: `{self.artifact_location}` ({self.artifact_kind})"
        )


class ArtifactChainPlan(BaseModel):
    """Practice-wide raw-input -> task artifacts contract."""

    raw_input_path: str
    steps: list[ArtifactStep] = Field(default_factory=list)
    evidence_specs: list[EvidenceSpec] = Field(default_factory=list)

    def to_prompt_context(self) -> str:
        """Render the chain for prompts."""
        lines = [
            "ARTIFACT CHAIN CONTRACT",
            f"- Единственный исходный raw input для первой задачи: `{self.raw_input_path}`",
            "- Каждая следующая задача должна использовать артефакт предыдущей задачи как вход.",
            "- Файлы в materials/ являются raw evidence: в них нет готового итогового артефакта студента.",
            "",
            "Цепочка задач:",
        ]
        lines.extend(step.to_prompt_line() for step in self.steps)
        if self.evidence_specs:
            lines.extend(["", "EvidenceSpec для materials/:"])
            lines.extend(spec.to_prompt_context() for spec in self.evidence_specs)
        return "\n".join(lines)

    def evidence_by_path(self) -> dict[str, EvidenceSpec]:
        """Return specs indexed by full path and basename."""
        indexed: dict[str, EvidenceSpec] = {}
        for spec in self.evidence_specs:
            path = spec.path.replace("\\", "/")
            indexed[path.lower()] = spec
            indexed[path.split("/")[-1].lower()] = spec
        return indexed


class GenericArtifactChainPlanner:
    """Build and enforce a generic artifact chain for any project topic."""

    RAW_EVIDENCE_EXCLUDES = [
        "готовый итоговый ответ",
        "заполненный артефакт студента",
        "классифицированную таблицу или матрицу",
        "готовый план, отчет, рекомендации или стратегию",
        "оценку качества выполненной студентом работы",
    ]

    def plan(self, seed: ProjectSeed, task_count: int, *, theory_summary: str = "") -> ArtifactChainPlan:
        """Create a deterministic chain before LLM practice generation."""
        raw_input_path = raw_material_path_for_task(1)
        steps: list[ArtifactStep] = []
        previous_artifact: str | None = None
        for task_index in range(1, max(1, task_count) + 1):
            artifact_location = self._artifact_path(seed, task_index)
            input_refs = [raw_input_path] if task_index == 1 else []
            if previous_artifact:
                input_refs.append(previous_artifact)
            steps.append(
                ArtifactStep(
                    task_index=task_index,
                    input_refs=input_refs,
                    artifact_location=artifact_location,
                    artifact_kind=self._artifact_kind(seed, task_index, theory_summary),
                    depends_on=previous_artifact,
                )
            )
            previous_artifact = artifact_location

        return ArtifactChainPlan(
            raw_input_path=raw_input_path,
            steps=steps,
            evidence_specs=[
                self._build_evidence_spec(
                    path=raw_input_path,
                    task_index=1,
                    seed=seed,
                    goal_hint="первичный анализ рабочего кейса",
                    expected_hint="первый проверяемый артефакт",
                )
            ],
        )

    def apply(
        self,
        tasks: list[PracticeTask],
        seed: ProjectSeed,
        plan: ArtifactChainPlan,
    ) -> tuple[list[PracticeTask], ArtifactChainPlan]:
        """Apply the chain to generated tasks and refresh EvidenceSpec entries."""
        if not tasks:
            return tasks, plan

        spec_by_path: dict[str, EvidenceSpec] = {spec.path.lower(): spec for spec in plan.evidence_specs}
        for idx, task in enumerate(tasks, 1):
            step = plan.steps[idx - 1] if idx - 1 < len(plan.steps) else None
            if step and not (task.artifact_location or "").strip():
                task.artifact_location = step.artifact_location

            if idx == 1:
                self._ensure_first_raw_input(task, plan.raw_input_path)
            else:
                previous_task = tasks[idx - 2]
                previous_artifact = (previous_task.artifact_location or "").strip()
                if previous_artifact and not task_uses_previous_artifact(task, previous_task):
                    task.input_data = self._append_sentence(
                        task.input_data,
                        f"Результат предыдущей задачи — см. файл `{previous_artifact}`.",
                    )

            for ref in extract_material_refs(task.input_data):
                key = ref.lower()
                if key not in spec_by_path:
                    spec_by_path[key] = self._build_evidence_spec(
                        path=ref,
                        task_index=idx,
                        seed=seed,
                        goal_hint=task.goal,
                        expected_hint=task.expected_artifact,
                    )

        refreshed_steps = self._refresh_steps(plan, tasks)
        refreshed_specs = sorted(spec_by_path.values(), key=lambda spec: spec.path.lower())
        return tasks, ArtifactChainPlan(
            raw_input_path=plan.raw_input_path,
            steps=refreshed_steps,
            evidence_specs=refreshed_specs,
        )

    def _ensure_first_raw_input(self, task: PracticeTask, raw_input_path: str) -> None:
        input_data = task.input_data or ""
        if raw_input_path.lower() in input_data.lower():
            return
        task.input_data = self._append_sentence(
            input_data,
            f"Сырые исходные материалы рабочего кейса — см. файл `{raw_input_path}`.",
        )

    def _refresh_steps(self, plan: ArtifactChainPlan, tasks: list[PracticeTask]) -> list[ArtifactStep]:
        steps: list[ArtifactStep] = []
        for idx, task in enumerate(tasks, 1):
            previous_artifact = (tasks[idx - 2].artifact_location or "").strip() if idx > 1 else None
            input_refs = extract_material_refs(task.input_data)
            if previous_artifact and previous_artifact not in input_refs:
                input_refs.append(previous_artifact)
            planned = plan.steps[idx - 1] if idx - 1 < len(plan.steps) else None
            steps.append(
                ArtifactStep(
                    task_index=idx,
                    input_refs=input_refs,
                    artifact_location=(task.artifact_location or (planned.artifact_location if planned else "")).strip(),
                    artifact_kind=self._infer_artifact_kind(task, planned),
                    depends_on=previous_artifact or None,
                )
            )
        return steps

    def _build_evidence_spec(
        self,
        *,
        path: str,
        task_index: int,
        seed: ProjectSeed,
        goal_hint: str,
        expected_hint: str,
    ) -> EvidenceSpec:
        topic = self._short_topic(seed)
        return EvidenceSpec(
            path=path,
            evidence_type="raw_case_evidence",
            contains=[
                f"сырые наблюдения, факты, ограничения и фрагменты коммуникации по теме «{topic}»",
                "неоднородные записи, которые требуют анализа и интерпретации",
                "достаточно контекста для выполнения задачи без внешних данных",
            ],
            excludes=self.RAW_EVIDENCE_EXCLUDES,
            student_must_derive=[
                self._compact_hint(goal_hint) or "выводы, классификацию и решение по задаче",
                self._compact_hint(expected_hint) or "структуру и содержание итогового артефакта",
            ],
            source_task_index=task_index,
        )

    def _artifact_path(self, seed: ProjectSeed, task_index: int) -> str:
        if seed.repo_path_template and not is_generic_repo_path_template(seed.repo_path_template):
            try:
                return seed.repo_path_template.format(num=task_index).replace("\\", "/")
            except (KeyError, ValueError):
                pass
        root = self._safe_root(seed.platform_name or seed.title_seed or "project")
        return f"{root}/part-03/task-{task_index:02d}/README.md"

    @staticmethod
    def _artifact_kind(seed: ProjectSeed, task_index: int, theory_summary: str) -> str:
        if task_index == 1:
            return "первичный рабочий артефакт на основе raw evidence"
        if task_index == max(1, int(getattr(seed, "tasks_count", 0) or 0)):
            return "итоговый артефакт проекта"
        topics = re.findall(r"^\s*\d+\.\s+(.+?)\s*$", theory_summary or "", flags=re.M)
        if 0 <= task_index - 1 < len(topics):
            return f"промежуточный артефакт по теме «{topics[task_index - 1]}»"
        return "промежуточный проверяемый артефакт"

    @staticmethod
    def _infer_artifact_kind(task: PracticeTask, planned: ArtifactStep | None) -> str:
        expected = (task.expected_artifact or "").strip()
        if expected:
            expected = re.sub(r"`[^`]+`", "", expected)
            expected = re.sub(r"\s+", " ", expected).strip(" .")
            if expected:
                return expected[:160]
        return planned.artifact_kind if planned else "проверяемый артефакт задачи"

    @staticmethod
    def _append_sentence(text: str, sentence: str) -> str:
        text = (text or "").strip()
        if not text:
            return sentence
        if sentence.lower() in text.lower():
            return text
        separator = "\n" if "\n" in text else " "
        if not text.endswith((".", "!", "?")):
            text += "."
        return f"{text}{separator}{sentence}"

    @staticmethod
    def _safe_root(value: Any) -> str:
        root = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "project")).strip("_.-")
        return root or "project"

    @staticmethod
    def _short_topic(seed: ProjectSeed) -> str:
        value = seed.title_seed or seed.platform_name or seed.project_description or seed.thematic_block or "проект"
        return re.sub(r"\s+", " ", value).strip()[:120]

    @staticmethod
    def _compact_hint(text: str) -> str:
        cleaned = re.sub(r"`[^`]+`", "", text or "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
        return cleaned[:160]
