"""Build ProjectSeed instances from current project metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from content_gen.models.schemas import ProjectSeed


@dataclass(frozen=True)
class ProjectSeedProviderResult:
    """Resolved ProjectSeed with provenance for logs and diagnostics."""

    seed: ProjectSeed
    source: str
    warnings: tuple[str, ...] = ()

    @property
    def has_structured_project_source(self) -> bool:
        """Whether seed came from current/cached project metadata, not a minimal fallback."""
        return self.source != "fallback.minimal"


class ProjectSeedProvider:
    """Resolve a ProjectSeed for generation-like workflows."""

    @classmethod
    def build_for_regeneration(
        cls,
        *,
        language: str,
        project_seed: Any = None,
        curriculum_project: Any = None,
        cached_result: dict[str, Any] | None = None,
    ) -> ProjectSeedProviderResult:
        """Build seed for regeneration, preferring current curriculum/project payloads."""
        warnings: list[str] = []

        candidates = (
            ("request.project_seed", lambda: cls._from_seed_payload(project_seed, language)),
            ("request.curriculum_project", lambda: cls._from_curriculum_project(curriculum_project, language)),
            ("cache.project_seed_payload", lambda: cls._from_seed_payload((cached_result or {}).get("project_seed_payload"), language)),
            ("cache.project_seed", lambda: cls._from_seed_payload((cached_result or {}).get("project_seed"), language)),
            ("cache.result.spec", lambda: cls._from_cached_spec(cached_result, language)),
            ("cache.report_json", lambda: cls._from_seed_payload((cached_result or {}).get("report_json"), language)),
        )

        for source, builder in candidates:
            try:
                seed = builder()
            except ValidationError as exc:
                warnings.append(f"{source}: validation failed: {exc}")
                continue
            except Exception as exc:
                warnings.append(f"{source}: build failed: {exc}")
                continue
            if seed is not None:
                return ProjectSeedProviderResult(seed=seed, source=source, warnings=tuple(warnings))

        return ProjectSeedProviderResult(
            seed=cls._minimal_seed(language),
            source="fallback.minimal",
            warnings=tuple(warnings),
        )

    @classmethod
    def _from_seed_payload(cls, payload: Any, language: str) -> ProjectSeed | None:
        if payload is None:
            return None
        if isinstance(payload, ProjectSeed):
            return payload
        if not isinstance(payload, dict):
            return None

        data = dict(payload)
        cls._apply_seed_aliases(data, language)

        if not cls._has_meaningful_project_data(data):
            return None

        return ProjectSeed(**data)

    @classmethod
    def _from_curriculum_project(cls, payload: Any, language: str) -> ProjectSeed | None:
        if not isinstance(payload, dict) or not payload:
            return None

        project = payload.get("project") if isinstance(payload.get("project"), dict) else payload
        block = payload.get("block") if isinstance(payload.get("block"), dict) else {}

        direction = (
            payload.get("direction")
            or block.get("code")
            or project.get("direction")
            or ""
        )
        thematic_block = (
            payload.get("thematic_block")
            or block.get("name")
            or project.get("block_name")
            or block.get("code")
            or ""
        )
        project_format = project.get("project_type") or project.get("format") or payload.get("project_type")

        data: dict[str, Any] = {
            "language": payload.get("language") or project.get("language") or language,
            "project_type": cls._project_type(project_format, project.get("group_size")),
            "direction": direction,
            "thematic_block": thematic_block,
            "audience_level": payload.get("audience_level") or project.get("audience_level") or "base",
            "required_tools": cls._as_list(project.get("required_tools")),
            "required_software": cls._as_list(project.get("required_software")),
            "project_content_type": payload.get("project_content_type") or project.get("project_content_type"),
            "title_seed": project.get("title_seed") or project.get("title") or "",
            "project_description": (
                project.get("project_description")
                or project.get("description")
                or project.get("title_seed")
                or project.get("title")
                or "Перегенерированный контент"
            ),
            "learning_outcomes": cls._as_list(project.get("learning_outcomes")),
            "skills": cls._as_list(project.get("skills")),
            "tasks_count": cls._optional_int(project.get("tasks_count")),
            "group_size": cls._optional_int(project.get("group_size")),
            "storytelling_type": project.get("storytelling_type") or payload.get("storytelling_type"),
            "sjm": project.get("sjm") or project.get("storytelling"),
            "platform_name": project.get("platform_name"),
            "gitlab_link": project.get("gitlab_link"),
            "workload_hours": project.get("workload_hours"),
            "workload_days": project.get("workload_days"),
            "xp_reward": project.get("xp_reward") or project.get("xp"),
            "additional_materials": project.get("additional_materials"),
            "expert_notes": project.get("expert_notes"),
            "curriculum_context": payload.get("curriculum_context") or payload.get("context"),
        }

        return ProjectSeed(**{k: v for k, v in data.items() if v not in (None, "")})

    @classmethod
    def _from_cached_spec(cls, cached_result: dict[str, Any] | None, language: str) -> ProjectSeed | None:
        if not cached_result:
            return None
        result = cached_result.get("result")
        spec = getattr(result, "spec", None) if result is not None else None
        if spec is None:
            return None
        if isinstance(spec, ProjectSeed):
            return spec
        if hasattr(spec, "model_dump"):
            return cls._from_seed_payload(spec.model_dump(), language)

        data = {
            "language": getattr(spec, "language", language),
            "project_type": getattr(spec, "project_type", "individual"),
            "direction": getattr(spec, "direction", ""),
            "thematic_block": getattr(spec, "thematic_block", ""),
            "audience_level": getattr(spec, "audience_level", "base"),
            "required_tools": list(getattr(spec, "required_tools", []) or []),
            "required_software": list(getattr(spec, "required_software", []) or []),
            "project_content_type": getattr(spec, "project_content_type", None),
            "title_seed": getattr(spec, "title_seed", ""),
            "project_description": getattr(spec, "project_description", "Перегенерированный контент"),
            "learning_outcomes": list(getattr(spec, "learning_outcomes", []) or []),
            "skills": list(getattr(spec, "skills", []) or []),
            "tasks_count": getattr(spec, "tasks_count", None),
            "storytelling_type": getattr(spec, "storytelling_type", "sjm"),
            "sjm": getattr(spec, "sjm", None),
        }
        return ProjectSeed(**data)

    @staticmethod
    def _apply_seed_aliases(data: dict[str, Any], language: str) -> None:
        data.setdefault("language", language)
        data.setdefault("project_type", "individual")
        data.setdefault("audience_level", "base")
        if not data.get("project_description"):
            data["project_description"] = data.get("description") or data.get("title_seed") or data.get("title") or "Перегенерированный контент"
        if not data.get("title_seed") and data.get("title"):
            data["title_seed"] = data["title"]
        data["required_tools"] = ProjectSeedProvider._as_list(data.get("required_tools"))
        data["required_software"] = ProjectSeedProvider._as_list(data.get("required_software"))
        data["learning_outcomes"] = ProjectSeedProvider._as_list(data.get("learning_outcomes"))
        data["skills"] = ProjectSeedProvider._as_list(data.get("skills"))

    @staticmethod
    def _has_meaningful_project_data(data: dict[str, Any]) -> bool:
        keys = (
            "title_seed",
            "project_description",
            "learning_outcomes",
            "skills",
            "required_tools",
            "required_software",
            "curriculum_context",
            "platform_name",
            "expert_notes",
        )
        return any(bool(data.get(key)) for key in keys)

    @staticmethod
    def _minimal_seed(language: str) -> ProjectSeed:
        return ProjectSeed(
            language=language,
            project_type="individual",
            thematic_block="",
            project_description="Перегенерированный контент",
            learning_outcomes=[],
            skills=[],
            tasks_count=3,
        )

    @staticmethod
    def _project_type(value: Any, group_size: Any = None) -> str:
        text = str(value or "").strip().lower()
        if text in {"group", "team", "командный", "групповой"} or ProjectSeedProvider._optional_int(group_size):
            return "group"
        return "individual"

    @staticmethod
    def _as_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_parts = value.replace("\r\n", "\n").replace("\r", "\n").replace(";", "\n").split("\n")
            if len(raw_parts) == 1 and "," in value:
                raw_parts = value.split(",")
            return [part.strip(" \t-•") for part in raw_parts if part.strip(" \t-•")]
        if isinstance(value, (list, tuple, set)):
            result: list[str] = []
            for item in value:
                if isinstance(item, str):
                    result.extend(ProjectSeedProvider._as_list(item))
                elif item is not None:
                    result.append(str(item).strip())
            return [item for item in result if item]
        return [str(value).strip()] if str(value).strip() else []

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
