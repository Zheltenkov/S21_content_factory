"""Application service for README improvement workflows."""

from __future__ import annotations

import asyncio
import base64
import io
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from api.db.generation_results_db import get_generation_result, get_report_by_request_id
from api.db.logging_db import write_log_async
from api.services.archive_builder import (
    add_assets_to_zip,
    build_readme_filename,
    merge_assets,
    transliterate_filename,
)
from api.utils.improvement_cache import (
    generate_diff,
    get_extract_request_id,
    get_generation_request_id,
    get_improved_readme,
    get_original_readme,
    link_generation_request,
    store_extracted_data,
    store_improved_readme,
    store_original_readme,
)
from api.utils.logger import get_logger
from api.utils.result_cache import get_generation_error, get_generation_status, get_result, set_generation_status
from content_gen.llm.factory import create_llm_client
from content_gen.models.schemas import ProjectSeed
from content_gen.project_seed_provider import ProjectSeedProvider
from content_gen.reverse_extraction.models import ClassificationResult, PartialProjectSeed
from content_gen.reverse_extraction.orchestrator import ReverseExtractionOrchestrator

logger = get_logger("readme_improvement")


@dataclass(frozen=True)
class ExtractForImprovementCommand:
    """Input contract for extracting editable seed data from README/project metadata."""

    request_id: str
    user_id: str
    readme_text: str
    learning_outcomes: list[str] | None = None
    curriculum_project: dict[str, Any] | None = None
    curriculum_context: dict[str, Any] | None = None


@dataclass(frozen=True)
class ExtractForImprovementResult:
    """Extracted data returned to the UI editor."""

    request_id: str
    status: str
    partial_seed: dict[str, Any]
    classification: dict[str, Any]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class GenerateImprovedCommand:
    """Command to start README improvement generation."""

    extract_request_id: str
    generation_request_id: str
    user_id: str
    seed: ProjectSeed


@dataclass(frozen=True)
class GenerateImprovedResult:
    """Async generation start response."""

    request_id: str
    status: str
    generation_request_id: str


@dataclass(frozen=True)
class ImprovedReadmeArchive:
    """ZIP archive with improved README and generated assets."""

    filename: str
    data: bytes


class ReadmeImprovementNotFoundError(Exception):
    """Raised when an improvement workflow resource is missing."""


class ReadmeImprovementService:
    """Coordinate README improvement extraction, generation and downloads."""

    def __init__(
        self,
        *,
        llm_factory: Callable[[], Any] | None = None,
        log_writer: Callable[..., Any] = write_log_async,
    ) -> None:
        self._llm_factory = llm_factory or (
            lambda: create_llm_client(default_role="planner", enable_cache=True, enable_batching=True)
        )
        self._log_writer = log_writer

    async def extract_for_improvement(
        self,
        command: ExtractForImprovementCommand,
    ) -> ExtractForImprovementResult:
        """Extract editable project seed data from curriculum payload or README."""
        logger.info("📄 Начало извлечения данных для улучшения README (request_id=%s)", command.request_id)
        await self._log_writer(
            request_id=command.request_id,
            level="INFO",
            message="Начало извлечения данных для улучшения README",
            user_id=command.user_id,
            phase="improvement_extract",
            metadata={
                "readme_length": len(command.readme_text),
                "has_learning_outcomes": bool(command.learning_outcomes),
            },
        )

        store_original_readme(command.request_id, command.readme_text, user_id=command.user_id)

        if command.curriculum_project and isinstance(command.curriculum_project, dict):
            partial_seed, classification = self._build_from_curriculum_project(command)
            store_extracted_data(command.request_id, partial_seed, classification)
            logger.info(
                "✅ Данные для улучшения взяты из УП: title='%s', block=%s",
                partial_seed.title_seed,
                classification.thematic_block or classification.thematic_block_suggested,
            )
            return ExtractForImprovementResult(
                request_id=command.request_id,
                status="completed",
                partial_seed=partial_seed.model_dump(),
                classification=classification.model_dump(),
                metadata={"source": "curriculum"},
            )

        llm_client = self._llm_factory()
        configure_context = getattr(llm_client, "configure_run_context", None)
        if callable(configure_context):
            configure_context(user_id=command.user_id, run_id=command.request_id)
        orchestrator = ReverseExtractionOrchestrator(llm_client)
        partial_seed, classification, _normalized_readme, metadata = await asyncio.to_thread(
            orchestrator.extract_data_only,
            command.readme_text,
        )
        store_extracted_data(command.request_id, partial_seed, classification)
        logger.info(
            "✅ Извлечение данных завершено: title='%s', thematic_block=%s",
            partial_seed.title_seed,
            classification.thematic_block or classification.thematic_block_suggested,
        )
        await self._log_writer(
            request_id=command.request_id,
            level="INFO",
            message="Извлечение данных для улучшения завершено успешно",
            user_id=command.user_id,
            phase="improvement_extract_complete",
            metadata={
                "title_seed": partial_seed.title_seed,
                "tasks_count": partial_seed.tasks_count,
                "skills_count": len(partial_seed.skills),
                "learning_outcomes_count": len(partial_seed.learning_outcomes),
                "thematic_block": classification.thematic_block or classification.thematic_block_suggested,
            },
        )
        return ExtractForImprovementResult(
            request_id=command.request_id,
            status="completed",
            partial_seed=partial_seed.model_dump(),
            classification=classification.model_dump(),
            metadata=metadata,
        )

    async def generate_improved_readme(self, command: GenerateImprovedCommand) -> GenerateImprovedResult:
        """Start background generation for improved README."""
        logger.info(
            "🚀 Начало генерации улучшенного README (extract_request_id=%s, generation_request_id=%s)",
            command.extract_request_id,
            command.generation_request_id,
        )
        original_readme = get_original_readme(command.extract_request_id)
        if not original_readme:
            raise ReadmeImprovementNotFoundError(
                f"Исходный README не найден для request_id={command.extract_request_id}. Возможно, данные устарели."
            )

        await self._log_writer(
            request_id=command.generation_request_id,
            level="INFO",
            message="Начало генерации улучшенного README",
            user_id=command.user_id,
            phase="improvement_generate",
            metadata={
                "extract_request_id": command.extract_request_id,
                "thematic_block": command.seed.thematic_block,
                "tasks_count": command.seed.tasks_count,
            },
        )
        set_generation_status(command.generation_request_id, "pending")
        link_generation_request(command.extract_request_id, command.generation_request_id)

        from api.routers.generation import _run_generation_background

        asyncio.create_task(
            _run_generation_background(
                request_id=command.generation_request_id,
                user_id=command.user_id,
                project_seed_dict=command.seed.model_dump(),
                track_paths=[],
                temp_dir=None,
            )
        )
        logger.info("✅ Генерация улучшенного README запущена (request_id=%s)", command.generation_request_id)
        return GenerateImprovedResult(
            request_id=command.extract_request_id,
            status="pending",
            generation_request_id=command.generation_request_id,
        )

    def get_diff(self, request_id: str) -> dict[str, Any]:
        """Return diff between original and improved README."""
        original = get_original_readme(request_id)
        if not original:
            raise ReadmeImprovementNotFoundError(f"Исходный README не найден для request_id={request_id}")

        generation_request_id = get_generation_request_id(request_id)
        if not generation_request_id:
            raise ReadmeImprovementNotFoundError(f"Генерация не найдена для extract_request_id={request_id}")

        improved = get_improved_readme(generation_request_id)
        if not improved:
            raise ReadmeImprovementNotFoundError("Улучшенный README еще не готов или не найден")

        store_improved_readme(request_id, improved)
        diff_data = generate_diff(request_id)
        if not diff_data:
            raise ReadmeImprovementNotFoundError("Не удалось сгенерировать diff")
        return diff_data

    def get_generation_status(self, generation_request_id: str) -> dict[str, Any]:
        """Return improvement generation status and completed result payload."""
        status = get_generation_status(generation_request_id)
        if not status:
            raise ReadmeImprovementNotFoundError(f"Генерация с request_id={generation_request_id} не найдена")

        result_data = None
        if status == "completed":
            result = get_result(generation_request_id)
            if result:
                result_data = self._build_status_result(generation_request_id, result)

        response: dict[str, Any] = {"status": status, "result": result_data}
        if status == "failed":
            error = get_generation_error(generation_request_id)
            if error:
                response["error"] = error
        return response

    def build_download_archive(self, generation_request_id: str) -> ImprovedReadmeArchive:
        """Build ZIP archive for generated improved README."""
        cached = get_result(generation_request_id)
        db_result = get_generation_result(generation_request_id)
        if not cached and not db_result:
            raise ReadmeImprovementNotFoundError("Результат генерации улучшенного README не найден")

        report_json = get_report_by_request_id(generation_request_id) or (cached or {}).get("report_json", {})
        improved_markdown = (cached or {}).get("markdown") or (db_result.markdown if db_result else "")
        if not improved_markdown:
            raise ReadmeImprovementNotFoundError("Улучшенный README не найден в результатах")

        original_name = build_readme_filename(report_json)
        improved_name = f"regen_{original_name}"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(improved_name, improved_markdown)
            logger.info("✅ Улучшенный README добавлен в архив: %s", improved_name)
            assets = merge_assets((report_json or {}).get("assets"), (cached or {}).get("assets"))
            image_count, file_count = add_assets_to_zip(archive, assets, logger)
            logger.info("📦 В архив улучшенного README добавлены assets: images=%s, files=%s", image_count, file_count)

        zip_data = buf.getvalue()
        buf.close()
        zip_filename = transliterate_filename(improved_name.replace(".md", ".zip"))
        logger.info("✅ ZIP архив с улучшенным README создан: размер=%s байт", len(zip_data))
        return ImprovedReadmeArchive(filename=zip_filename, data=zip_data)

    @staticmethod
    def _build_from_curriculum_project(
        command: ExtractForImprovementCommand,
    ) -> tuple[PartialProjectSeed, ClassificationResult]:
        block = command.curriculum_project.get("block") or {}
        project = command.curriculum_project.get("project") or {}
        block_code = block.get("code") or block.get("name") or ""
        project_title = project.get("title") or ""
        project_description = project.get("description") or ""
        project_lo = ProjectSeedProvider._as_list(project.get("learning_outcomes") or command.learning_outcomes or [])
        project_skills = ProjectSeedProvider._as_list(project.get("skills") or [])
        project_format = (project.get("format") or "individual").lower()
        required_software = project.get("required_software")
        required_tools = ProjectSeedProvider._as_list(required_software or project.get("required_tools") or [])

        partial_seed = PartialProjectSeed(
            title_seed=project_title,
            project_description=project_description or project_title,
            learning_outcomes=project_lo,
            skills=project_skills,
            required_tools=required_tools,
            tasks_count=project.get("tasks_count"),
            sjm=project.get("sjm"),
        )
        classification = ClassificationResult(
            language=command.curriculum_project.get("language") or "ru",
            thematic_block=block_code or None,
            thematic_block_suggested=block_code or None,
            thematic_block_name=block.get("name"),
            audience_level=command.curriculum_project.get("audience_level") or "base",
            project_type="group" if project_format == "group" else "individual",
        )
        return partial_seed, classification

    def _build_status_result(self, generation_request_id: str, result: dict[str, Any]) -> dict[str, Any]:
        report_json = result.get("report_json", {})
        markdown = report_json.get("markdown", "")
        if markdown:
            store_improved_readme(generation_request_id, markdown)
            extract_request_id = get_extract_request_id(generation_request_id)
            if extract_request_id:
                store_improved_readme(extract_request_id, markdown)

        rubric = result.get("rubric") or report_json.get("rubric")
        logger.debug(
            "🔍 Проверка rubric для generation_request_id=%s: result.rubric=%s, report_json.rubric=%s, final_rubric=%s",
            generation_request_id,
            result.get("rubric") is not None,
            report_json.get("rubric") is not None,
            rubric is not None,
        )
        assets = report_json.get("assets") or {}
        if not assets and result.get("assets"):
            assets = _assets_to_base64(result.get("assets"))

        return {
            "markdown": markdown,
            "rubric": rubric,
            "text_stats": report_json.get("text_stats"),
            "assets": assets,
        }


def _assets_to_base64(assets_binary: dict[str, Any]) -> dict[str, Any]:
    """Convert in-memory binary assets into JSON-safe base64 payloads."""
    assets: dict[str, Any] = {}
    if assets_binary.get("images"):
        assets["images"] = [
            {
                "name": img.get("name"),
                "data": base64.b64encode(img["data"]).decode("utf-8")
                if isinstance(img.get("data"), bytes) else img.get("data"),
            }
            for img in assets_binary["images"]
            if img.get("name") and img.get("data")
        ]
    if assets_binary.get("files"):
        assets["files"] = [
            {
                "path": file.get("path"),
                "data": base64.b64encode(file["data"]).decode("utf-8")
                if isinstance(file.get("data"), bytes) else file.get("data"),
            }
            for file in assets_binary["files"]
            if file.get("path") and file.get("data")
        ]
    return assets
