"""Theory phase executor with checks and local regeneration."""

import logging
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .config.thresholds import THRESHOLDS
from .domain_contracts import StaticInstructionLeakGuard
from .generation_runtime import GenerationRuntimeContainer
from .models.phase_results import TheoryPhaseResult
from .models.readme_document import ReadmeDocument, ReadmeSection
from .models.schemas import ProjectContextMeta, ProjectSeed, TheoryPart
from .observability import record_runtime_fallback_traces
from .recovery import ModelOutputNormalizer
from .utils.cancellation import CancelledError
from .utils.markdown_display_normalizer import normalize_markdown_display_blocks

logger = logging.getLogger("content_gen.theory_phase_executor")


def _remove_static_instruction_leaks(text: str, *, topic_text: str = "") -> str:
    """
    Убирает из теории фразы, которые относятся к статической инструкции проекта.

    Глава 2 должна объяснять предметную теорию и рабочий кейс. Ссылки на
    репозиторий, P2P и среду проверки допустимы в Главе 1, но в теории они
    создают ложный нарративный мост.
    """
    if not text:
        return text

    return StaticInstructionLeakGuard().strip(text, topic_text=topic_text)


def _polish_theory_part(orchestrator: Any, part: TheoryPart, seed: ProjectSeed) -> TheoryPart:
    """Applies optional local post-processing when the theory agent supports it."""
    polisher = getattr(getattr(orchestrator, "theory", None), "polish_part", None)
    if callable(polisher):
        return polisher(part, seed)
    try:
        from .agents.theory import _sanitize_theory_body_text, _sanitize_theory_example_text

        anchors: list[str] = []
        for tool in getattr(seed, "required_tools", []) or []:
            if tool:
                anchors.append(str(tool).lower())
        for skill in getattr(seed, "skills", []) or []:
            if skill:
                anchors.append(str(skill).lower())
        for outcome in getattr(seed, "learning_outcomes", []) or []:
            anchors.extend(
                token.lower()
                for token in re.findall(r"\w+", str(outcome))
                if len(token) >= 4
            )
        anchors.extend(
            token.lower()
            for token in re.findall(r"\w+", getattr(seed, "project_description", "") or "")
            if len(token) >= 4
        )
        anchors = list(dict.fromkeys(anchors))

        lo, hi = THRESHOLDS["theory_words_per_part"]
        part.body = _sanitize_theory_body_text(part.body, part.title, seed, anchors, lo, hi)
        part.example = _sanitize_theory_example_text((part.example or "").strip())
        part.bridge_questions = [
            question.strip()
            for question in (part.bridge_questions or [])
            if isinstance(question, str) and question.strip()
        ][:2]
    except Exception:
        return part
    return part


def _render_theory_part_markdown(part: Any, part_index: int) -> str:
    """Render one theory part into markdown for local regeneration."""
    chunks = [f"### 2.{part_index}. {part.title}", "", (part.body or "").strip()]

    example = (getattr(part, "example", "") or "").strip()
    if example:
        chunks.extend(["", f"**Пример:** {example}"])

    questions = [q.strip() for q in (getattr(part, "bridge_questions", []) or []) if isinstance(q, str) and q.strip()]
    if questions:
        chunks.extend(["", "**Вопросы к практике:**"])
        chunks.extend([f"- {question}" for question in questions])

    return "\n".join(chunks).strip()


def _parse_theory_part_markdown(markdown: str) -> tuple[str, str, str, list[str]]:
    """Parse regenerated theory markdown into title/body/example/questions."""
    text = ModelOutputNormalizer().normalize_theory_markdown(markdown or "").markdown.strip()
    header_match = re.search(r"^###\s+2\.\d+\.\s*(.+?)\s*$", text, flags=re.M)
    title = header_match.group(1).strip() if header_match else ""

    content = text[header_match.end():].strip() if header_match else text
    example_match = re.search(
        r"\*\*Пример:\*\*\s*(.+?)(?=\n\*\*Вопросы к практике:\*\*|\Z)",
        content,
        flags=re.S,
    )
    questions_match = re.search(r"\*\*Вопросы к практике:\*\*\s*(.+)$", content, flags=re.S)

    cut_positions = [match.start() for match in (example_match, questions_match) if match]
    body_end = min(cut_positions) if cut_positions else len(content)
    body = content[:body_end].strip()

    example = example_match.group(1).strip() if example_match else ""
    questions: list[str] = []
    if questions_match:
        questions_text = questions_match.group(1).strip()
        questions = [
            line.strip("- •\t ").rstrip()
            for line in questions_text.splitlines()
            if line.strip()
        ][:2]

    return title, body, example, questions


class TheoryPhaseExecutor:
    """Execute Phase 2 theory generation, checks, and local repair."""

    def __init__(self, runtime: GenerationRuntimeContainer) -> None:
        self.runtime = runtime

    def execute(
        self,
        seed: ProjectSeed,
        context_meta: ProjectContextMeta,
        markdown: str,
        practice_plan_contract: Any | None = None,
        section_context: dict[str, Any] | None = None,
    ) -> TheoryPhaseResult:
        """Run the full theory phase through explicit, testable sub-steps."""
        theory_res = self.generate_parts(seed, context_meta, practice_plan_contract, section_context)
        self.process_parts(theory_res, seed)
        initial_checks_result, warnings = self.repair_initial_issues(theory_res, seed)
        self.enhance_parts(theory_res, seed)
        self.repair_post_edit_issues(theory_res, seed, warnings)
        readme_document = self.render_document(ReadmeDocument.from_markdown(markdown), theory_res.parts, seed)
        readme_document = self.apply_completeness_check_document(readme_document, seed, context_meta, warnings)
        final_issues = self.finalize_checks(theory_res.parts, initial_checks_result, warnings)
        self.runtime.theory_parts = list(theory_res.parts)
        final_markdown = normalize_markdown_display_blocks(readme_document.to_markdown())
        readme_document = ReadmeDocument.from_markdown(final_markdown)
        return TheoryPhaseResult(
            markdown=final_markdown,
            readme_document=readme_document,
            theory_parts=list(theory_res.parts),
            issues=final_issues,
            warnings=warnings,
        )

    def generate_parts(
        self,
        seed: ProjectSeed,
        context_meta: ProjectContextMeta,
        practice_plan_contract: Any | None = None,
        section_context: dict[str, Any] | None = None,
    ) -> Any:
        """Generate initial theory parts using the theory agent."""
        logger.info("🔄 Phase 2 | TheoryAgent")
        lo, hi = THRESHOLDS["theory_parts"]
        desired_parts = random.randint(lo, hi)
        practice_plan_contract = practice_plan_contract or getattr(self.runtime, "practice_plan_contract", None)
        theory_res = self.runtime.theory.generate(
            seed,
            context_meta,
            desired_parts=desired_parts,
            practice_plan_contract=practice_plan_contract,
            section_context=section_context,
        )
        self.runtime.theory_parts = list(theory_res.parts)
        return theory_res

    def process_parts(self, theory_res: Any, seed: ProjectSeed) -> None:
        """Run definitions, length, and readability agents over generated parts."""
        cancellation_token = getattr(self.runtime, "cancellation_token", None)
        progress_tracker = getattr(self.runtime, "progress_tracker", None)

        if cancellation_token is None:
            from .utils.cancellation import CancellationToken

            cancellation_token = CancellationToken()
        if progress_tracker is None:
            from .utils.progress import ProgressTracker

            progress_tracker = ProgressTracker()

        agents_to_process = [
            ("definitions", self.runtime.definitions_agent, "DefinitionsAgent"),
            ("length", self.runtime.length_agent, "LengthAgent"),
            ("readability", self.runtime.readability_agent, "ReadabilityAgent"),
        ]

        for agent_name, agent, agent_display_name in agents_to_process:
            logger.info(f"🔄 Phase 2 | {agent_display_name} (параллельно)")

            if cancellation_token:
                cancellation_token.check()

            num_parts = len(theory_res.parts)
            if num_parts > 1:
                theory_res.parts = self._process_parts_parallel(
                    agent_name,
                    agent,
                    agent_display_name,
                    theory_res.parts,
                    seed,
                    progress_tracker,
                )
            else:
                theory_res.parts = [self._process_single_part(agent_name, agent, theory_res.parts[0], seed)]

    def _process_parts_parallel(
        self,
        agent_name: str,
        agent: Any,
        agent_display_name: str,
        parts: list[Any],
        seed: ProjectSeed,
        progress_tracker: Any,
    ) -> list[Any]:
        """Process one independent agent over all parts concurrently."""
        num_parts = len(parts)
        max_workers = min(4, num_parts)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._process_single_part, agent_name, agent, part, seed): part_idx
                for part_idx, part in enumerate(parts)
            }

            processed_parts = [None] * num_parts
            for future in as_completed(futures):
                part_idx = futures[future]
                try:
                    processed_part = future.result()
                    processed_parts[part_idx] = processed_part

                    if progress_tracker:
                        completed = sum(1 for p in processed_parts if p is not None)
                        progress_tracker.update(
                            phase="theory",
                            current=completed,
                            total=num_parts,
                            message=f"{agent_display_name}: часть {part_idx + 1}/{num_parts}",
                        )
                except CancelledError:
                    logger.warning(f"⚠️ Обработка {agent_display_name} отменена")
                    raise
                except Exception as e:
                    logger.error(f"⚠️ Ошибка при обработке части {part_idx + 1} в {agent_display_name}: {e}")
                    processed_parts[part_idx] = parts[part_idx]

        return processed_parts

    @staticmethod
    def _process_single_part(agent_name: str, agent: Any, part: Any, seed: ProjectSeed) -> Any:
        """Run a single post-processing agent over one theory part."""
        if agent_name == "definitions":
            return agent.ensure_definitions(part, seed)
        if agent_name == "length":
            return agent.fix_length(part, seed)
        if agent_name == "readability":
            return agent.improve_readability(part, seed)
        return part

    def repair_initial_issues(self, theory_res: Any, seed: ProjectSeed) -> tuple[Any, list[str]]:
        """Run initial TheoryChecks and regenerate fixable hard issues."""
        logger.info("🔄 Phase 2 | TheoryChecks")
        initial_checks_result = self.runtime.theory_checks.check(theory_res.parts)
        warnings: list[str] = []

        if not initial_checks_result.passed:
            logger.warning(f"⚠️ Theory Checks: {len(initial_checks_result.hard_issues)} замечаний качества")
            warnings.append(
                f"ℹ️ TheoryChecks: найдено {len(initial_checks_result.hard_issues)} замечаний качества, запускаю локальную коррекцию."
            )
            self._regenerate_fixable_parts(theory_res.parts, initial_checks_result.hard_issues, seed, warnings)

        return initial_checks_result, warnings

    def _regenerate_fixable_parts(
        self,
        parts: list[Any],
        hard_issues: list[Any],
        seed: ProjectSeed,
        warnings: list[str],
    ) -> None:
        """Regenerate parts for fixable hard theory-check issues."""
        for issue in hard_issues:
            if issue.fixable and issue.part_index > 0:
                part_idx = issue.part_index - 1
                if part_idx < len(parts):
                    part = parts[part_idx]
                    logger.info(f"🔄 Phase 2 | Regeneration части {issue.part_index}")
                    try:
                        part_md = _render_theory_part_markdown(part, issue.part_index)
                        regenerated = self.runtime.regeneration.regenerate(
                            original_md=part_md,
                            comments=f"Исправь проблему: {issue.message}",
                            language=seed.language,
                        ).regenerated_md
                        self._apply_regenerated_part(part, regenerated)
                    except Exception as e:
                        warnings.append(f"Не удалось перегенерировать часть {issue.part_index}: {e}")

    @staticmethod
    def _apply_regenerated_part(part: Any, regenerated_markdown: str) -> None:
        """Patch one part with parsed regenerated markdown fields."""
        new_title, new_body, new_example, new_questions = _parse_theory_part_markdown(regenerated_markdown)
        if new_title:
            part.title = new_title
        if new_body:
            part.body = new_body
        if new_example:
            part.example = new_example
        if new_questions:
            part.bridge_questions = new_questions

    def enhance_parts(self, theory_res: Any, seed: ProjectSeed) -> None:
        """Enhance, edit, and polish generated theory parts."""
        logger.info("🔄 Phase 2 | TheoryEnhancementAgent")
        enhanced_parts, enhancement_plan, _ = self.runtime.theory_enhancement.enhance(
            parts=theory_res.parts,
            seed=seed,
        )
        self._record_fallback_traces(list(getattr(enhancement_plan, "fallback_traces", []) or []))
        theory_res.parts = enhanced_parts

        logger.info("🔄 Phase 2 | ContentEditor.edit_theory_parts")
        theory_res.parts = self.runtime.content_editor.edit_theory_parts(theory_res.parts, seed)
        theory_res.parts = [_polish_theory_part(self.runtime, part, seed) for part in theory_res.parts]

    def _record_fallback_traces(self, events: list[dict[str, Any]]) -> None:
        """Store fallback events on runtime when the container supports it."""
        record_runtime_fallback_traces(self.runtime, events)

    def repair_post_edit_issues(self, theory_res: Any, seed: ProjectSeed, warnings: list[str]) -> None:
        """Run final local repair after enhancement/editor passes."""
        post_edit_checks = self.runtime.theory_checks.check(theory_res.parts)
        if post_edit_checks.hard_issues:
            logger.info("🔄 Phase 2 | TheoryRepairPass")
            for issue in post_edit_checks.hard_issues:
                if not issue.fixable or issue.part_index <= 0:
                    continue
                part_idx = issue.part_index - 1
                if part_idx >= len(theory_res.parts):
                    continue
                part = theory_res.parts[part_idx]
                try:
                    if issue.criterion_id == "2.4.3":
                        part = self.runtime.length_agent.fix_length(part, seed, max_attempts=1)
                    elif issue.criterion_id == "2.4.6" and "пример" in issue.message.lower():
                        if not (part.example or "").strip():
                            part.example = (
                                f"Например, в проекте ты сравниваешь варианты решения по теме «{part.title}» "
                                "и фиксируешь, почему выбранный подход лучше подходит под ограничения команды."
                            )
                    elif issue.criterion_id == "2.4.6" and "вопросы к практике" in issue.message.lower():
                        if not part.bridge_questions:
                            part.bridge_questions = [
                                f"Как решение по теме «{part.title}» повлияет на твой практический выбор в этом проекте?"
                            ]
                    theory_res.parts[part_idx] = _polish_theory_part(self.runtime, part, seed)
                except Exception as exc:
                    warnings.append(f"Не удалось выполнить финальную коррекцию части {issue.part_index}: {exc}")

    def render_document(
        self,
        readme_document: ReadmeDocument,
        parts: list[Any],
        seed: ProjectSeed,
    ) -> ReadmeDocument:
        """Render theory parts into Chapter 2 of the typed README document."""
        updated, changed = readme_document.with_replaced_chapter_children(
            2,
            self._render_theory_sections(parts, seed),
            language=seed.language,
        )
        return updated if changed else readme_document

    def _render_theory_sections(self, parts: list[Any], seed: ProjectSeed) -> list[ReadmeSection]:
        """Build typed public Chapter 2 sections from structured theory parts."""
        topic_text = self._topic_text(seed)
        sections: list[ReadmeSection] = []
        for i, part in enumerate(parts, 1):
            clean_body = self._clean_theory_body(part.body, topic_text)
            qs = "\n".join(f"- {q}" for q in part.bridge_questions)
            body = (
                f"{clean_body}\n\n"
                f"**Пример:** {part.example}\n\n"
                f"**Вопросы к практике:**\n{qs}"
            )
            sections.append(
                ReadmeSection(
                    title=f"2.{i}. {part.title}",
                    level=3,
                    body=body.strip(),
                )
            )
        return sections

    @staticmethod
    def _topic_text(seed: ProjectSeed) -> str:
        return " ".join(
            [
                getattr(seed, "title_seed", "") or "",
                getattr(seed, "project_description", "") or "",
                " ".join(getattr(seed, "learning_outcomes", []) or []),
                " ".join(getattr(seed, "skills", []) or []),
            ]
        )

    @staticmethod
    def _clean_theory_body(body: str, topic_text: str) -> str:
        clean_body = re.sub(r"^##\s+Глава\s+2[^\n]*\n", "", body, flags=re.M)
        clean_body = re.sub(r"^###\s+Глава\s+2[^\n]*\n", "", clean_body, flags=re.M)
        clean_body = re.sub(r"^\*\*Глава\s+2[^\*]+\*\*\s*\n?", "", clean_body, flags=re.M)
        clean_body = re.sub(r"^\s*\*\*Глава\s+2[^\*]+\*\*\s*\n?", "", clean_body, flags=re.M)
        if "**Вопросы к практике:**" in clean_body:
            clean_body = clean_body.split("**Вопросы к практике:**", 1)[0].strip()
        clean_body = _remove_static_instruction_leaks(clean_body, topic_text=topic_text)
        return normalize_markdown_display_blocks(clean_body.strip())

    def apply_completeness_check_document(
        self,
        readme_document: ReadmeDocument,
        seed: ProjectSeed,
        context_meta: ProjectContextMeta,
        warnings: list[str],
    ) -> ReadmeDocument:
        """Optionally enhance theory in README-improvement mode on the typed document."""
        try:
            import sys

            if "api.utils.logging_context" not in sys.modules:
                return readme_document

            from api.utils.improvement_cache import get_extract_request_id, get_extracted_data, get_original_readme
            from api.utils.logging_context import get_request_id

            from .agents.theory_completeness_agent import TheoryCompletenessAgent

            generation_request_id = get_request_id()
            if not generation_request_id:
                return readme_document
            extract_request_id = get_extract_request_id(generation_request_id)
            if not extract_request_id:
                return readme_document

            logger.info("🔄 Phase 2 | TheoryCompletenessAgent (режим улучшения README)")
            original_readme = get_original_readme(extract_request_id)
            extracted_data = get_extracted_data(extract_request_id)
            if not (original_readme and extracted_data):
                return readme_document

            partial_seed, _classification = extracted_data
            extracted_topics, extracted_tools = self._extract_completeness_inputs(partial_seed, original_readme)

            if self.runtime.theory_completeness is None:
                llm_client = (
                    self.runtime.llm_for("theory", "TheoryCompletenessAgent", "theory_completeness")
                    if hasattr(self.runtime, "llm_for")
                    else self.runtime.llm
                )
                self.runtime.theory_completeness = TheoryCompletenessAgent(llm_client)

            markdown = readme_document.to_markdown()
            enhanced_md, completeness_warnings, completeness_issues = self.runtime.theory_completeness.check_and_enhance(
                theory_markdown=markdown,
                original_readme=original_readme,
                extracted_topics=extracted_topics,
                extracted_tools=extracted_tools,
                seed=seed,
                context_meta=context_meta,
            )

            if enhanced_md != markdown:
                logger.info("✅ Теория дополнена недостающими темами и инструментами")
                readme_document = self._replace_with_enhanced_theory_document(readme_document, enhanced_md, seed)

            warnings.extend(completeness_warnings)
            warnings.extend(str(issue) for issue in completeness_issues[:3] if issue)
        except ImportError:
            return readme_document
        except Exception as e:
            logger.warning(f"⚠️ Не удалось проверить полноту теории: {e}")
        return readme_document

    @staticmethod
    def _extract_completeness_inputs(partial_seed: Any, original_readme: str) -> tuple[list[str], list[str]]:
        """Extract topics and tools from source README improvement context."""
        extracted_topics = []
        extracted_tools = []

        if hasattr(partial_seed, "theory_parts") and partial_seed.theory_parts:
            extracted_topics = [str(part) for part in partial_seed.theory_parts if part]

        if hasattr(partial_seed, "required_tools") and partial_seed.required_tools:
            extracted_tools = [str(tool) for tool in partial_seed.required_tools if tool]

        if original_readme:
            theory_headers = re.findall(r"^#{2,3}\s+(.+?)$", original_readme, re.MULTILINE)
            for header in theory_headers:
                header_clean = header.strip()
                if header_clean and header_clean not in extracted_topics:
                    if "глава" not in header_clean.lower() and "теория" not in header_clean.lower():
                        extracted_topics.append(header_clean)

        return extracted_topics, extracted_tools

    @staticmethod
    def _replace_with_enhanced_theory_document(
        readme_document: ReadmeDocument,
        enhanced_markdown: str,
        seed: ProjectSeed,
    ) -> ReadmeDocument:
        """Replace Chapter 2 with enhanced theory in a typed README document."""
        enhanced_document = ReadmeDocument.from_markdown(enhanced_markdown)
        enhanced_chapter = enhanced_document.chapter_section(2, language=seed.language)
        if enhanced_chapter is not None:
            updated, changed = readme_document.with_replaced_chapter_children(
                2,
                enhanced_chapter.children,
                chapter_body=enhanced_chapter.body,
                language=seed.language,
            )
            if changed:
                return updated

        is_chapter_body_only = not enhanced_document.sections or all(
            section.level >= 3 for section in enhanced_document.sections
        )
        if is_chapter_body_only:
            updated, changed = readme_document.with_replaced_chapter_body(
                2,
                enhanced_markdown,
                language=seed.language,
            )
            if changed:
                return updated
        return enhanced_document

    def finalize_checks(
        self,
        parts: list[Any],
        initial_checks_result: Any,
        warnings: list[str],
    ) -> list[Any]:
        """Run final TheoryChecks and return final issue set."""
        logger.info("🔄 Phase 2 | TheoryChecks (final)")
        final_checks_result = self.runtime.theory_checks.check(parts)

        if initial_checks_result.hard_issues and not final_checks_result.hard_issues:
            warnings.append("✅ TheoryChecks: локальная коррекция сняла замечания качества.")
        elif final_checks_result.hard_issues:
            warnings.append(
                f"⚠️ TheoryChecks: после автокоррекции осталось {len(final_checks_result.hard_issues)} замечаний качества."
            )

        return list(final_checks_result.hard_issues) + list(final_checks_result.soft_issues)
