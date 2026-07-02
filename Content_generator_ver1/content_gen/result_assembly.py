"""Final result assembly for AgentFlow generation output."""

from __future__ import annotations

import base64
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .checklist import ProjectChecklist, build_project_checklist
from .config.loader import get_loaded_agent_versions
from .models.readme_document import ReadmeDocument
from .models.result import OrchestratorResult
from .models.schemas import Annotation, IntroSection, PracticeTask, ProjectSpec, TheoryPart
from .observability import build_unified_observability_report, normalize_fallback_trace_event
from .utils.markdown_display_normalizer import normalize_markdown_display_blocks
from .utils.mermaid_export import convert_mermaid_blocks
from .utils.protected_blocks import fix_common_latex_issues_in_md

logger = logging.getLogger("content_gen.result_assembly")

IntroSplitter = Callable[[str], tuple[str, str]]
TheoryPartsParser = Callable[[str], list[TheoryPart]]
PracticeTasksParser = Callable[[str], list[PracticeTask]]


@dataclass
class FinalizationResult:
    """Artifacts produced by final result assembly."""

    result: OrchestratorResult
    project_spec: ProjectSpec
    markdown: str
    readme_document: ReadmeDocument
    translated_markdown: str | None
    assets_binary: dict[str, Any]
    step_warnings: list[str]


class ResultAssembler:
    """Build ProjectSpec, report_json and export assets from the flow context."""

    def __init__(
        self,
        llm_client: Any,
        title_annotation_agent: Any,
        intro_splitter: IntroSplitter,
        theory_parts_parser: TheoryPartsParser,
        practice_tasks_parser: PracticeTasksParser,
    ) -> None:
        self.llm = llm_client
        self.title_annotation_agent = title_annotation_agent
        self.intro_splitter = intro_splitter
        self.theory_parts_parser = theory_parts_parser
        self.practice_tasks_parser = practice_tasks_parser

    def assemble(
        self,
        context: dict[str, Any],
        dataset_files: list[dict[str, Any]] | None = None,
    ) -> FinalizationResult:
        """Assemble final result without running generation agents."""
        seed = context["seed"]
        context_meta = context["context_meta"]
        context_analysis = context["context_analysis"]
        context_bundle = context.get("context_bundle")
        blueprint = context.get("blueprint")
        rubric_json = context["rubric_json"]
        task_plan = context.get("task_plan")
        warnings: list[str] = context.get("warnings", [])
        step_warnings: list[str] = []
        issues: list[Any] = context.get("issues", [])
        md = normalize_markdown_display_blocks(context["markdown"])
        title = context.get("title")
        annotation = context.get("annotation")
        intro_section = context.get("intro_section")
        theory_parts = list(context.get("theory_parts") or [])
        practice_tasks = list(context.get("practice_tasks") or [])
        target_language = str(context.get("target_language", "ru")).lower().strip()
        translated_md = context.get("translated_markdown") or (md if target_language == "ru" else None)
        if translated_md:
            translated_md = normalize_markdown_display_blocks(translated_md)

        self._log_translation_presence(translated_md, md, target_language)

        md = normalize_markdown_display_blocks(fix_common_latex_issues_in_md(md))
        translated_md, translated_assets_binary = self._prepare_translated_markdown(translated_md, md)

        assets_binary = context.get("assets_binary") or {}
        self._attach_practice_files(assets_binary, context.get("practice_tasks"))
        self._attach_dataset_files(assets_binary, translated_assets_binary, dataset_files or [])

        md = normalize_markdown_display_blocks(self._convert_original_mermaid(md, assets_binary, warnings, step_warnings))
        readme_document = self._readme_document_from_context(context, md)
        context["readme_document"] = readme_document
        title, annotation = self._ensure_title_annotation(title, annotation, seed, context_meta)
        intro_section = self._ensure_intro_section(intro_section, md)
        if not theory_parts:
            theory_parts.extend(self.theory_parts_parser(md))
        if not practice_tasks:
            practice_tasks.extend(self.practice_tasks_parser(md))
        checklist = build_project_checklist(
            project_title=title or readme_document.title,
            language=seed.language,
            readme_document=readme_document,
            practice_tasks=practice_tasks,
        )
        checklist_yml = checklist.to_yaml()
        self._attach_checklist_file(assets_binary, checklist_yml)
        context["checklist"] = checklist.model_dump(mode="json")
        context["checklist_yml"] = checklist_yml

        spec = ProjectSpec(
            language=seed.language,
            project_type=seed.project_type,
            thematic_block=seed.thematic_block,
            required_tools=seed.required_tools,
            title=title,
            annotation=annotation,
            intro=intro_section,
            theory=theory_parts,
            practice=practice_tasks,
            checklist_yml=checklist_yml,
            bonus=seed.bonus_wish,
            context=context_meta,
            toc_md=None,
        )

        practice_critic_issues = context.get("practice_critic_issues")
        section_contexts = context.get("section_contexts")
        story_map_contract = context.get("story_map_contract")
        practice_plan_contract = context.get("practice_plan_contract")
        artifact_chain_plan = context.get("artifact_chain_plan")
        evidence_specs = context.get("evidence_specs")
        methodology_revision_results = context.get("methodology_revision_results")
        methodology_resume_plan = context.get("methodology_resume_plan")
        agent_versions = get_loaded_agent_versions()
        title_en = self._build_english_title(title, md, seed, step_warnings)
        report = self._build_report(
            seed=seed,
            context_meta=context_meta,
            context_analysis=context_analysis,
            context_bundle=context_bundle,
            blueprint=blueprint,
            rubric_json=rubric_json,
            task_plan=task_plan,
            warnings=warnings,
            issues=issues,
            markdown=md,
            translated_markdown=translated_md,
            title_en=title_en,
            practice_critic_issues=practice_critic_issues,
            section_contexts=section_contexts,
            story_map_contract=story_map_contract,
            practice_plan_contract=practice_plan_contract,
            artifact_chain_plan=artifact_chain_plan,
            evidence_specs=evidence_specs,
            methodology_revision_results=methodology_revision_results,
            methodology_resume_plan=methodology_resume_plan,
            agent_versions=agent_versions,
            readme_document=readme_document,
            checklist=checklist,
            checklist_yml=checklist_yml,
            node_traces=context.get("node_traces") or [],
            llm_traces=context.get("llm_traces") or [],
            fallback_traces=context.get("fallback_traces") or [],
            compatibility_events=context.get("compatibility_events") or [],
            observability=(
                context["observability_sink"].report()
                if hasattr(context.get("observability_sink"), "report")
                else context.get("observability")
            ),
        )

        encoded_assets = self._encode_assets(assets_binary)
        if encoded_assets:
            report["assets"] = encoded_assets
        translated_encoded_assets = self._encode_assets(translated_assets_binary)
        if translated_encoded_assets:
            report["translated_assets"] = translated_encoded_assets
            logger.info(
                "Saved translated assets: %s diagrams, %s files",
                len(translated_encoded_assets.get("images", [])),
                len(translated_encoded_assets.get("files", [])),
            )

        result = OrchestratorResult(
            spec=spec,
            warnings=warnings,
            report_json=report,
            assets=assets_binary or None,
            practice_critic_issues=practice_critic_issues,
            agent_config_versions=agent_versions,
        )
        return FinalizationResult(
            result=result,
            project_spec=spec,
            markdown=md,
            readme_document=readme_document,
            translated_markdown=translated_md,
            assets_binary=assets_binary,
            step_warnings=step_warnings,
        )

    @staticmethod
    def calculate_text_stats(text: str, language: str) -> dict[str, int]:
        """Calculate report text statistics with token-counter fallback."""
        chars = len(text)
        words = len(text.split())
        lines = len(text.splitlines())
        sentences = len(re.split(r"[.!?]+", text)) - 1
        if sentences < 0:
            sentences = 0
        try:
            from utils.token_counter import count_tokens

            tokens = count_tokens(text)
        except Exception:
            tokens = chars // 4 if language == "ru" else chars // 3
        return {
            "chars": chars,
            "chars_total": chars,
            "words": words,
            "sentences": sentences,
            "lines": lines,
            "tokens": tokens,
        }

    def _prepare_translated_markdown(
        self,
        translated_md: str | None,
        original_md: str,
    ) -> tuple[str | None, dict[str, list[dict[str, Any]]]]:
        translated_assets_binary: dict[str, list[dict[str, Any]]] = {"images": [], "files": []}
        if not translated_md or translated_md == original_md:
            return translated_md, translated_assets_binary

        translated_md = normalize_markdown_display_blocks(fix_common_latex_issues_in_md(translated_md))
        try:
            translated_md, translated_image_assets = convert_mermaid_blocks(translated_md)
            if translated_image_assets:
                translated_assets_binary["images"] = translated_image_assets
                logger.info("Generated %s translated Mermaid images", len(translated_image_assets))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Translated Mermaid conversion failed: %s", exc)
        return translated_md, translated_assets_binary

    def _convert_original_mermaid(
        self,
        markdown: str,
        assets_binary: dict[str, Any],
        warnings: list[str],
        step_warnings: list[str],
    ) -> str:
        try:
            markdown, image_assets = convert_mermaid_blocks(markdown)
            if image_assets:
                assets_binary["images"] = image_assets
        except Exception as exc:  # noqa: BLE001
            warn = f"⚠️ Ошибка конвертации Mermaid диаграмм: {exc}"
            logger.warning(warn)
            warnings.append(warn)
            step_warnings.append(warn)
        return markdown

    def _ensure_title_annotation(
        self,
        title: str | None,
        annotation: Annotation | None,
        seed: Any,
        context_meta: Any,
    ) -> tuple[str, Annotation]:
        if title and annotation:
            return title, annotation
        generated = self.title_annotation_agent.generate(seed, context_meta)
        title = title or generated.title
        annotation = annotation or Annotation(text=generated.annotation.text, chars=len(generated.annotation.text))
        return title, annotation

    def _ensure_intro_section(self, intro_section: IntroSection | None, markdown: str) -> IntroSection:
        if intro_section is not None:
            return intro_section
        intro_text = ""
        instr_text = ""
        try:
            intro_text, instr_text = self.intro_splitter(markdown)
        except Exception:
            pass
        return IntroSection(intro_text=intro_text, instruction_text=instr_text)

    def _build_english_title(
        self,
        title: str,
        markdown: str,
        seed: Any,
        step_warnings: list[str],
    ) -> str:
        h1_match = re.search(r"^#\s+(.+)$", markdown, re.M)
        title_ru = title or (h1_match.group(1).strip() if h1_match else "")
        if not title_ru:
            return ""
        try:
            translation_prompt = (
                "Переведи следующий заголовок учебного проекта на английский язык. \n"
                "Заголовок должен быть кратким (1-4 слова), без артиклей в начале.\n\n"
                f"Заголовок на русском: {title_ru}\n\n"
                "Переведи только заголовок, без дополнительных комментариев, без символа #."
            )
            title_en_raw = self.llm.complete(
                system=(
                    "Ты профессиональный переводчик технических документов. "
                    "Переводи заголовки кратко и точно, сохраняя смысл."
                ),
                user=translation_prompt,
                temperature=0.2,
            ).strip()
            title_en_raw = re.sub(r"^#+\s*", "", title_en_raw)
            title_en_raw = re.sub(r'["\'«»]', "", title_en_raw).strip()
            words = [word.capitalize() for word in title_en_raw.split() if word and word.strip()]
            return "".join(words)[:50]
        except Exception as exc:  # noqa: BLE001
            warn = f"⚠️ Ошибка при переводе заголовка: {exc}"
            logger.warning(warn)
            step_warnings.append(warn)
            return "".join(word.capitalize() for word in title_ru.split() if word)[:50]

    def _build_report(
        self,
        *,
        seed: Any,
        context_meta: Any,
        context_analysis: Any,
        context_bundle: Any,
        blueprint: Any,
        rubric_json: dict[str, Any],
        task_plan: Any,
        warnings: list[str],
        issues: list[Any],
        markdown: str,
        translated_markdown: str | None,
        title_en: str,
        practice_critic_issues: list[dict[str, Any]] | None,
        section_contexts: dict[str, Any] | None,
        story_map_contract: Any,
        practice_plan_contract: Any,
        artifact_chain_plan: Any,
        evidence_specs: Any,
        methodology_revision_results: Any,
        methodology_resume_plan: Any,
        agent_versions: dict[str, str],
        readme_document: ReadmeDocument,
        checklist: ProjectChecklist,
        checklist_yml: str,
        node_traces: list[dict[str, Any]],
        llm_traces: list[dict[str, Any]],
        fallback_traces: list[dict[str, Any]],
        compatibility_events: list[dict[str, Any]],
        observability: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        observability_report = observability or build_unified_observability_report(
            run_id=None,
            user_id=None,
            node_traces=node_traces,
            llm_traces=llm_traces,
            fallback_traces=fallback_traces,
            compatibility_events=compatibility_events,
        )
        normalized_fallback_traces = [
            normalize_fallback_trace_event(event).model_dump(mode="json")
            for event in fallback_traces
        ]
        report = {
            "language": seed.language,
            "warnings": warnings,
            "context": context_meta.model_dump(),
            "context_bundle": context_bundle.model_dump() if context_bundle else None,
            "blueprint": blueprint.model_dump() if blueprint else None,
            "title_en": title_en,
            "context_analysis": self._serialize_context_analysis(context_analysis),
            "issues": issues,
            "markdown": markdown,
            "translated_markdown": translated_markdown if translated_markdown != markdown else None,
            "rubric": rubric_json,
            "section_contexts": self._serialize_report_value(section_contexts),
            "story_map_contract": self._serialize_report_value(story_map_contract),
            "practice_plan_contract": self._serialize_report_value(practice_plan_contract),
            "artifact_chain_plan": self._serialize_report_value(artifact_chain_plan),
            "evidence_specs": self._serialize_report_value(evidence_specs),
            "methodology_revision_results": self._serialize_report_value(methodology_revision_results),
            "methodology_resume_plan": self._serialize_report_value(methodology_resume_plan),
            "readme_document": readme_document.model_dump(mode="json"),
            "checklist": checklist.model_dump(mode="json"),
            "checklist_yml": checklist_yml,
            "node_traces": self._serialize_report_value(node_traces),
            "llm_traces": self._serialize_report_value(llm_traces),
            "fallback_traces": self._serialize_report_value(normalized_fallback_traces),
            "compatibility_events": self._serialize_report_value(compatibility_events),
            "observability": self._serialize_report_value(observability_report),
            "text_stats": self.calculate_text_stats(markdown, seed.language),
            "practice_critic_issues": practice_critic_issues,
            "agent_config_versions": agent_versions,
        }
        if task_plan:
            report["task_plan"] = task_plan.as_dict()
        return report

    @staticmethod
    def _readme_document_from_context(context: dict[str, Any], markdown: str) -> ReadmeDocument:
        """Use a matching upstream typed README document; reparse stale documents."""
        document = ReadmeDocument.from_value(
            context.get("readme_document"),
            fallback_markdown=markdown,
        )
        if ResultAssembler._same_markdown_payload(document.to_markdown(), markdown):
            return document
        return ReadmeDocument.from_markdown(markdown)

    @staticmethod
    def _same_markdown_payload(left: str, right: str) -> bool:
        """Compare Markdown payloads while ignoring renderer-only whitespace."""
        left_normalized = re.sub(r"\s+", " ", left or "").strip()
        right_normalized = re.sub(r"\s+", " ", right or "").strip()
        return left_normalized == right_normalized

    @classmethod
    def _serialize_report_value(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, bytes):
            return f"<bytes:{len(value)}>"
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, list):
            return [cls._serialize_report_value(item) for item in value]
        if isinstance(value, tuple):
            return [cls._serialize_report_value(item) for item in value]
        if isinstance(value, dict):
            return {str(key): cls._serialize_report_value(item) for key, item in value.items()}
        if hasattr(value, "model_dump"):
            return cls._serialize_report_value(value.model_dump())
        if hasattr(value, "as_dict"):
            return cls._serialize_report_value(value.as_dict())
        if hasattr(value, "__dict__"):
            return cls._serialize_report_value(value.__dict__)
        return str(value)

    @staticmethod
    def _serialize_context_analysis(context_analysis: Any) -> dict[str, Any]:
        return {
            "is_first_project": context_analysis.is_first_project,
            "context_summary": context_analysis.context_summary,
            "narrative_anchor": context_analysis.narrative_anchor,
            "similar_projects_count": len(context_analysis.similar_projects),
            "skills_alignment": context_analysis.skills_alignment,
            "learning_outcomes_alignment": context_analysis.learning_outcomes_alignment,
            "tools_alignment": context_analysis.tools_alignment,
            "audience_level_match": context_analysis.audience_level_match,
            "metrics": context_analysis.metrics,
            "similar_projects": [
                {
                    "code": project.code,
                    "code_name": project.code_name,
                    "title": project.title,
                    "order": project.order,
                    "skills": project.skills,
                }
                for project in context_analysis.similar_projects[:10]
            ],
        }

    def _attach_practice_files(self, assets_binary: dict[str, Any], practice_tasks: list[PracticeTask] | None) -> None:
        if practice_tasks:
            practice_files = self._generate_practice_artifact_files(practice_tasks)
            if practice_files:
                assets_binary.setdefault("files", []).extend(practice_files)

    @staticmethod
    def _attach_dataset_files(
        assets_binary: dict[str, Any],
        translated_assets_binary: dict[str, list[dict[str, Any]]],
        dataset_files: list[dict[str, Any]],
    ) -> None:
        if not dataset_files:
            return
        assets_binary.setdefault("files", []).extend(dataset_files)
        logger.info("Added %s dataset files to assets", len(dataset_files))
        translated_assets_binary["files"] = assets_binary.get("files", [])

    @staticmethod
    def _attach_checklist_file(assets_binary: dict[str, Any], checklist_yml: str) -> None:
        if not checklist_yml.strip():
            return
        files = assets_binary.setdefault("files", [])
        existing_paths = {
            str(asset.get("path") or "").replace("\\", "/").lower()
            for asset in files
            if isinstance(asset, dict)
        }
        if "check-list.yml" in existing_paths or "checklist.yml" in existing_paths:
            return
        files.append({"path": "check-list.yml", "data": checklist_yml.encode("utf-8")})

    @staticmethod
    def _encode_assets(assets_binary: dict[str, Any]) -> dict[str, Any]:
        encoded_assets: dict[str, Any] = {}
        if assets_binary.get("images"):
            encoded_assets["images"] = [
                {"name": asset["name"], "data": base64.b64encode(asset["data"]).decode("utf-8")}
                for asset in assets_binary["images"]
                if asset.get("name") and asset.get("data")
            ]
        if assets_binary.get("files"):
            files: list[dict[str, Any]] = []
            for asset in assets_binary["files"]:
                if not asset.get("path") or not asset.get("data"):
                    continue
                encoded = {
                    "path": asset["path"],
                    "data": base64.b64encode(asset["data"]).decode("utf-8"),
                }
                if asset.get("evidence_spec"):
                    encoded["evidence_spec"] = ResultAssembler._serialize_report_value(asset["evidence_spec"])
                files.append(encoded)
            encoded_assets["files"] = files
        return encoded_assets

    @staticmethod
    def _generate_practice_artifact_files(practice_tasks: list[PracticeTask] | None) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        if not practice_tasks:
            return files

        seen_paths: set[str] = set()
        for idx, task in enumerate(practice_tasks, 1):
            raw_path = (getattr(task, "artifact_location", "") or "").strip()
            if not raw_path or raw_path.startswith(("http://", "https://")):
                continue

            safe_path = ResultAssembler._sanitize_artifact_path(raw_path, idx)
            if not safe_path or safe_path in seen_paths:
                continue

            seen_paths.add(safe_path)
            expected = (getattr(task, "expected_artifact", "") or "").strip()
            input_data = (getattr(task, "input_data", "") or "").strip()
            goal = (getattr(task, "goal", "") or "").strip()
            approach = [item.strip() for item in (getattr(task, "approach_bullets", []) or []) if item.strip()]
            criteria = [item.strip() for item in (getattr(task, "p2p_criteria", []) or []) if item.strip()]
            content_lines = [
                f"# Артефакт задачи {idx}: {task.title}",
                "",
                "Этот файл — рабочий шаблон артефакта, а не готовое решение.",
                "Заполни его по ходу выполнения задачи и оставь только проверяемый результат.",
            ]
            if input_data:
                content_lines.extend(["", "## Входные данные", input_data])
            if goal:
                content_lines.extend(["", "## Цель артефакта", goal])
            if expected:
                content_lines.extend(["", "## Ожидаемый результат", expected])
            if approach:
                content_lines.extend(["", "## Рабочие шаги"])
                content_lines.extend(f"- [ ] {item}" for item in approach)
            content_lines.extend([
                "",
                "## Итоговый артефакт",
                "",
                "<!-- Заполни этот раздел своим результатом: таблицей, списком, схемой или кратким Markdown-документом по условию задачи. -->",
            ])
            if criteria:
                content_lines.extend(["", "## Самопроверка"])
                content_lines.extend(f"- [ ] {item}" for item in criteria)
            content = "\n".join(content_lines) + "\n"
            files.append({"path": safe_path, "data": content.encode("utf-8")})

        return files

    @staticmethod
    def _sanitize_artifact_path(path: str, idx: int) -> str:
        cleaned = re.sub(r"^[a-zA-Z]+://", "", path).strip()
        cleaned = cleaned.replace("\\", "/")
        parts = [part for part in cleaned.split("/") if part and part not in (".", "..")]
        if not parts:
            parts = [f"artifacts/task-{idx:02d}", "README.md"]
        if len(parts) == 1:
            parts.append("README.md")
        return "/".join(parts)

    @staticmethod
    def _log_translation_presence(translated_md: str | None, original_md: str, target_language: str) -> None:
        if translated_md:
            logger.info(
                "Finalize received translated markdown: chars=%s differs=%s",
                len(translated_md),
                translated_md != original_md,
            )
        elif target_language != "ru":
            logger.warning("Finalize has no translated_markdown for target language %s", target_language)
