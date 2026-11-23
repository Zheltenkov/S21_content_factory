"""Shared interpretation of form fields; independent of any model provider."""

import math
import re

from .models import CurriculumRow, StartRequest
from .source_policy import effective_request


def projects(request: StartRequest) -> list[CurriculumRow]:
    """The plan's projects in order. Exams are control points between them: they are not counted as projects and are
    nobody's «предыдущий проект». Everywhere else an exam stays a line of the plan, marked as an exam: in the
    participant's path, in the map of the programme the models get, in the hours of the plan."""
    request = effective_request(request)
    return [row for row in (request.curriculum.rows if request.curriculum else []) if row.kind != "exam"]


def program_position(request: StartRequest) -> dict[str, object] | None:
    """Where the project sits among the projects of the curriculum, computed from row order for people and models alike."""
    request = effective_request(request)
    rows = projects(request)
    index = next((i for i, row in enumerate(rows) if row.id == request.selected_row_id), None)
    if index is None:
        return None
    blocks = list(dict.fromkeys(row.block for row in rows))
    total = len(rows)
    stage = ("первый проект программы" if index == 0 else "последний проект программы" if index == total - 1
             else "начало программы" if index < total / 3 else "середина программы" if index < 2 * total / 3
             else "конец программы")
    return {
        "program": rows[index].program, "position": index + 1, "total": total, "stage": stage,
        "block": rows[index].block, "block_position": blocks.index(rows[index].block) + 1, "blocks_total": len(blocks),
        "previous": rows[index - 1].title if index else "", "next": rows[index + 1].title if index + 1 < total else "",
    }


def _items(text: str) -> list[str]:
    """Skills or tools cell as a list: one per line, or separated by «;» or «,»."""
    return [part.strip() for part in re.split(r"[\n;,]", text or "") if part.strip()]


def goal_lines(text: str) -> list[str]:
    """Block goals as a list: one per line, bullets and numbering removed."""
    return [re.sub(r"^\s*(?:[-•*·]|\d+[.)])\s*", "", line).strip() for line in (text or "").splitlines() if line.strip()]


def _key(text: str) -> str:
    # «Requirements Analisys» and «Requirements analysis» are the same skill written twice; «C», «C++» and «C#» are
    # three skills of the platform's catalogue.
    return re.sub(r"[^a-zа-я0-9+#]", "", text.casefold().replace("ё", "е").replace("analisys", "analysis"))


def _hours(text: str) -> float | None:
    try:
        value = float(str(text).replace(",", ".").strip())
    except ValueError:
        return None
    # «inf» is a number to float() and breaks every JSON the hours go into.
    return value if math.isfinite(value) and value > 0 else None


def program_map(request: StartRequest) -> dict[str, object] | None:
    """How the project links to its block and the whole curriculum: facts counted from the plan, not guessed.

    Row order and the skills column are all the plan says; whether an earlier project really prepares this one
    is for the methodologist and the designer to judge.
    """
    request = effective_request(request)
    rows = projects(request)
    index = next((i for i, row in enumerate(rows) if row.id == request.selected_row_id), None)
    if index is None:
        return None
    row = rows[index]
    block_rows = [(i, r) for i, r in enumerate(rows) if r.block == row.block]
    before = rows[:index]
    after = rows[index + 1:]
    # Hours are the plan's: an exam takes the participant's time as a project does, and the plan's own total counts it.
    lines = request.curriculum.rows if request.curriculum else []
    place = next(i for i, r in enumerate(lines) if r.id == row.id)
    seen_before = {_key(s) for r in before for s in _items(r.skills)}
    later: dict[str, list[str]] = {}
    for r in after:
        for skill in _items(r.skills):
            later.setdefault(_key(skill), []).append(r.title)
    skills = list(dict.fromkeys(_items(row.skills)))
    block_hours = [_hours(r.hours) for r in lines if r.block == row.block]
    program_hours = [_hours(r.hours) for r in lines]

    def total(values: list[float | None]) -> float | None:
        return round(sum(v for v in values if v), 2) if any(values) else None

    def neighbour(r: CurriculumRow | None) -> dict[str, str] | None:
        return {"title": r.title, "block": r.block} if r else None

    position = program_position(request)
    assert position is not None
    return {
        **position,
        "block_goals": row.block_goals,
        # Numbered so that a goal can be placed, covered by a task and checked by code; the numbers follow the cell's lines.
        "block_goal_items": [{"number": n, "text": text} for n, text in enumerate(goal_lines(row.block_goals), 1)],
        "block_projects": [{"title": r.title, "hours": _hours(r.hours), "current": i == index} for i, r in block_rows],
        "position_in_block": [i for i, _ in block_rows].index(index) + 1,
        "block_hours": total(block_hours),
        "program_hours": total(program_hours),
        "hours_before": total([_hours(r.hours) for r in lines[:place]]),
        "previous_project": neighbour(rows[index - 1] if index else None),
        "next_project": neighbour(rows[index + 1] if index + 1 < len(rows) else None),
        "skills_practised_before": [s for s in skills if _key(s) in seen_before],
        "skills_new": [s for s in skills if _key(s) not in seen_before],
        "skills_used_later": [{"skill": s, "projects": later[_key(s)][:3], "count": len(later[_key(s)])}
                              for s in skills if _key(s) in later],
        # The participant's path by the plan: earlier projects with outcomes, skills and tools; the later ones.
        "learner": learner_facts(request),
        "meaning": "Посчитано кодом по порядку записей и колонке навыков УП. Порядок — не доказательство освоения; "
                   "повтор навыка — повод опереться на прошлый проект, а не гарантия, что он пройден.",
    }


EXAMS_MEANING = (" Записи с kind=exam — экзамены: контрольные точки программы, а не проекты. Их ОР показывают, что у "
                 "участника проверяли; на экзамен нельзя опираться как на проект и передавать ему результат.")


def _exam(row: CurriculumRow) -> dict[str, str]:
    # Only an exam is marked: the path of a plan without exams reads exactly as before.
    return {"kind": "exam"} if row.kind == "exam" else {}


def _block_name(block: str) -> str:
    return " ".join(block.split())


def _tools(row: CurriculumRow) -> list[str]:
    return list(dict.fromkeys(_items(", ".join(x for x in (row.tools, row.software) if x))))


def learner_facts(request: StartRequest) -> dict[str, object] | None:
    """What the participant has gone through by the plan before this project, and what comes after it: the outcomes,
    skills and tools of every earlier project, counted from the plan. The plan's order is what the programme intends,
    not proof that someone mastered it; the interpreter summarises it, the methodologist corrects it."""
    rows = request.curriculum.rows if request.curriculum else []
    index = next((i for i, row in enumerate(rows) if row.id == request.selected_row_id), None)
    if index is None:
        return None
    row = rows[index]
    seen: dict[str, dict[str, object]] = {}
    # «В скольких проектах» counts projects: an exam on the platform is not practice with a tool.
    for earlier in (r for r in rows[:index] if r.kind != "exam"):
        for tool in _tools(earlier):
            entry = seen.setdefault(_key(tool), {"tool": tool, "projects": []})
            projects = entry["projects"]
            assert isinstance(projects, list)
            projects.append(earlier.title)
    ahead: dict[str, str] = {}
    for later in (r for r in rows[index + 1:] if r.kind != "exam"):
        for tool in _tools(later):
            if _key(tool) not in seen and _key(tool) not in {_key(t) for t in _tools(row)}:
                ahead.setdefault(_key(tool), f"{tool} — «{later.title}»")
    return {
        "before": [{"id": r.id, **_exam(r), "title": r.title, "block": _block_name(r.block), "hours": _hours(r.hours),
                    "outcomes": goal_lines(r.outcomes), "skills": _items(r.skills), "tools": _tools(r)} for r in rows[:index]],
        "later": [{"id": r.id, **_exam(r), "title": r.title, "block": _block_name(r.block), "tools": _tools(r)}
                  for r in rows[index + 1:]],
        "tools_before": list(seen.values()),
        "tools_here": [{"tool": t, "before": len(seen[_key(t)]["projects"]) if _key(t) in seen else 0}  # type: ignore[arg-type]
                       for t in _tools(row)],
        "tools_ahead": list(ahead.values()),
        "meaning": "Собрано кодом из УП: ОР, навыки и инструменты проектов до этого — то, что участник проходил по плану. "
                   "Порядок плана — не доказательство, что это освоено." + (EXAMS_MEANING if any(r.kind == "exam" for r in rows) else ""),
    }


def input_semantics(request: StartRequest) -> dict[str, object]:
    """Expose meaning and actual program order, without inventing prerequisites."""
    rows = request.curriculum.rows if request.curriculum else []
    selected = next((i for i, row in enumerate(rows) if row.id == request.selected_row_id), None)
    position = program_position(request)
    exams = any(row.kind == "exam" for row in rows)
    return {
        "fields": {
            "skills": "Получаемые навыки: проект развивает их. Это НЕ входные требования и НЕ доказательство освоения.",
            "audience": "Заданный уровень аудитории. Конкретные входные умения требуют отдельного основания или пометки proposal.",
            "outcomes": "Обязательные ОР текущего проекта. Сохраняй действие, объект, условия и названные инструменты (например Excel).",
            "tools": "Инструменты и ПО, обязательные в проекте (методисты их не различают); каждый должен получить обоснованное применение, не декоративное упоминание. Устаревшее поле software, если заполнено, читай вместе с tools.",
            "learner_term": "Как программа называет обучающегося (например, участник или студент). Пусто — «участник». Используй это слово во всех текстах.",
            "concept_feedback": "Замечания методолога к предыдущему замыслу: какие задания оставить, заменить или переработать. Обязательны к учёту в новой версии.",
            "tables": "Дополнительные таблицы в учебном тексте. False: специально не добавлять ради оформления. Не запрещает таблицу/Excel в работе участника по ОР.",
            "formulas": "Дополнительные формулы в учебном тексте. False не запрещает расчёты и формулы в смете участника.",
            "diagrams": "Дополнительные диаграммы в учебном тексте. False не отменяет диаграмму, прямо необходимую по ОР.",
            "bonus": "Добавлять бонусное задание только при true. Бонус не заменяет обязательные ОР.",
            "output_package": "Что должен создать генератор позднее; пустое поле означает предложить комплект, а не требовать готовые файлы сейчас.",
            "start_level": "Стартовый уровень участника: zero — не знаком с инструментами и понятиями направления; basic, intermediate, advanced. Пусто — уровень неизвестен, это вопрос, а не новичок по умолчанию. В текстах для людей называй уровни по-русски: нулевой, базовый, средний, продвинутый; коды zero, basic и т. п. не пиши.",
            "start_skills": "Что участник уже умеет и с чем работал на входе. Только это считается входной подготовкой; всё новое вводится до использования. Здесь только умения; чего участники не умеют, записано в constraints.",
            "program_outcomes": "Образовательные результаты всей программы. Проект вносит вклад в часть из них, не обязан закрывать все.",
            "customer_requirements": "Требования заказчика программы: ограничения и ожидания, обязательные для учёта.",
            "program": "Устаревшее свободное описание программы из старых брифов; используй, если новые поля пусты.",
        },
        "start_level_rules": {
            "zero": "Нельзя требовать настройки окружения, служебных файлов и инструментов (например .gitignore, .gitattributes, CI) без явного ОР или требования; каждое новое понятие и инструмент вводятся с примером до самостоятельного применения.",
            "unknown": "Не предполагай подготовку; уровень и входные умения — вопрос методологу.",
        },
        "priority": "Явные ОР и обязательные инструменты не отменяются опциями оформления. Изменение ОР — только предложение для методолога, не выполненный ОР.",
        "materials": "Отделяй приложенные источники от будущего комплекта и сдачи участника. Для будущего материала предложи: что, для какого задания, кто создаёт, что делает участник. Отсутствие файла сейчас не дефект замысла, если его создание запланировано.",
        "program_order": {
            # The same number as computed_state.program_position: the project's number among the projects of the plan.
            "selected_position": position["position"] if position else None,
            "before_ids": [r.id for r in rows[:selected]] if selected is not None else [],
            "after_ids": [r.id for r in rows[selected + 1:]] if selected is not None else [],
            "meaning": "Порядок записей УП, а не доказательство освоения или обязательной зависимости. Не называй предыдущие записи последующими."
                       + (" selected_position — номер среди проектов; before_ids и after_ids — все записи до и после, экзамены тоже." if exams else ""),
        },
    }
