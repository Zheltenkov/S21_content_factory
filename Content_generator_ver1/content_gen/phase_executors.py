"""Phase executors backed by the generation runtime container."""

from __future__ import annotations

import logging
import re
from typing import Any

from .generation_runtime import GenerationRuntimeContainer
from .models.phase_results import EvaluationPhaseResult, QualityPhaseResult, TranslationPhaseResult
from .models.readme_document import ReadmeDocument
from .models.schemas import ProjectSeed
from .utils.markdown_display_normalizer import normalize_markdown_display_blocks
from .utils.markdown_helpers import clean_duplicate_chapter_headers
from .utils.rubric_export import criteria_to_json
from .validators.rubric import RubricScorer

logger = logging.getLogger("content_gen.phase_executors")


class QualityPhaseExecutor:
    """Execute Phase 4 global quality pass."""

    def __init__(self, runtime: GenerationRuntimeContainer) -> None:
        self.runtime = runtime

    def execute(
        self,
        seed: ProjectSeed,
        markdown: str,
        readme_document: ReadmeDocument | None = None,
        story_map_contract: Any | None = None,
    ) -> QualityPhaseResult:
        """Run quality passes while carrying a typed README document between steps."""
        document = ReadmeDocument.from_value(readme_document, fallback_markdown=markdown)

        logger.info("🔄 Phase 4 | ContentEditor.ensure_global_coherence")
        document = self._run_content_editor(seed, document)
        document = self._ensure_final_section_document(seed, document, story_map_contract=story_map_contract)

        logger.info("🔄 Phase 4 | TOCRenderer")
        document = self._run_toc(seed, document)

        md = clean_duplicate_chapter_headers(document.to_markdown(), seed.language)
        document = ReadmeDocument.from_markdown(md)

        logger.info("🔄 Phase 4 | StyleGuardRepair")
        document = self._run_style(seed, document)
        document = self._ensure_final_section_document(seed, document, story_map_contract=story_map_contract)

        final_markdown = normalize_markdown_display_blocks(document.to_markdown())
        return QualityPhaseResult(
            markdown=final_markdown,
            readme_document=ReadmeDocument.from_markdown(final_markdown),
        )

    def _run_content_editor(
        self,
        seed: ProjectSeed,
        readme_document: ReadmeDocument,
    ) -> ReadmeDocument:
        """Run content editor through the typed README contract."""
        editor = self.runtime.content_editor
        result = editor.ensure_global_coherence_document(readme_document, seed)
        return ReadmeDocument.from_value(result, fallback_markdown=readme_document.to_markdown())

    def _run_toc(
        self,
        seed: ProjectSeed,
        readme_document: ReadmeDocument,
    ) -> ReadmeDocument:
        """Rebuild and inject TOC through typed TOC methods."""
        toc_agent = self.runtime.toc
        toc_res = toc_agent.build_document(readme_document, language=seed.language)
        result = toc_agent.inject_document(readme_document, toc_res.toc_md, language=seed.language)
        return ReadmeDocument.from_value(result, fallback_markdown=readme_document.to_markdown())

    def _run_style(
        self,
        seed: ProjectSeed,
        readme_document: ReadmeDocument,
    ) -> ReadmeDocument:
        """Run style guard through the typed README contract."""
        style = self.runtime.style
        issues_style = style.lint_document(readme_document, seed.language)
        if not issues_style:
            return readme_document

        result = style.rewrite_document(readme_document, seed.language)
        return ReadmeDocument.from_value(result, fallback_markdown=readme_document.to_markdown())

    def _ensure_final_section_document(
        self,
        seed: ProjectSeed,
        readme_document: ReadmeDocument,
        story_map_contract: Any | None = None,
    ) -> ReadmeDocument:
        """Append a source-compliant closing section to the typed README."""
        final_body = self._final_section_body(seed, story_map_contract=story_map_contract)
        for section in readme_document.sections:
            if re.search(r"^(?:Заключение|Итог проекта|Финал проекта|Завершение проекта)\b", section.title, flags=re.I):
                if not self._is_placeholder_final_section(section.body_markdown()):
                    return readme_document
                return readme_document.with_upserted_section_by_title_fragment(
                    section.title,
                    f"{'#' * section.level} {section.title}\n\n{final_body}",
                    fallback_level=section.level,
                )
        return readme_document.with_upserted_section_by_title_fragment(
            "Заключение",
            f"## Заключение\n\n{final_body}",
            fallback_level=2,
        )

    @staticmethod
    def _is_placeholder_final_section(body: str) -> bool:
        """Return whether a final section is still a skeleton placeholder."""
        normalized = re.sub(r"\s+", " ", (body or "").strip()).casefold()
        if not normalized:
            return True
        placeholder_patterns = [
            r"^\(?финальное завершение текущего проекта без анонса следующего\)?$",
            r"^\(?здесь будет\b",
            r"^<[^>]+>$",
        ]
        return any(re.search(pattern, normalized, flags=re.I) for pattern in placeholder_patterns)

    def _final_section_body(self, seed: ProjectSeed, story_map_contract: Any | None = None) -> str:
        """Build the final project conclusion body."""
        title = (getattr(seed, "title_seed", "") or getattr(seed, "project_description", "") or "проект").strip()
        completion = ""
        story_map = story_map_contract or getattr(self.runtime, "story_map_contract", None)
        if story_map is not None:
            completion = str(self._contract_value(story_map, "completion") or "")
        if not completion or self._is_placeholder_final_section(completion) or self._mentions_next_project(completion):
            completion = (
                "Собери итоговый артефакт, проверь его по критериям заданий и убедись, "
                "что peer-review может принять работу без дополнительных пояснений."
            )

        return (
            f"В финале у тебя должен остаться проверяемый результат текущего проекта «{title}». "
            f"{completion.rstrip('.')}.\n\n"
            "Проверь, что ключевые решения опираются на материалы проекта, артефакты лежат по указанным путям, "
            "а каждый важный вывод можно показать на p2p-ревью."
        )

    @staticmethod
    def _contract_value(contract: Any, key: str) -> Any:
        """Read a contract field from either a typed object or serialized dict."""
        if isinstance(contract, dict):
            return contract.get(key)
        return getattr(contract, key, None)

    @staticmethod
    def _mentions_next_project(text: str) -> bool:
        """Guard the final section from teaser-like references to the next project."""
        return bool(re.search(r"следующ(?:ий|ем|его|ему)\s+проект", text or "", flags=re.I))


class EvaluationPhaseExecutor:
    """Execute Phase 5/6 final evaluation and rubric scoring."""

    def __init__(self, runtime: GenerationRuntimeContainer) -> None:
        self.runtime = runtime

    def execute(
        self,
        seed: ProjectSeed,
        markdown: str,
        readme_document: ReadmeDocument | None = None,
    ) -> EvaluationPhaseResult:
        readme_document = ReadmeDocument.from_value(readme_document, fallback_markdown=markdown)
        logger.info("🔄 Phase 5 | Validators")
        issues_intro = self._validate_intro(markdown, readme_document)
        issues_theory = self._validate_theory(markdown, readme_document)
        issues_practice = self._validate_practice(markdown, readme_document, seed)

        logger.info("🔄 Phase 5 | RubricScorer")
        llm_client = (
            self.runtime.llm_for("evaluation", "RubricScorer", "rubric")
            if hasattr(self.runtime, "llm_for")
            else self.runtime.llm
        )
        self.runtime.rubric = RubricScorer(language=seed.language, llm_client=llm_client)
        criteria_report = self._score_rubric(markdown, readme_document, seed)
        rubric_json = criteria_to_json(criteria_report)

        all_issues = (
            [issue.__dict__ for issue in issues_intro]
            + [issue.__dict__ for issue in issues_theory]
            + [issue.__dict__ for issue in issues_practice]
        )
        return EvaluationPhaseResult(
            rubric_json=rubric_json,
            issues=all_issues,
            readme_document=readme_document,
        )

    def _validate_intro(self, markdown: str, readme_document: ReadmeDocument) -> list[Any]:
        """Run the typed intro validator."""
        return self.runtime.intro_validator.validate_document(readme_document)

    def _validate_theory(self, markdown: str, readme_document: ReadmeDocument) -> list[Any]:
        """Run the typed theory validator."""
        return self.runtime.theory_validator.validate_document(readme_document)

    def _validate_practice(self, markdown: str, readme_document: ReadmeDocument, seed: ProjectSeed) -> list[Any]:
        """Run the typed practice validator."""
        return self.runtime.practice_validator.validate_document(
            readme_document,
            language=seed.language,
            tasks_count_expected=seed.tasks_count,
        )

    def _score_rubric(self, markdown: str, readme_document: ReadmeDocument, seed: ProjectSeed) -> Any:
        """Run typed rubric scoring."""
        return self.runtime.rubric.score_document(readme_document, learning_outcomes=seed.learning_outcomes)


class TranslationPhaseExecutor:
    """Execute final translation when the target language differs from Russian."""

    def __init__(self, runtime: GenerationRuntimeContainer) -> None:
        self.runtime = runtime

    def execute(
        self,
        seed: ProjectSeed,
        markdown: str,
        target_language: str,
        readme_document: ReadmeDocument | None = None,
    ) -> TranslationPhaseResult:
        readme_document = ReadmeDocument.from_value(readme_document, fallback_markdown=markdown)
        original_md = markdown
        logger.info(
            "🔄 Phase 6 | Входные параметры: target_language='%s', размер md=%s символов",
            target_language,
            len(original_md),
        )

        if target_language != "ru":
            logger.info("🔄 Phase 6 | TranslatorAgent (перевод на %s)", target_language)
            translated_md = self.runtime.translator.translate(original_md, target_language, seed)
            if original_md == translated_md:
                logger.warning(
                    "⚠️ Phase 6 | Переводчик вернул исходный текст без изменений для языка %s!",
                    target_language,
                )
                logger.warning("⚠️ Phase 6 | Первые 200 символов оригинала: %s", original_md[:200])
                logger.warning("⚠️ Phase 6 | Первые 200 символов перевода: %s", translated_md[:200])
            else:
                logger.info(
                    "✅ Phase 6 | Перевод применен (до: %s символов, после: %s символов)",
                    len(original_md),
                    len(translated_md),
                )
                logger.info("✅ Phase 6 | Первые 200 символов перевода: %s", translated_md[:200])
        else:
            logger.info("🔄 Phase 6 | TranslatorAgent пропущен (язык уже русский)")
            translated_md = original_md

        translated_document = None
        if translated_md:
            translated_document = ReadmeDocument.from_markdown(translated_md, fallback_title=readme_document.title)
        return TranslationPhaseResult(
            markdown=original_md,
            translated_markdown=translated_md,
            readme_document=readme_document,
            translated_readme_document=translated_document,
        )
