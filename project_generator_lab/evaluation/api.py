"""Owner-scoped evaluation jobs, checkpoints and exports; never change generation approval state."""
import asyncio
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, Response
from pydantic import Field, field_validator

from ..content import store as content_store
from ..core import usage
from ..core.contracts import Contract
from ..core.llm import ModelClient, model_configuration, reviewer_model
from . import engine, registry, store
from .findings import grouped
from .models import Context, Evaluation
from .policy import presentation, summary
from .snapshot import build, hashed

router = APIRouter()
jobs: dict[str, asyncio.Task[None]] = {}
def default_context(run_id: str) -> Context:
    return Context()


def default_client() -> ModelClient:
    return ModelClient(model=reviewer_model() or model_configuration()[2])


context_provider: Callable[[str], Context] = default_context
client_factory: Callable[[], ModelClient] = default_client


class Start(Contract):
    use_model: bool = True
    force: bool = False
    approved_files: list[str] | None = Field(default=None, max_length=300)

    @field_validator("approved_files")
    @classmethod
    def relative_files(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        result = []
        for value in values:
            value = value.replace("\\", "/").strip()
            path = PurePosixPath(value)
            if not value or len(value) > 300 or path.is_absolute() or ".." in path.parts or ":" in value:
                raise ValueError("Перечень содержит недопустимый относительный путь")
            result.append(str(path))
        if len(set(result)) != len(result):
            raise ValueError("Имена файлов в перечне повторяются")
        return sorted(result)


def context(run_id: str) -> Context:
    try:
        return context_provider(run_id)
    except HTTPException as error:
        if error.status_code != 404:
            raise
        return Context(limitations=["Исходный запуск замысла недоступен; проверка использует переданный контекст."])


def view(report: Evaluation, run_id: str) -> dict[str, object]:
    report = presentation(report)
    draft = content_store.load(run_id)
    current = build(draft, context(run_id), report.snapshot.approved_files)
    profile = registry.load()
    stale = (current.digest != report.snapshot.digest or registry.digest(profile) != report.profile_digest
             or report.engine_version != engine.VERSION or hashed(engine.PROMPT.read_text(encoding="utf-8")) != report.prompt_digest)
    return {"exists": True, **report.model_dump(), "summary": summary(report, stale), "model_ready": bool(model_configuration()[0]),
            "progress": sum(v.state != "pending" for v in report.verdicts), "total": len(report.verdicts)}


@router.get("/api/content/{run_id}/evaluation")
async def get_evaluation(run_id: UUID) -> dict[str, object]:
    key = str(run_id)
    content_store.load(key)
    report = store.latest(key)
    if report is None:
        return {"exists": False, "profile": registry.load().model_dump(), "model_ready": bool(model_configuration()[0]),
                "history_warnings": store.history(key)[1]}
    return view(report, key)


@router.post("/api/content/{run_id}/evaluation", status_code=202)
async def start_evaluation(run_id: UUID, body: Start) -> dict[str, object]:
    key = str(run_id)
    async with store.lock(key), content_store.lock(key):
        draft = content_store.load(key)
        previous = store.latest(key)
        if key in jobs and not jobs[key].done():
            assert previous is not None
            return view(previous, key)
        if draft.busy:
            raise HTTPException(409, "Дождитесь завершения записи контента, чтобы проверить согласованный снимок.")
        if body.use_model and not model_configuration()[0]:
            raise HTTPException(503, "Модель не настроена. Можно выполнить проверки кодом.")
        if sum(not task.done() for task in jobs.values()) >= 2:
            raise HTTPException(429, "Сейчас выполняются две проверки. Повторите позже.")
        snapshot = build(draft, context(key), body.approved_files)
        model = (reviewer_model() or model_configuration()[2]) if body.use_model else ""
        report = engine.initial(draft, snapshot, model, body.use_model)
        if (previous and previous.state == "complete" and previous.snapshot.digest == snapshot.digest
                and previous.use_model == body.use_model and engine.compatible(previous, report) and not body.force):
            return view(previous, key)
        client = client_factory() if body.use_model else None
        store.save(report)
        # Inherit owner identity and account every paid call under the source project, even after the request ends.
        task = asyncio.create_task(engine.run(report, draft, client, previous, body.force), context=usage.under(key))
        jobs[key] = task
        task.add_done_callback(lambda finished: jobs.pop(key, None) if jobs.get(key) is finished else None)
        return view(report, key)


@router.post("/api/content/{run_id}/evaluation/stop")
async def stop_evaluation(run_id: UUID) -> dict[str, object]:
    key = str(run_id)
    # Serialize stop and start for this project. The engine never takes this orchestration lock.
    async with store.lock(key):
        content_store.load(key)
        report = store.latest(key)
        if report is None:
            raise HTTPException(404, "Проверка ещё не запускалась")
        task = jobs.get(key)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            report = store.latest(key) or report
        # A task canceled before first scheduling has not entered engine.run's finally block.
        if report.state == "running":
            report.state = "interrupted"
            report.notes.append("Проверка остановлена пользователем. Готовые результаты сохранены.")
            store.save(report)
        return view(report, key)


def markdown(report: Evaluation, stale: bool) -> str:
    report = presentation(report)
    totals = summary(report, stale)
    rows = [f"# Проверка проекта: {report.snapshot.title}", "", "Экспериментальная рекомендация, не утверждение проекта.",
            f"Состояние: {totals['recommendation']}; актуальность: {'устарело' if stale else 'актуально'}.",
            f"Оценено {totals['assessed']} из {totals['applicable']} применимых критериев 1–40.",
            f"Правила итога: {totals['policy_version']}. {totals['score_basis']}",
            f"Подтверждено требований: {totals['fulfilled']}. Групп замечаний: {totals['finding_count']}. Экспертные 41–56: без баллов.",
            f"Профиль: {report.profile.version}; снимок: {report.snapshot.digest}; модель: {report.model or 'только код'}.", ""]
    rows += ["## Что исправить"]
    for finding in grouped(report,report.verdicts):
        rows += [f"### {finding.title}", f"Уровень: {finding.level}; критерии: {finding.criteria}.", finding.reason,finding.suggestion]
    rows += ["## Нужна проверка основания замечаний"]
    for verdict in report.verdicts:
        if verdict.criterion <= 40 and not verdict.evidence_valid:
            rows += [f"Критерий {verdict.criterion}: {verdict.reason}", "Замечание сохранено; доказанным дефектом не считается."]
    rows += [f"Попыток вызова модели: {report.calls}; сбоев: {report.failed_calls}; известные токены: {report.input_tokens} / {report.output_tokens}.",
             "## Ограничения и состояние проверки",
             *(f"- {text}" for text in [*report.snapshot.limitations, *report.notes, *report.history_warnings]), "",
             "## Связи образовательных результатов"]
    if not report.outcomes:
        rows.append("Цепочки ОР не проверялись или не предъявлены; это не подтверждение достижения ОР.")
    for item in report.outcomes:
        title = report.snapshot.outcomes[item.outcome - 1] if item.outcome <= len(report.snapshot.outcomes) else "Неизвестный ОР"
        rows += [f"### ОР {item.outcome}. {title}", f"Статус: {item.status}; задания: {', '.join(map(str, item.tasks)) or '—'}.", item.reason]
        for name, references in (("Действие", item.action), ("Результат", item.result), ("P2P", item.peer)):
            rows += [f"{name}:", *(f"> [{e.unit}] {e.quote}" for e in references)]
            if not references:
                rows.append("Свидетельство не предъявлено.")
    for v in report.verdicts:
        c = report.profile.criteria[v.criterion - 1]
        assessment = f"оценка: {v.mark if v.mark is not None else '—'}" if v.criterion <= 40 else "экспертное замечание, без баллов"
        rows += [f"## {c.number}. {c.title}", f"Статус: {v.state}; {assessment}; метод: {v.method or 'не выполнен'}.",
                 v.reason, *(f"> [{e.unit}] {e.quote}" for e in v.evidence), v.suggestion, ""]
    return "\n\n".join(rows)


@router.get("/api/content/{run_id}/evaluation/export/{kind}")
async def export_evaluation(run_id: UUID, kind: Literal["json", "md"]) -> Response:
    key = str(run_id)
    content_store.load(key)
    report = store.latest(key)
    if report is None:
        raise HTTPException(404, "Проверка ещё не запускалась")
    result = view(report, key)
    headers = {"Content-Disposition": f'attachment; filename="project-evaluation-{key}.{kind}"'}
    if kind == "json":
        import json
        return Response(json.dumps(result, ensure_ascii=False, indent=2), media_type="application/json", headers=headers)
    return PlainTextResponse(markdown(report, bool(result["summary"]["stale"])), headers=headers)  # type: ignore[index]


async def stop_all() -> None:
    pending = list(jobs.values())
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
