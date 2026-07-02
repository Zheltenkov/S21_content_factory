"""Practice phase executor with checks, critic repair, and dataset generation."""

import logging
import re
from typing import Any

from .config.loader import prompt_trace_kwargs
from .generation_runtime import GenerationRuntimeContainer
from .models.phase_results import PracticePhaseResult
from .models.readme_document import ReadmeDocument, ReadmeSection
from .models.schemas import PracticeTask, ProjectSeed
from .observability import record_runtime_fallback_traces
from .recovery import ModelOutputNormalizer
from .utils.text_analysis import extract_defined_terms
from .utils.markdown_helpers import extract_chapter_content
from .validators.practice_checks import PracticeChecks

logger = logging.getLogger("content_gen.practice_phase_executor")


def _apply_critic_suggestions(tasks: list[Any], critic_issues: list[Any]) -> None:
    """
    Больше не пишет внутренние подсказки CriticAgent в пользовательский README.

    Замечания критика уже сохраняются в issues/report_json и используются
    для локальной регенерации проблемных задач. Публиковать их в финальном
    артефакте для студента не нужно.
    """
    return


def _format_approach_bullet(text: str) -> str:
    """Форматирует пункт подхода, сохраняя вложенные блоки кода."""
    if not text:
        return "- —"

    normalized = text.strip("\n")
    if normalized.startswith("ℹ️"):
        return f"> 💡 {normalized.replace('ℹ️', '').strip()}"

    lines = normalized.splitlines()
    if not lines:
        return "- —"

    first_line = lines[0].strip()
    rest = lines[1:]

    def _indent(line: str) -> str:
        return f"  {line}" if line else "  "

    if first_line.startswith("```"):
        block = [first_line] + rest
        return "-\n" + "\n".join(_indent(ln) for ln in block)

    rendered = f"- {first_line}"
    if rest:
        rendered += "\n" + "\n".join(_indent(ln) for ln in rest)
    return rendered


def _clean_inline_text(value: Any) -> str:
    """Normalize one short public README fragment."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _task_location(task: Any) -> str:
    """Return the public artifact location, falling back to an embedded path."""
    location = _clean_inline_text(getattr(task, "artifact_location", "") or "")
    if location:
        return location
    expected = _clean_inline_text(getattr(task, "expected_artifact", "") or "")
    match = re.search(r"`([^`]+/[A-Za-z0-9_./{}:\-]+)`", expected)
    if match:
        return match.group(1).strip()
    return ""


def _strip_check_marker(value: str) -> str:
    return re.sub(r"^\s*[-*]?\s*\[[ xX]?\]\s*", "", value or "").strip(" .")


def _observable_results(task: Any) -> list[str]:
    """Build 2-5 observable outcomes for source-compliant public practice."""
    expected = _clean_inline_text(getattr(task, "expected_artifact", "") or "")
    location = _task_location(task)
    criteria = [
        _strip_check_marker(str(item))
        for item in (getattr(task, "p2p_criteria", []) or [])
        if str(item or "").strip()
    ]

    results: list[str] = []
    if expected and location:
        results.append(f"{expected.rstrip('.')} размещен по пути `{location}`.")
    elif expected:
        results.append(f"{expected.rstrip('.')}.")
    elif location:
        results.append(f"Рабочий артефакт размещен по пути `{location}`.")

    for criterion in criteria:
        if criterion and criterion not in results:
            results.append(f"{criterion.rstrip('.')}.")
        if len(results) >= 5:
            break

    if len(results) < 2 and location:
        results.append("Артефакт можно открыть и проверить без устных пояснений автора.")
    if len(results) < 2:
        results.append("Результат содержит явные критерии, по которым peer-review может принять или вернуть работу.")

    return results[:5]


def _submission_format(task: Any) -> str:
    """Describe what the student should show during p2p review."""
    location = _task_location(task)
    if location:
        return (
            f"На p2p-ревью покажи артефакт по пути `{location}` и сверь его с пунктами "
            "из блока «Что должно получиться»."
        )
    return "На p2p-ревью покажи итоговый артефакт и сверь его с пунктами из блока «Что должно получиться»."


def _transition_text(task: Any, next_task: Any | None, *, bonus: bool = False) -> str:
    """Connect the current assignment with the next public step."""
    if bonus:
        return "После бонусного задания проверь, что основной результат проекта остался целостным и не зависит от бонуса."
    if next_task is not None:
        next_title = _clean_inline_text(getattr(next_task, "title", "") or "следующее задание")
        return f"В следующем задании используй этот результат как входные данные для шага «{next_title}»."
    return (
        "На этом шаге практическая цепочка завершается: итог должен быть проверяемым "
        "без устных пояснений автора."
    )


def _public_task_title(task: Any, index: int, *, bonus: bool = False) -> str:
    """Build the public task heading title without Markdown syntax."""
    title_prefix = "Бонусное задание" if bonus else "Задание"
    title_suffix = "*" if bonus else ""
    return f"{title_prefix} {index}{title_suffix}. {task.title}"


def _public_task_body(task: Any, next_task: Any | None, *, bonus: bool = False) -> str:
    """Build the public task body using the source/PDF template."""
    approach_items = [
        _format_approach_bullet(item)
        for item in (getattr(task, "approach_bullets", []) or [])
        if str(item or "").strip()
    ]
    approach = "\n".join(approach_items).strip() or "- Сопоставь входные данные, цель и ожидаемый артефакт."

    situation = _clean_inline_text(getattr(task, "situation", "") or "")
    input_data = _clean_inline_text(getattr(task, "input_data", "") or "")
    goal = _clean_inline_text(getattr(task, "goal", "") or "")
    constraints = _clean_inline_text(getattr(task, "constraints_or_risk", "") or "")
    roles = ", ".join(getattr(task, "group_roles", []) or [])
    outcome_items = "\n".join(f"- [ ] {item}" for item in _observable_results(task))

    action_lines: list[str] = []
    if situation:
        action_lines.append(f"Ситуация: {situation}")
    if input_data:
        action_lines.append(f"Исходные данные: {input_data}")
    if goal:
        action_lines.append(f"Цель: {goal}")
    action_lines.extend(["Подход:", approach])

    chunks = [
        "**Что нужно сделать**",
        "",
        "\n\n".join(action_lines).strip(),
        "",
        "**Что должно получиться**",
        "",
        outcome_items,
    ]
    if constraints:
        chunks.extend(["", "**Ограничения и условия**", "", constraints])
    if roles:
        chunks.extend(["", "**Ограничения и условия**" if not constraints else "", "", f"Роли для группы: {roles}"])
    chunks.extend(
        [
            "",
            "**Формат сдачи**",
            "",
            _submission_format(task),
            "",
            "**Переход к следующему заданию**",
            "",
            _transition_text(task, next_task, bonus=bonus),
        ]
    )
    return "\n".join(part for part in chunks if part != "").strip() + "\n"


def _render_public_task_section(task: Any, index: int, next_task: Any | None, *, bonus: bool = False) -> ReadmeSection:
    """Render one practice task as a typed README section."""
    return ReadmeSection(
        title=_public_task_title(task, index, bonus=bonus),
        level=3,
        body=_public_task_body(task, next_task, bonus=bonus).strip(),
    )


def _render_practice_sections(tasks: list[Any]) -> list[ReadmeSection]:
    """Render practice tasks into typed README sections."""
    return [
        _render_public_task_section(task, i, tasks[i] if i < len(tasks) else None)
        for i, task in enumerate(tasks, 1)
    ]


def _render_practice_block(tasks: list[Any]) -> str:
    """Render practice tasks into the final markdown block."""
    return "\n\n".join(section.to_markdown() for section in _render_practice_sections(tasks))


def _render_bonus_sections(tasks: list[Any]) -> list[ReadmeSection]:
    """Render optional bonus tasks as typed README sections."""
    return [_render_public_task_section(task, i, None, bonus=True) for i, task in enumerate(tasks, 1)]


def _practice_chapter_title(language: str) -> str:
    """Return the canonical public title for Chapter 3 when a skeleton is incomplete."""
    normalized = (language or "ru").casefold().strip()
    if normalized == "en":
        return "Chapter 3. Practice block"
    return "Глава 3. Практический блок"


def _build_theory_summary(orchestrator, md: str, language: str) -> tuple[str, int, int]:
    """
    Строит конспект теории для практики.

    Сначала использует structured `theory_parts`, если они уже есть в state.
    Это надежнее и методологически правильнее, чем повторно парсить markdown.
    """
    structured_parts = list(getattr(orchestrator, "theory_parts", []) or [])
    titles: list[str] = []
    terms: list[str] = []
    seen_terms: set[str] = set()

    def collect_terms(text: str, limit_per_part: int = 3) -> None:
        for term in extract_defined_terms(text, language=language, limit=limit_per_part):
            normalized = term.lower()
            if normalized in seen_terms:
                continue
            seen_terms.add(normalized)
            terms.append(term)

    if structured_parts:
        for part in structured_parts:
            if not getattr(part, "title", "").strip():
                continue
            titles.append(part.title.strip())
            collect_terms(getattr(part, "body", "") or "")
    else:
        theory_match = re.search(r"##\s+Глава\s+2[^\n]*\n(.*?)(?=\n##\s+Глава\s+3|\Z)", md, re.S)
        if not theory_match:
            return "Глава 2 ещё не сгенерирована. Ориентируйся на описание и LO проекта.", 0, 0

        theory_text = theory_match.group(1).strip()
        theory_text = ModelOutputNormalizer().normalize_theory_markdown(theory_text).markdown
        part_matches = re.findall(
            r"###\s+2\.\d+\.\s+(.+?)\n(.*?)(?=^###\s+2\.\d+\.|\Z)",
            theory_text,
            re.M | re.S,
        )
        for title, body in part_matches:
            titles.append(title.strip())
            collect_terms(body)

    if not titles:
        return "Глава 2 ещё не сгенерирована. Ориентируйся на описание и LO проекта.", 0, 0

    summary_lines = ["КЛЮЧЕВЫЕ ТЕМЫ ИЗ ТЕОРИИ (Глава 2):"]
    for i, title in enumerate(titles, 1):
        summary_lines.append(f"{i}. {title}")

    if terms:
        summary_lines.append("")
        summary_lines.append("КЛЮЧЕВЫЕ ПОНЯТИЯ (используй в заданиях):")
        summary_lines.extend([f"  - {term}" for term in terms[:7]])

    return "\n".join(summary_lines), len(titles), len(terms)


class PracticePhaseExecutor:
    """Execute Phase 3 practice generation, checks, critic repair, and datasets."""

    def __init__(self, runtime: GenerationRuntimeContainer) -> None:
        self.runtime = runtime

    def execute(
        self,
        seed: ProjectSeed,
        markdown: str,
        generate_bonus: bool,
        practice_plan_contract: Any | None = None,
        artifact_chain_plan: Any | None = None,
        section_context: dict[str, Any] | None = None,
    ) -> PracticePhaseResult:
        """Run practice generation through explicit, testable sub-steps."""
        instruction_text, theory_summary = self.extract_instruction_and_theory_summary(markdown, seed)
        practice_res, artifact_chain_plan = self.generate_tasks(
            seed,
            instruction_text,
            theory_summary,
            practice_plan_contract=practice_plan_contract,
            artifact_chain_plan=artifact_chain_plan,
            section_context=section_context,
        )

        issues: list[Any] = []
        warnings: list[str] = []
        critic_issues, issues_for_regen, theory_extract_text = self.review_with_critic(
            practice_res,
            seed,
            markdown,
            theory_summary,
            warnings,
        )
        artifact_chain_plan = self.apply_artifact_chain(practice_res, seed, artifact_chain_plan, warnings)
        self.apply_critic_findings(
            practice_res,
            seed,
            critic_issues,
            issues_for_regen,
            theory_extract_text,
            issues,
            warnings,
        )
        self.validate_practice(practice_res, seed, issues)
        bonus_tasks = self.generate_bonus_tasks(seed, generate_bonus, section_context, issues, warnings)
        dataset_files = self.generate_dataset_files(practice_res, seed)
        readme_document, _ = self.render_practice_document(
            ReadmeDocument.from_markdown(markdown),
            practice_res.tasks,
            bonus_tasks,
            generate_bonus,
            seed,
        )
        markdown = readme_document.to_markdown()
        return PracticePhaseResult(
            markdown=markdown,
            readme_document=readme_document,
            practice_tasks=list(practice_res.tasks),
            bonus_tasks=list(bonus_tasks),
            issues=issues,
            warnings=warnings,
            artifact_chain_plan=artifact_chain_plan,
            evidence_specs=list(
                getattr(artifact_chain_plan, "evidence_specs", [])
                or getattr(self.runtime, "evidence_specs", [])
                or []
            ),
            dataset_files=list(dataset_files or []),
            practice_critic_issues=list(getattr(self.runtime, "practice_critic_issues", []) or []),
        )

    def extract_instruction_and_theory_summary(self, markdown: str, seed: ProjectSeed) -> tuple[str, str]:
        """Extract Chapter 1 instruction and Chapter 2 theory summary for practice generation."""
        logger.info("🔄 Phase 3 | PracticeAgent")
        instruction_text = ""
        try:
            _, instruction_text = self.runtime.intro._split_intro_instruction(markdown)
        except Exception:
            instruction_text = ""
        instruction_text = instruction_text[:1000]

        theory_summary, theory_parts_count, theory_terms_count = _build_theory_summary(
            self.runtime,
            markdown,
            seed.language,
        )
        if theory_parts_count:
            logger.info(f"📚 Извлечён конспект теории: {theory_parts_count} частей, {theory_terms_count} терминов")
        else:
            logger.warning("⚠️ Глава 2 не найдена в markdown для извлечения конспекта")
        return instruction_text, theory_summary

    def generate_tasks(
        self,
        seed: ProjectSeed,
        instruction_text: str,
        theory_summary: str,
        practice_plan_contract: Any | None = None,
        artifact_chain_plan: Any | None = None,
        section_context: dict[str, Any] | None = None,
    ) -> tuple[Any, Any]:
        """Generate initial practice tasks and synchronize artifact-chain runtime state."""
        practice_plan_contract = practice_plan_contract or getattr(self.runtime, "practice_plan_contract", None)
        artifact_chain_plan = artifact_chain_plan or getattr(self.runtime, "artifact_chain_plan", None)
        practice_res = self.runtime.practice.generate(
            seed,
            instruction_text=instruction_text,
            theory_summary=theory_summary,
            practice_plan_contract=practice_plan_contract,
            artifact_chain_plan=artifact_chain_plan,
            section_context=section_context,
        )
        self.runtime.practice_tasks = practice_res.tasks
        artifact_chain_plan = getattr(self.runtime.practice, "last_artifact_chain_plan", None)
        self.runtime.artifact_chain_plan = artifact_chain_plan
        self.runtime.evidence_specs = list(getattr(artifact_chain_plan, "evidence_specs", []) or [])
        return practice_res, artifact_chain_plan

    def review_with_critic(
        self,
        practice_res: Any,
        seed: ProjectSeed,
        markdown: str,
        theory_summary: str,
        warnings: list[str],
    ) -> tuple[list[Any], list[Any], str]:
        """Review practice tasks with critic and regenerate serious problematic tasks."""
        practice_block = _render_practice_block(practice_res.tasks)
        self.runtime.practice_critic_issues = []
        critic_issues: list[Any] = []
        issues_for_regen: list[Any] = []
        theory_extract_text = ""
        try:
            theory_extract = extract_chapter_content(markdown, 2, seed.language) or ("", "")
            theory_extract_text = theory_extract[1]
            critic_issues = self.runtime.practice_critic.review(
                seed=seed,
                practice_markdown=practice_block,
                theory_summary=theory_extract_text,
            )
            self._record_fallback_traces(self.runtime.practice_critic.consume_fallback_traces())
            self.runtime.practice_critic_issues = [issue.as_dict() for issue in critic_issues]
            issues_for_regen = self._critic_issues_for_regeneration(critic_issues)
            if issues_for_regen:
                self.regenerate_problem_tasks(practice_res, seed, theory_summary, issues_for_regen)
            _apply_critic_suggestions(practice_res.tasks, critic_issues)
        except Exception as critic_err:
            consume = getattr(self.runtime.practice_critic, "consume_fallback_traces", None)
            if callable(consume):
                self._record_fallback_traces(consume())
            warnings.append(f"⚠️ PracticeCritic: {critic_err}")
            self.runtime.practice_critic_issues = []
            critic_issues = []
        return critic_issues, issues_for_regen, theory_extract_text

    def _record_fallback_traces(self, events: list[dict[str, Any]]) -> None:
        """Store fallback events on runtime when the container supports it."""
        record_runtime_fallback_traces(self.runtime, events)

    @staticmethod
    def _critic_issues_for_regeneration(critic_issues: list[Any]) -> list[Any]:
        """Select critic issues that justify local task regeneration."""
        serious_severities = {"critical", "hard", "major", "error"}
        serious_kinds = {"p2p_check", "p2p_checkable", "theory_alignment", "sjm_alignment", "story_alignment"}
        return [
            issue
            for issue in critic_issues
            if (str(issue.kind or "").strip().lower() in serious_kinds)
            and (str(issue.severity or "").strip().lower() in serious_severities)
        ]

    def regenerate_problem_tasks(
        self,
        practice_res: Any,
        seed: ProjectSeed,
        theory_summary: str,
        issues_for_regen: list[Any],
    ) -> None:
        """Regenerate tasks with serious critic findings."""
        logger.warning(f"🔄 Обнаружено {len(issues_for_regen)} проблем качества, запускаю локальную регенерацию задач")
        tasks_to_regen = sorted({max(1, int(issue.task_index or 1)) for issue in issues_for_regen})

        for task_idx in tasks_to_regen:
            if task_idx < 1 or task_idx > len(practice_res.tasks):
                continue
            task = practice_res.tasks[task_idx - 1]
            logger.info(f"♻️ Регенерация задачи {task_idx}: {task.title}")
            task_issues = [issue for issue in issues_for_regen if issue.task_index == task_idx]
            issue_descriptions = "\n".join([f"- {issue.message}: {issue.suggestion}" for issue in task_issues])
            regen_prompt = self._build_regeneration_prompt(task, task_idx, issue_descriptions, theory_summary, seed)
            try:
                system_prompt = self.runtime.practice.config.get_prompt("system").format(language=seed.language)
                regen_kwargs = {"temperature": 0.3}
                regen_kwargs.update(
                    prompt_trace_kwargs(self.runtime.practice.config, "system", output_schema="PracticeTask")
                )
                regen_md = self.runtime.practice.llm.complete(
                    system=system_prompt,
                    user=regen_prompt,
                    **regen_kwargs,
                )
                if self._apply_regenerated_task(task, regen_md, seed, task_idx, theory_summary):
                    logger.info(f"✅ Задача {task_idx} успешно регенерирована")
                else:
                    logger.warning(f"⚠️ Не удалось извлечь части из регенерации задачи {task_idx}, оставляю оригинал")
            except Exception as regen_err:
                logger.warning(f"⚠️ Ошибка регенерации задачи {task_idx}: {regen_err}")

    @staticmethod
    def _build_regeneration_prompt(
        task: Any,
        task_idx: int,
        issue_descriptions: str,
        theory_summary: str,
        seed: ProjectSeed,
    ) -> str:
        """Build the local regeneration prompt for one problematic practice task."""
        return f"""Перегенерируй задачу {task_idx}, исправив критические проблемы.

ТЕКУЩАЯ ВЕРСИЯ ЗАДАЧИ:
Название: {task.title}
Ситуация: {getattr(task, 'situation', '') or 'не указана'}
Ограничение / риск: {getattr(task, 'constraints_or_risk', '') or 'не указано'}
Входные данные: {task.input_data}
Цель: {task.goal}
Подход: {'; '.join(task.approach_bullets[:3])}
Результат: {task.expected_artifact}
Критерии проверки (P2P): {'; '.join(task.p2p_criteria[:5]) if task.p2p_criteria else 'не указаны'}

КРИТИЧЕСКИЕ ПРОБЛЕМЫ:
{issue_descriptions}

КОНСПЕКТ ТЕОРИИ (используй эти темы):
{theory_summary[:800]}

SJM / кейс:
{seed.sjm or '—'}

ТРЕБОВАНИЯ К НОВОЙ ВЕРСИИ:
1. Задача должна СТРОГО соответствовать теории (использовать понятия и термины из конспекта)
2. Артефакт должен быть p2p-проверяемым (конкретный + локация + критерии)
3. Задача должна быть реалистичной и выполнимой
4. Не уходи в темы, которых нет в теории
5. Цель сформулируй в активной форме с явным глаголом действия
6. Если есть SJM, явно привяжи задачу к роли, контексту и ограничениям кейса
7. Обязательно добавь блок **Критерии проверки (P2P)** с 3-5 бинарными проверками
8. Обязательно добавь блок **Ситуация**, где есть рабочий контекст, проблема и смысл действия
9. Обязательно добавь блок **Ограничение / риск**, где видно цену ошибки, ограничение по сроку/ресурсам или ключевой выбор

Формат вывода - стандартный формат задачи:

### Задание {task_idx}. <новое название>

**Ситуация:** <кто действует, что произошло, в чём проблема>
**Ограничение / риск:** <что ограничивает решение или где цена ошибки>
**Входные данные:** <конкретные данные>
**Цель:** <активная форма>
**Подход:**
- <пункт 1>
- <пункт 2>
**Ожидаемый результат:** <артефакт + путь>

**Критерии проверки (P2P):**
- [ ] <критерий 1>
- [ ] <критерий 2>
- [ ] <критерий 3>
"""

    @staticmethod
    def _extract_regenerated_field(label: str, markdown: str) -> str:
        pattern = rf"\*\*{label}:\*\*\s*(.+?)(?=\n\*\*|\Z)"
        match = re.search(pattern, markdown, flags=re.S)
        return match.group(1).strip() if match else ""

    @classmethod
    def _extract_any_regenerated_field(cls, labels: list[str], markdown: str) -> str:
        for label in labels:
            value = cls._extract_regenerated_field(label, markdown)
            if value:
                return value
        return ""

    def _apply_regenerated_task(
        self,
        task: Any,
        regen_md: str,
        seed: ProjectSeed,
        task_idx: int,
        theory_summary: str,
    ) -> bool:
        """Parse regenerated task markdown and update the task object in place."""
        regen_md = ModelOutputNormalizer().normalize_practice_markdown(regen_md).markdown
        title_match = re.search(r"^###\s+Задани(?:е|я)\s+\d+\.\s*(.+?)\s*$", regen_md, flags=re.M)
        new_title = title_match.group(1).strip() if title_match else ""
        new_situation = self._extract_regenerated_field("Ситуация", regen_md)
        new_constraints = self._extract_any_regenerated_field(
            ["Ограничение / риск", "Ограничение/риск", "Ограничение", "Риск"],
            regen_md,
        )
        new_input = self._extract_regenerated_field("Входные данные", regen_md)
        new_goal = self._extract_regenerated_field("Цель", regen_md)
        new_approach = self._extract_regenerated_field("Подход", regen_md)
        new_result = self._extract_regenerated_field("Ожидаемый результат", regen_md)
        new_criteria = self.runtime.practice._parse_p2p_criteria(regen_md)

        if not (new_goal and new_result):
            return False

        task.title = new_title or task.title
        task.situation = self.runtime.practice._fix_task_situation(
            new_situation,
            new_input or task.input_data,
            new_goal,
            seed.language,
        )
        task.constraints_or_risk = self.runtime.practice._fix_task_risk(
            new_constraints,
            task.situation,
            new_goal,
            seed.language,
        )
        task.input_data = new_input or task.input_data
        task.goal = self.runtime.practice._fix_goal_active_form(new_goal, seed.language)
        fixed_result, fixed_location = self.runtime.practice._fix_result_artifact(
            new_result,
            seed,
            task_idx - 1,
        )
        task.expected_artifact = fixed_result
        if fixed_location:
            task.artifact_location = fixed_location

        new_bullets = self.runtime.practice._parse_approach_bullets(new_approach)
        if new_bullets:
            task.approach_bullets = new_bullets[:6]
        task.theory_support = self.runtime.practice._infer_theory_support(
            theory_summary,
            task.title,
            task.situation,
            task.constraints_or_risk,
            task.goal,
            task.input_data,
            " ".join(task.approach_bullets),
        )
        task.approach_bullets = self.runtime.practice._normalize_approach_bullets(
            task.approach_bullets,
            task.theory_support,
            seed.language,
        )
        task.covered_outcomes = self.runtime.practice._infer_covered_outcomes(
            seed,
            task.title,
            task.situation,
            task.constraints_or_risk,
            task.goal,
            task.input_data,
            " ".join(task.approach_bullets),
        )
        task.theory_support = self.runtime.practice._infer_theory_support(
            theory_summary,
            task.title,
            task.situation,
            task.constraints_or_risk,
            task.goal,
            task.input_data,
            " ".join(task.approach_bullets),
        )
        task.p2p_criteria = self.runtime.practice._ensure_p2p_criteria(
            new_criteria or list(task.p2p_criteria or []),
            task.artifact_location,
            task.expected_artifact,
            task.theory_support,
            seed.language,
        )
        return True

    def apply_artifact_chain(
        self,
        practice_res: Any,
        seed: ProjectSeed,
        artifact_chain_plan: Any,
        warnings: list[str],
    ) -> Any:
        """Re-apply generic artifact-chain contract after possible task regeneration."""
        try:
            if artifact_chain_plan is not None:
                practice_res.tasks, artifact_chain_plan = self.runtime.practice.artifact_chain_planner.apply(
                    practice_res.tasks,
                    seed,
                    artifact_chain_plan,
                )
                practice_res.tasks = self.runtime.practice._ensure_task_artifact_contract(
                    practice_res.tasks,
                    seed,
                    seed.language,
                    artifact_chain_plan,
                )
                self.runtime.practice.last_artifact_chain_plan = artifact_chain_plan
                self.runtime.artifact_chain_plan = artifact_chain_plan
                self.runtime.evidence_specs = list(artifact_chain_plan.evidence_specs)
            else:
                practice_res.tasks = self.runtime.practice._ensure_task_artifact_contract(
                    practice_res.tasks,
                    seed,
                    seed.language,
                    None,
                )
            self.runtime.practice_tasks = practice_res.tasks
        except Exception as chain_err:
            warnings.append(f"⚠️ ArtifactChainPlanner: {chain_err}")
        return artifact_chain_plan

    def apply_critic_findings(
        self,
        practice_res: Any,
        seed: ProjectSeed,
        critic_issues: list[Any],
        issues_for_regen: list[Any],
        theory_extract_text: str,
        issues: list[Any],
        warnings: list[str],
    ) -> None:
        """Suppress critic false positives and append remaining critic issues."""
        practice_block = _render_practice_block(practice_res.tasks)
        if critic_issues:
            try:
                critic_issues = self.runtime.practice_critic._suppress_false_positives(
                    seed=seed,
                    practice_markdown=practice_block,
                    theory_summary=theory_extract_text,
                    issues=critic_issues,
                )
            except Exception as critic_filter_err:
                warnings.append(f"⚠️ PracticeCritic filter: {critic_filter_err}")
            self.runtime.practice_critic_issues = [issue.as_dict() for issue in critic_issues]
            for issue in critic_issues:
                issues.append(
                    {
                        "source": "PracticeCritic",
                        "kind": issue.kind,
                        "severity": issue.severity,
                        "task_index": max(1, int(issue.task_index or 1)),
                        "message": issue.message,
                        "suggestion": issue.suggestion,
                    }
                )
            if critic_issues:
                warnings.append(
                    f"ℹ️ PracticeCritic: найдено {len(critic_issues)} замечаний "
                    f"(перегенерировано задач по {len(issues_for_regen)} проблемам)"
                )

    def validate_practice(self, practice_res: Any, seed: ProjectSeed, issues: list[Any]) -> None:
        """Run final practice checks after critic/regeneration."""
        logger.info("🔄 Phase 3 | PracticeChecks")
        self.runtime.practice_checks = PracticeChecks(language=seed.language, expected_tasks=seed.tasks_count)
        practice_checks_result = self.runtime.practice_checks.check(practice_res.tasks)

        if not practice_checks_result.passed:
            logger.warning(f"⚠️ Practice Checks: {len(practice_checks_result.hard_issues)} HARD проблем")
            issues.extend(practice_checks_result.hard_issues)

        issues.extend(practice_checks_result.soft_issues)

    def generate_bonus_tasks(
        self,
        seed: ProjectSeed,
        generate_bonus: bool,
        section_context: dict[str, Any] | None,
        issues: list[Any],
        warnings: list[str],
    ) -> list[PracticeTask]:
        """Generate and validate optional bonus tasks."""
        bonus_tasks: list[PracticeTask] = []
        if not generate_bonus:
            return bonus_tasks

        n_bonus = 1
        if seed.bonus_wish:
            match = re.search(r"(\d+)\s*(?:задани|бонус)", seed.bonus_wish.lower())
            if match:
                n_bonus = min(max(int(match.group(1)), 1), 2)

        logger.info(f"🔄 Phase 3 | PracticeAgent.generate_bonus ({n_bonus})")
        try:
            bonus_tasks = self.runtime.practice.generate_bonus(seed, n_bonus)
            bonus_tasks = self.runtime.practice.finalize_bonus_tasks(
                bonus_tasks,
                seed,
                seed.language,
                sjm_override=(section_context or {}).get("sjm_context"),
            )
        except Exception as bonus_err:  # noqa: BLE001
            bonus_tasks = []
            warnings.append(f"⚠️ Бонусное задание не сгенерировано: {bonus_err}")
            logger.warning("⚠️ Ошибка генерации бонусного задания: %s", bonus_err)

        if bonus_tasks:
            logger.info("🔄 Phase 3 | BonusPracticeChecks")
            bonus_checks = PracticeChecks(language=seed.language, check_task_count=False).check(bonus_tasks)
            if bonus_checks.hard_issues:
                issues.extend(bonus_checks.hard_issues)
                warnings.append("⚠️ Бонусное задание скрыто: не прошло обязательные проверки практической части")
                logger.warning("⚠️ Бонусное задание не прошло hard-проверки: %s", len(bonus_checks.hard_issues))
                bonus_tasks = []
            else:
                issues.extend(bonus_checks.soft_issues)
        return bonus_tasks

    def generate_dataset_files(self, practice_res: Any, seed: ProjectSeed) -> list[dict[str, Any]]:
        """Generate dataset/material files after finalizing practice tasks."""
        logger.info("🔄 Phase 3 | DatasetGeneratorAgent")
        try:
            dataset_files = self.runtime.dataset_generator.generate_files(
                practice_res.tasks,
                seed,
                evidence_specs=list(getattr(self.runtime, "evidence_specs", []) or []),
            )
            self.runtime.dataset_files = dataset_files
            if dataset_files:
                logger.info(f"✅ Сгенерировано {len(dataset_files)} файлов данных")
            else:
                logger.info("ℹ️ Файлы данных не требуются для этих задач")
            return list(dataset_files or [])
        except Exception as dataset_err:
            logger.warning(f"⚠️ Ошибка генерации файлов данных: {dataset_err}")
            self.runtime.dataset_files = []
            return []

    @staticmethod
    def render_practice_document(
        readme_document: ReadmeDocument,
        tasks: list[Any],
        bonus_tasks: list[Any],
        generate_bonus: bool,
        seed: ProjectSeed,
    ) -> tuple[ReadmeDocument, bool]:
        """Render practice and optional bonus blocks into a typed README document."""
        practice_sections = _render_practice_sections(tasks)
        updated, changed = readme_document.with_replaced_chapter_children(
            3,
            practice_sections,
            language=seed.language,
        )
        if not changed:
            updated = readme_document.model_copy(deep=True)
            updated.sections.append(
                ReadmeSection(
                    title=_practice_chapter_title(seed.language),
                    level=2,
                    children=practice_sections,
                )
            )
        if generate_bonus:
            bonus_children = _render_bonus_sections(bonus_tasks)
            bonus_fragment = "Bonus" if (seed.language or "ru").casefold().strip() == "en" else "Бонус"
            if bonus_children:
                existing = updated.section_by_title_fragment(bonus_fragment)
                bonus_section = ReadmeSection(
                    title=existing.title if existing else bonus_fragment,
                    level=2,
                    children=[child.model_copy(deep=True) for child in bonus_children],
                )
                updated = updated.with_upserted_section_by_title_fragment(
                    bonus_fragment,
                    bonus_section,
                    fallback_level=2,
                )
            else:
                updated, _ = updated.without_section_by_title_fragment(bonus_fragment)
        return updated, True
