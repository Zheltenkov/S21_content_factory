from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from content_factory.api.services import curriculum_generation_contract as contract
from content_factory.api.services.generation_start_service import GenerationStartService


class _JsonRequest:
    def __init__(self, seed: dict[str, Any]) -> None:
        self.headers = {"content-type": "application/json"}
        self._seed = seed

    async def json(self) -> dict[str, Any]:
        return {"seed": self._seed}


class _WorkflowService:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.failed: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> None:
        self.created.append(kwargs)

    def mark_failed(self, **kwargs: Any) -> None:
        self.failed.append(kwargs)


class _Logger:
    def info(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def error(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _session_factory() -> sessionmaker:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("ATTACH DATABASE ':memory:' AS catalog")
        conn.exec_driver_sql(
            """
            CREATE TABLE catalog.profile_brief (
                id INTEGER PRIMARY KEY,
                raw_text TEXT NOT NULL,
                role TEXT,
                seniority TEXT,
                domain TEXT
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE catalog.curriculum_plan (
                id INTEGER PRIMARY KEY,
                brief_id INTEGER,
                source_policy TEXT NOT NULL DEFAULT 'accepted_only',
                status TEXT NOT NULL DEFAULT 'built',
                title TEXT,
                audience_level TEXT,
                total_blocks INTEGER NOT NULL DEFAULT 0,
                total_projects INTEGER NOT NULL DEFAULT 0,
                total_hours REAL NOT NULL DEFAULT 0,
                total_days REAL NOT NULL DEFAULT 0,
                total_xp INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                profile_id INTEGER,
                direction TEXT NOT NULL DEFAULT '',
                version TEXT NOT NULL DEFAULT 'v1',
                author_ref TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE catalog.curriculum_plan_row (
                id INTEGER PRIMARY KEY,
                plan_id INTEGER NOT NULL,
                block_index INTEGER NOT NULL,
                row_number INTEGER NOT NULL,
                project_index_in_block INTEGER NOT NULL,
                block_title TEXT,
                block_goal TEXT,
                project_name TEXT NOT NULL,
                project_summary TEXT,
                outcomes_know TEXT,
                outcomes_can TEXT,
                outcomes_skills TEXT,
                learning_outcomes TEXT,
                skills_list TEXT,
                audience_level TEXT,
                required_tools TEXT,
                materials TEXT,
                storytelling TEXT,
                delivery_format TEXT,
                group_size TEXT,
                effort_hours REAL,
                effort_days REAL,
                cumulative_days REAL,
                xp INTEGER,
                platform_project_name TEXT,
                artifact_links TEXT,
                completion_percent REAL,
                p2p_checks INTEGER,
                weighted_skills TEXT,
                validation_criteria TEXT
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE catalog.review_queue (
                id INTEGER PRIMARY KEY,
                entity_type TEXT NOT NULL,
                entity_id INTEGER,
                source_ref TEXT,
                reason_code TEXT NOT NULL,
                severity TEXT NOT NULL,
                details TEXT,
                status TEXT NOT NULL DEFAULT 'open'
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE catalog.curriculum_artifact_template_proposal (
                id INTEGER PRIMARY KEY,
                brief_id INTEGER NOT NULL,
                plan_id INTEGER,
                status TEXT NOT NULL DEFAULT 'open'
            )
            """
        )
        conn.exec_driver_sql(
            """
            INSERT INTO catalog.profile_brief(id, raw_text, role, seniority, domain)
            VALUES (1, 'Нужна программа по анализу данных', 'Data Analyst', 'junior', 'analytics')
            """
        )
        conn.exec_driver_sql(
            """
            INSERT INTO catalog.curriculum_plan(
                id, brief_id, source_policy, status, title, audience_level,
                total_blocks, total_projects, total_hours, total_days, total_xp,
                payload_json, created_at, updated_at, direction, version, metadata_json
            )
            VALUES (
                10, 1, 'accepted_only', 'built', 'Data Analytics UP', 'Начальный',
                1, 1, 12, 4, 120, '{}', '2026-07-08 10:00:00',
                '2026-07-08 10:00:00', 'DS', 'v1', '{}'
            )
            """
        )
        conn.exec_driver_sql(
            """
            INSERT INTO catalog.curriculum_plan_row(
                id, plan_id, block_index, row_number, project_index_in_block,
                block_title, block_goal, project_name, project_summary,
                learning_outcomes, skills_list, audience_level, required_tools,
                materials, storytelling, delivery_format, group_size,
                effort_hours, effort_days, cumulative_days, xp,
                platform_project_name, artifact_links, completion_percent,
                p2p_checks, weighted_skills, validation_criteria
            )
            VALUES (
                100, 10, 1, 1, 1, 'Блок 1. Данные', 'Строить аналитический пайплайн',
                'Анализ датасета', 'Участник исследует CSV и готовит выводы.',
                'Читать CSV\nПроверять качество данных', 'Python, Pandas',
                'Начальный', 'Jupyter, Git', 'materials/data.md',
                'Рабочий кейс аналитика.', 'индивидуальный', NULL,
                12, 4, 4, 120, 'DS01_Dataset', 'https://gitlab.example/ds01',
                75, 2, 'Python: 50%, Pandas: 50%', 'Проверить notebook и отчет.'
            )
            """
        )
    return sessionmaker(bind=engine)


@pytest.mark.asyncio
async def test_brief_up_snapshot_to_single_project_generation_start(monkeypatch: pytest.MonkeyPatch) -> None:
    session_factory = _session_factory()
    monkeypatch.setattr(contract, "SessionLocal", session_factory)

    with session_factory() as db:
        context_result = contract.build_generation_context_from_persisted_plan(
            db,
            plan_id=10,
            block_name="Блок 1. Данные",
            project_order=1,
        )

    origin = context_result["origin"]
    assert origin["source_plan_id"] == 10
    assert origin["plan_row_id"] == 100
    assert origin["project_index"] == 1
    assert origin["pipeline_run_id"].startswith("pipe_")
    assert context_result["context"]["curriculum_origin"]["plan_hash"] == origin["plan_hash"]

    seed = {
        "language": "ru",
        "llm_provider": "polza",
        "project_type": "individual",
        "direction": "DS",
        "thematic_block": "Блок 1. Данные",
        "audience_level": "beginner_plus",
        "required_tools": ["Jupyter", "Git"],
        "required_software": [],
        "title_seed": "Анализ датасета",
        "project_description": "Участник исследует CSV и готовит выводы.",
        "learning_outcomes": ["Читать CSV", "Проверять качество данных"],
        "skills": ["Python", "Pandas"],
        "storytelling_type": "sjm",
        "sjm": "Рабочий кейс аналитика.",
        "curriculum_context": context_result["context"],
        "curriculum_origin": origin,
    }

    background_calls: list[dict[str, Any]] = []
    tasks = []
    workflow = _WorkflowService()

    async def background_runner(
        request_id: str,
        user_id: str,
        project_seed_dict: dict[str, Any],
        track_paths: list[str],
        _resume_from: str | None,
    ) -> None:
        background_calls.append(
            {
                "request_id": request_id,
                "user_id": user_id,
                "seed": project_seed_dict,
                "track_paths": track_paths,
            }
        )

    async def log_writer(**_kwargs: Any) -> None:
        return None

    service = GenerationStartService(
        status_setter=lambda *_args: None,
        error_store=lambda *_args: None,
        task_registrar=lambda _request_id, task: tasks.append(task),
        background_runner=background_runner,
        log_writer=log_writer,
        logger=_Logger(),
        request_id_factory=lambda: "req-up-1",
        workflow_service=workflow,  # type: ignore[arg-type]
    )

    response = await service.start_from_request(
        request=_JsonRequest(seed),  # type: ignore[arg-type]
        track_files=None,
        user_id="methodologist",
    )
    assert not background_calls
    for task in tasks:
        await task

    assert response.request_id == "req-up-1"
    assert workflow.created[0]["seed_metadata"]["source_plan_id"] == 10
    assert workflow.created[0]["seed_metadata"]["plan_row_id"] == 100
    assert background_calls[0]["seed"]["pipeline_run_id"] == origin["pipeline_run_id"]
    assert background_calls[0]["seed"]["source_plan_id"] == 10
    assert background_calls[0]["seed"]["plan_hash"] == origin["plan_hash"]


def test_readiness_gate_blocks_generation_when_brief_reviews_are_open() -> None:
    session_factory = _session_factory()
    with session_factory() as db:
        db.execute(
            text(
                """
                INSERT INTO catalog.review_queue(
                    id, entity_type, entity_id, source_ref, reason_code, severity, details, status
                )
                VALUES (1, 'skill', 1000, 'brief:1', 'manual_check', 'warning', '{}', 'open')
                """
            )
        )
        db.commit()

        with pytest.raises(contract.CurriculumContractError) as exc_info:
            contract.build_generation_context_from_persisted_plan(
                db,
                plan_id=10,
                block_name="Блок 1. Данные",
                project_order=1,
            )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["blockers"][0]["code"] == "open_reviews"
