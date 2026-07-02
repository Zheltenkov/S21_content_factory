"""Main theory generation service."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..config.loader import prompt_trace_kwargs
from ..domain_contracts import SectionContextPolicy
from ..models.schemas import ProjectContextMeta, ProjectSeed, TheoryPart
from ..project_planning import render_practice_plan_contract_section
from ..recovery import ModelOutputNormalizer
from .base.llm_client import LLMClientProtocol
from .theory_generation import (
    build_theory_example_prompt,
    parse_theory_example_response,
    parse_theory_part_blocks,
    parse_theory_questions_response,
    pick_theory_parts_count,
    strip_theory_chapter_heading,
    theory_anchor_terms,
    validate_bridge_questions,
)
from .theory_materializer import TheoryPartMaterializer
from .theory_prompting import (
    build_theory_content_type_section,
    build_theory_curriculum_context_section,
    build_theory_formulas_requirements,
    build_theory_questions_prompt,
    build_theory_sjm_section,
    determine_theory_content_type,
)


StyleRewrite = Callable[[str, str], str]


@dataclass
class TheoryGenerationOutcome:
    """Output of the main theory generation service."""

    parts: list[TheoryPart]


class TheoryGenerationService:
    """Build prompt, invoke LLM, repair fallback fields and materialize theory parts."""

    RX_PART = re.compile(r"^###\s+2\.(\d+)\.\s*(.+?)\s*$", re.M)
    RX_EXAMPLE = re.compile(r"\*\*Пример:\*\*\s*(.+)")
    RX_QS = re.compile(r"\*\*Вопросы к практике:\*\*([\s\S]+?)(?=\n###|\Z)", re.M)

    def __init__(
        self,
        *,
        llm: LLMClientProtocol,
        config: Any,
        llm_kwargs: dict[str, Any],
        didactics_context: str,
        style_rewrite: StyleRewrite,
        materializer: TheoryPartMaterializer,
        logger: logging.Logger | None = None,
    ) -> None:
        self.llm = llm
        self.config = config
        self.llm_kwargs = llm_kwargs
        self.didactics_context = didactics_context
        self.style_rewrite = style_rewrite
        self.materializer = materializer
        self.output_normalizer = ModelOutputNormalizer()
        self.logger = logger or logging.getLogger("content_gen.agents.theory_generation_service")

    def generate(
        self,
        seed: ProjectSeed,
        context_meta: ProjectContextMeta,
        desired_parts: int = 3,
        *,
        practice_plan_contract: Any | None = None,
        section_context: dict[str, Any] | None = None,
    ) -> TheoryGenerationOutcome:
        """Generate and materialize theory parts."""
        n_parts = pick_theory_parts_count(desired_parts)
        system_prompt = self._build_system_prompt(seed)
        user_prompt = self._build_user_prompt(
            seed,
            context_meta,
            n_parts=n_parts,
            practice_plan_contract=practice_plan_contract,
            section_context=section_context,
        )

        generation_kwargs = self.llm_kwargs.copy()
        generation_kwargs.setdefault("temperature", 0.2)
        generation_kwargs.update(
            prompt_trace_kwargs(
                self.config,
                "system",
                "user_template",
                output_schema="TheoryPart[]",
            )
        )
        markdown = self.llm.complete(system=system_prompt, user=user_prompt, **generation_kwargs)
        markdown = strip_theory_chapter_heading(markdown)
        normalization = self.output_normalizer.normalize_theory_markdown(markdown)
        markdown = normalization.markdown
        if normalization.changed:
            self.logger.warning("Theory model output normalized: %s", "; ".join(normalization.changes))

        if not markdown or len(markdown.strip()) < 50:
            self.logger.warning("LLM вернул пустой или очень короткий ответ (%s символов)", len(markdown) if markdown else 0)
        else:
            self.logger.debug("Получен ответ от LLM (%s символов)", len(markdown))

        header_matches = list(self.RX_PART.finditer(markdown))
        print(
            f"  📋 Найдено {len(header_matches)} частей теории. Выполняю проверку для ВСЕХ {len(header_matches)} частей...",
            file=sys.stderr,
            flush=True,
        )
        if len(header_matches) == 0:
            print(
                f"  ⚠️ ВНИМАНИЕ: Регулярное выражение не нашло ни одной части! Паттерн: {self.RX_PART.pattern}",
                file=sys.stderr,
                flush=True,
            )
            print("  💡 Попробуем найти части вручную...", file=sys.stderr, flush=True)

        parts_data, examples_to_generate, questions_to_generate = parse_theory_part_blocks(
            markdown,
            rx_part=self.RX_PART,
            rx_example=self.RX_EXAMPLE,
            rx_qs=self.RX_QS,
            seed=seed,
            style_rewrite=self.style_rewrite,
            anchors=theory_anchor_terms(seed),
        )
        self._fill_missing_examples(parts_data, examples_to_generate, seed)
        self._fill_missing_questions(parts_data, questions_to_generate, seed)

        parts = [
            self._materialize_part(part_data, idx=idx, total_headers=len(header_matches), seed=seed)
            for idx, part_data in enumerate(parts_data)
        ]
        print(
            f"  ✅ Все {len(parts)} частей теории проверены и обработаны (проверка выполнена для всех {len(parts)} частей)",
            file=sys.stderr,
            flush=True,
        )
        return TheoryGenerationOutcome(parts=parts)

    def _build_system_prompt(self, seed: ProjectSeed) -> str:
        """Build system prompt with didactics context."""
        system_prompt = self.config.get_prompt("system").format(language=seed.language)
        if self.didactics_context:
            system_prompt = f"{system_prompt}\n\n=== DIDACTICS CONTEXT ===\n{self.didactics_context}"
        return system_prompt

    def _build_user_prompt(
        self,
        seed: ProjectSeed,
        context_meta: ProjectContextMeta,
        *,
        n_parts: int,
        practice_plan_contract: Any | None,
        section_context: dict[str, Any] | None,
    ) -> str:
        """Build the main theory user prompt."""
        curriculum_context_section = build_theory_curriculum_context_section(seed, section_context=section_context)
        sjm_section = build_theory_sjm_section(seed, section_context=section_context)
        practice_plan_section = render_practice_plan_contract_section(
            practice_plan_contract or (section_context or {}).get("practice_plan_contract")
        )

        content_type = determine_theory_content_type(seed)
        direction = getattr(seed, "direction", "") or seed.thematic_block or "—"
        self.logger.info("Тип контента: %s (direction=%s)", content_type, direction)
        self._log_curriculum_context(seed)

        platform_name = getattr(seed, "platform_name", None) or "project"
        topic_text = " ".join(
            [
                getattr(seed, "title_seed", "") or "",
                getattr(seed, "project_description", "") or "",
                " ".join(getattr(seed, "learning_outcomes", []) or []),
                " ".join(getattr(seed, "skills", []) or []),
            ]
        )
        gitlab_link_raw = getattr(seed, "gitlab_link", None) or "—"
        gitlab_link = (
            "—"
            if section_context is not None
            else (
                SectionContextPolicy.for_theory().filter_context_value(
                    "gitlab_link",
                    gitlab_link_raw,
                    topic_text=topic_text,
                )
                or "—"
            )
        )

        filtered_learning_outcomes = (section_context or {}).get("learning_outcomes") or seed.learning_outcomes
        filtered_skills = (section_context or {}).get("skills") or seed.skills
        filtered_project_description = (section_context or {}).get("project_description") or seed.project_description
        filtered_context_summary = (section_context or {}).get("context_summary") or context_meta.context_summary or "—"
        filtered_narrative_anchor = (section_context or {}).get("narrative_anchor") or context_meta.narrative_anchor or "—"

        user_prompt = self.config.get_prompt("user_template").format(
            n_parts=n_parts,
            learning_outcomes="; ".join(filtered_learning_outcomes),
            direction=direction,
            track=seed.thematic_block,
            project_description=filtered_project_description,
            skills="; ".join(filtered_skills),
            context_summary=filtered_context_summary,
            narrative_anchor=filtered_narrative_anchor,
            include_formulas=seed.include_formulas,
            include_tables=seed.include_tables,
            include_diagrams=seed.include_diagrams,
            curriculum_context_section=curriculum_context_section,
            sjm_section=sjm_section,
            content_type_section=build_theory_content_type_section(content_type),
            formulas_code_requirements=build_theory_formulas_requirements(seed, content_type),
            platform_name=platform_name,
            gitlab_link=gitlab_link,
            required_software=", ".join(getattr(seed, "required_software", []) or []) or "—",
            workload_hours=getattr(seed, "workload_hours", None) or "—",
            i="{i}",
        )
        if practice_plan_section:
            user_prompt = (
                f"{user_prompt}\n\n=== ПЛАН УЧЕБНОЙ ДЕЯТЕЛЬНОСТИ ДО ТЕОРИИ ===\n"
                "Сгенерируй Главу 2 как поддержку этого плана. Не раскрывай готовые ответы практики.\n\n"
                f"{practice_plan_section}"
            )
        if seed.reference_project_hint and seed.reference_project_hint.strip():
            user_prompt = (
                f"{user_prompt}\n\n=== ЭТАЛОН ИДЕАЛЬНОГО ПРОЕКТА ===\n"
                "Ниже дан reference по качеству и глубине объяснения. Используй его как ориентир по структуре,"
                " но не копируй формулировки и не подменяй им темы текущего проекта из УП.\n\n"
                f"{seed.reference_project_hint.strip()}"
            )
        return user_prompt

    def _fill_missing_examples(
        self,
        parts_data: list[dict[str, Any]],
        examples_to_generate: list[int],
        seed: ProjectSeed,
    ) -> None:
        """Generate missing examples for parsed theory parts."""
        if not examples_to_generate:
            return
        print(f"  ⚠️ Генерирую примеры для {len(examples_to_generate)} частей (батч)...", file=sys.stderr, flush=True)
        system_prompt = self.config.get_prompt("system").format(language=seed.language)

        if hasattr(self.llm, "complete_batch"):
            trace_kwargs = prompt_trace_kwargs(self.config, "system", output_schema="theory_example")
            batch_requests = [
                (
                    system_prompt,
                    build_theory_example_prompt(parts_data[idx]),
                    None,
                    {"temperature": 0.3, **trace_kwargs},
                )
                for idx in examples_to_generate
            ]
            example_results = self.llm.complete_batch(batch_requests)
            for result_idx, part_idx in enumerate(examples_to_generate):
                example_md = example_results[result_idx]
                parts_data[part_idx]["example"] = self.style_rewrite(
                    parse_theory_example_response(example_md),
                    seed.language,
                )
        else:
            for part_idx in examples_to_generate:
                user_prompt = build_theory_example_prompt(parts_data[part_idx])
                example_md = self.llm.complete(
                    system=system_prompt,
                    user=user_prompt,
                    temperature=0.3,
                    **prompt_trace_kwargs(self.config, "system", output_schema="theory_example"),
                )
                parts_data[part_idx]["example"] = self.style_rewrite(
                    parse_theory_example_response(example_md),
                    seed.language,
                )

        print(f"  ✅ Сгенерировано {len(examples_to_generate)} примеров", file=sys.stderr, flush=True)

    def _fill_missing_questions(
        self,
        parts_data: list[dict[str, Any]],
        questions_to_generate: list[int],
        seed: ProjectSeed,
    ) -> None:
        """Generate and validate missing practice bridge questions."""
        if not questions_to_generate:
            return
        print(f"  ⚠️ Генерирую вопросы для {len(questions_to_generate)} частей (батч)...", file=sys.stderr, flush=True)
        system_prompt = self.config.get_prompt("system").format(language=seed.language)

        if hasattr(self.llm, "complete_batch"):
            trace_kwargs = prompt_trace_kwargs(self.config, "system", output_schema="theory_bridge_questions")
            batch_requests = [
                (
                    system_prompt,
                    build_theory_questions_prompt(parts_data[idx], seed),
                    None,
                    {"temperature": 0.3, **trace_kwargs},
                )
                for idx in questions_to_generate
            ]
            question_results = self.llm.complete_batch(batch_requests)
            needs_regen: list[int] = []
            for result_idx, part_idx in enumerate(questions_to_generate):
                questions = parse_theory_questions_response(question_results[result_idx])
                parts_data[part_idx]["qs"] = questions
                if not self._validate_questions(questions, seed):
                    needs_regen.append(part_idx)

            for part_idx in needs_regen:
                parts_data[part_idx]["qs"] = self._regenerate_or_fallback_questions(
                    parts_data[part_idx],
                    seed,
                    system_prompt,
                )
        else:
            for part_idx in questions_to_generate:
                questions_md = self.llm.complete(
                    system=system_prompt,
                    user=build_theory_questions_prompt(parts_data[part_idx], seed),
                    temperature=0.3,
                    **prompt_trace_kwargs(self.config, "system", output_schema="theory_bridge_questions"),
                )
                questions = parse_theory_questions_response(questions_md)
                if not self._validate_questions(questions, seed):
                    questions = self._regenerate_or_fallback_questions(parts_data[part_idx], seed, system_prompt)
                parts_data[part_idx]["qs"] = questions

        print(f"  ✅ Сгенерировано вопросов для {len(questions_to_generate)} частей", file=sys.stderr, flush=True)

    def _regenerate_or_fallback_questions(
        self,
        part_data: dict[str, Any],
        seed: ProjectSeed,
        system_prompt: str,
    ) -> list[str]:
        """Regenerate weak questions once, then use deterministic fallback."""
        fix_prompt = (
            build_theory_questions_prompt(part_data, seed)
            + "\nУточнение: вопросы должны содержать явную связь с проектом и инструментами."
        )
        questions_md = self.llm.complete(
            system=system_prompt,
            user=fix_prompt,
            temperature=0.1,
            **prompt_trace_kwargs(self.config, "system", output_schema="theory_bridge_questions"),
        )
        questions = parse_theory_questions_response(questions_md)
        if self._validate_questions(questions, seed):
            return questions
        anchors = theory_anchor_terms(seed)
        anchor = anchors[0] if anchors else "инструменты проекта"
        return [f"Как применишь {anchor} в практической задаче этого проекта?"]

    def _validate_questions(self, questions: list[str], seed: ProjectSeed) -> bool:
        """Validate generated bridge questions for project specificity."""
        return validate_bridge_questions(questions, seed, theory_anchor_terms(seed))

    def _materialize_part(
        self,
        part_data: dict[str, Any],
        *,
        idx: int,
        total_headers: int,
        seed: ProjectSeed,
    ) -> TheoryPart:
        """Materialize one parsed part and emit the existing progress line."""
        part = self.materializer.materialize(part_data, seed)
        print(
            f"  ✅ Часть {idx + 1}/{total_headers} '{part.title}' проверена и обработана",
            file=sys.stderr,
            flush=True,
        )
        return part

    def _log_curriculum_context(self, seed: ProjectSeed) -> None:
        """Log compact curriculum context diagnostics."""
        ctx = getattr(seed, "curriculum_context", None)
        if not ctx:
            return
        self.logger.info("=" * 50)
        self.logger.info("THEORY AGENT - Контекст УП для промпта:")
        self.logger.info("  Блок: %s", ctx.get("block_name", "N/A"))
        self.logger.info("  Номер проекта: %s", ctx.get("current_project_order", "N/A"))
        self.logger.info("  Проектов ДО: %s, ПОСЛЕ: %s", len(ctx.get("previous_projects", [])), len(ctx.get("next_projects", [])))
        self.logger.info("  SJM: %s", "Да" if ctx.get("sjm_context") else "Нет")
        self.logger.info("=" * 50)
