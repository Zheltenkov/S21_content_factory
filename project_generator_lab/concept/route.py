"""Where the route stands (inputs → concept → review → approval) and the steps that move it. The next step is computed
from the state, so the chat never has to guess the route; approval stays a button.
"""

from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from ..core import chat
from .answers import open_questions
from .models import Run, StartRequest
from .patches import apply_patches
from .thread import FIELDS, Message, Thread, api, readable
from .workload import overflow, overflow_text, said


def changed_fields(thread: Thread, run: Run) -> list[str]:
    base, draft = run.request.brief.model_dump(), thread.draft.model_dump()
    return [key for key in FIELDS if draft[key] != base[key]]


AREA_NAMES = {"outcomes": "ОР", "audience": "Аудитория", "constraints": "Ограничения", "program": "Программа", "materials": "Материалы"}
KIND_ORDER = {"conflict": 0, "unknown": 1, "proposal": 2}


def doubtful_claims(run: Run, limit: int = 4) -> list[dict[str, str]]:
    """What the methodologist should look at first: contradictions, then gaps, then the model's own assumptions."""
    claims = [c for c in (run.input_card.claims if run.input_card else []) if c.kind in KIND_ORDER]
    claims.sort(key=lambda c: KIND_ORDER[c.kind])
    return [{"kind": c.kind, "area": AREA_NAMES[c.area], "text": c.text} for c in claims[:limit]]


STAGE_NAMES = {"context": "разбор входов", "inputs_approval": "разбор входов", "design": "подготовка замысла", "review": "проверка замысла"}
ROUTE = "Этапы: входы → замысел → проверка → ваше согласование. После согласования в этом же чате готовим контент."


def next_step(thread: Thread, run: Run) -> dict[str, object]:
    """What the methodologist can do now, computed from the state: the chat never has to guess the route."""
    step: dict[str, object] = {"action": None, "button": "", "title": "", "text": "", "route": ROUTE}
    changed = [FIELDS[key] for key in changed_fields(thread, run)]
    pending = any(m.status == "pending" for m in thread.messages)
    if run.status == "running":
        stage = next((e.stage for e in reversed(run.events) if e.status == "running"), "context")
        return {**step, "title": f"Идёт {STAGE_NAMES.get(stage, 'подготовка')}",
                "text": "Готовые этапы сохраняются. Можно писать в чат: правки войдут в задание для следующей версии."}
    if run.status in {"failed", "interrupted"} and not pending and not changed:
        return {**step, "action": "resume", "button": "Продолжить", "title": "Подготовка остановилась",
                "text": f"{run.error or 'Подготовка не завершена.'} Готовые этапы не повторяются. "
                        "Если нужно что-то убрать или поменять в задании (например, лишние материалы), напишите — "
                        "тогда подготовка пойдёт новой версией с правками."}
    # After a failure the methodologist's edits come first: «Продолжить» would rerun the old brief and fail the same
    # way (audit 30.09.2026). Pending edits are applied, then a new version starts with them.
    if pending:
        return {**step, "action": "apply_and_continue", "button": "Применить правки и продолжить", "title": "Есть правки без решения",
                "text": "Примените, измените или отклоните правки выше. Кнопка применит последние предложенные правки и перейдёт к следующему шагу."}
    if changed:
        return {**step, "action": "new_version", "button": "Учесть правки и подготовить замысел", "title": "Задание изменилось",
                "text": f"Изменено: {', '.join(changed)}. ИИ заново разберёт входы с учётом правок и, если не появится важных вопросов, "
                        "сразу подготовит замысел и проверку."}
    if run.status == "input_review" and not run.hours_kept and (capacity := overflow(run.workload_budget)) is not None:
        # Not a warning to read past: the design would make the usual eight tasks of the hours, each longer than the norm.
        assert run.workload_budget is not None
        hours = run.workload_budget.total_hours or 0
        return {**step, "action": "keep_hours", "button": f"Оставить {said(hours)} ч — задания длиннее нормы",
                "title": "Часы не помещаются в один проект", "text": overflow_text(run.workload_budget, capacity),
                "edit_hint": "Новая трудоёмкость или как разделить проект…", "checks": doubtful_claims(run)}
    if run.status == "input_review":
        left = open_questions(thread, run)
        return {**step, "action": "confirm_inputs", "button": "Подготовить замысел", "title": "Входы разобраны",
                "text": "Дальше два пути. Если нужно изменить или дополнить требования к контенту — напишите, что поменять: "
                        "я внесу это в задание. Если всё верно — переходим к замыслу: ИИ спроектирует задания, отдельный "
                        "проверяющий их проверит, и замысел вернётся к вам на согласование."
                        + (f" Вопросов без ответа: {left} — с ними тоже можно продолжить, ИИ отметит свои допущения." if left else ""),
                "edit_hint": "Что изменить или дополнить в требованиях…", "checks": doubtful_claims(run)}
    if run.status == "review":
        return {**step, "action": "approve", "button": "Согласовать замысел", "title": "Шаг 3 из 4: замысел и проверка готовы",
                "text": "Замысел и замечания проверяющего — выше. Нужна правка — напишите в чат, например «задание 2 заменить на …»: "
                        "она попадёт в задание, и замысел пересоберётся. Согласование фиксирует эту версию."}
    return {**step, "title": "Замысел согласован",
            "text": "Следующий этап — учебный контент в этом же чате. Замысел также можно скачать файлом."}


def agree(thread: Thread, run: Run) -> str:
    """«Пойдёт» accepts the proposal it answers: the assistant's last reply, when that is a proposal still waiting.
    Otherwise it changes nothing, and the hint names what waits (its button), as in the content chat."""
    last = next((m for m in reversed(thread.messages) if m.role == "assistant"), None)
    if not last or last.status != "pending":
        return "Понял: задание оставляю без изменений." + chat.next_hint(next_step(thread, run))
    try:
        apply_patches(thread, last)
    except HTTPException as error:
        return f"Не применил предложенные правки: {error.detail}"
    return "Применил правки: " + ", ".join(f"«{p.label}»" for p in last.patches) + "."


async def perform(thread: Thread, run: Run, action: str) -> str:
    """Run one step of the route and say what happened; the caller saves the thread."""
    if action == "apply_and_continue":
        # Validate the entire batch on a copy; a stale edit must not leave half a batch applied.
        probe = thread.model_copy(deep=True)
        labels: list[str] = []
        for message in probe.messages:
            if message.status == "pending":
                apply_patches(probe, message)
                labels.extend(p.label for p in message.patches)
        thread.draft, thread.messages = probe.draft, probe.messages
        applied = ""
        if labels:
            applied = "Применил пакет правок: " + ", ".join(dict.fromkeys(labels)) + ". "
        follow = next_step(thread, run)
        # Neither the approval nor the hours more than a project holds are taken on the way: each is a decision of its own.
        if follow["action"] in {"apply_and_continue", "approve", "keep_hours", None}:
            return applied + str(follow["text"])
        return applied + await perform(thread, run, str(follow["action"]))
    if action == "new_version":
        await start_version(thread, run, auto_continue=True)
        return "Запускаю новую версию: разбор входов с учётом правок, затем замысел и проверка."
    if action == "confirm_inputs":
        await api().confirm_inputs(UUID(run.id))
        return "Входы приняты. Готовлю замысел, затем его проверит отдельный проверяющий."
    if action == "keep_hours":
        await api().confirm_inputs(UUID(run.id), keep_hours=True)
        plan = run.workload_budget
        assert plan is not None and plan.total_hours and plan.main_tasks_max  # the step is offered only then
        return (f"Оставляю {said(plan.total_hours)} ч: основных заданий будет {plan.main_tasks_max}, примерно по "
                f"{said(round(plan.total_hours / plan.main_tasks_max, 1))} ч — длиннее нормы. Входы приняты, готовлю замысел.")
    if action == "resume":
        await api().resume(UUID(run.id))
        return "Продолжаю с незавершённого этапа."
    if action == "approve":
        await api().approve(UUID(run.id))
        return "Замысел согласован и зафиксирован."
    raise HTTPException(409, "Сейчас нечего запускать")


async def proceed(thread: Thread, run: Run) -> str:
    """«Дальше» from the chat: every step except approval, which fixes a version and stays a button."""
    step = next_step(thread, run)
    action = step["action"]
    if action is None:
        return str(step["text"])
    if action == "approve":
        return "Замысел готов к согласованию. Согласую только по кнопке «Согласовать замысел»: она фиксирует версию."
    if action == "keep_hours":
        # «Дальше» is no answer to «fewer hours, two projects or longer tasks?»: the question is said again.
        return f"Сначала нужно ваше решение по часам. {step['text']}"
    try:
        return await perform(thread, run, str(action))
    except HTTPException as error:
        return f"Не получилось перейти дальше: {error.detail}"


async def start_version(thread: Thread, latest: Run, auto_continue: bool = False) -> None:
    """Start interpretation of the draft as a new run; the previous run stays unchanged."""
    if latest.status == "running":
        raise HTTPException(409, "Дождитесь завершения текущей подготовки или остановите её")
    if thread.draft == latest.request.brief and latest.status not in {"failed", "interrupted"}:
        raise HTTPException(409, "Задание не изменилось: новая версия не нужна")
    try:
        request = StartRequest(request_id=uuid4(), brief=thread.draft, curriculum=latest.request.curriculum,
                               selected_row_id=latest.request.selected_row_id, materials=latest.request.materials,
                               parent_run_id=UUID(latest.id),
                               resolved_questions=[*(a for a in latest.request.resolved_questions if not a.message_id),
                                                   *(a for m in thread.messages if m.status == "applied" for a in m.answers)][-200:])
    except ValidationError as error:
        raise HTTPException(422, readable(error)) from error
    result = await api().start(request, auto_continue=auto_continue)
    changed = [FIELDS[k] for k in changed_fields(thread, latest)]
    thread.runs.append(str(result["id"]))
    text = f"Версия {len(thread.runs)}: разбор входов запущен"
    thread.messages.append(Message(role="system", text=text + (f" · изменено: {', '.join(changed)}" if changed else ""),
                                   run_id=str(result["id"])))
