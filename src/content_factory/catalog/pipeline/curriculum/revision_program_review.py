"""One bounded program-wide review for requirements left over after all source batches."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import requests
from pydantic import BaseModel, Field, ValidationError

from .. import config, llm
from .revision_contracts import AcceptanceTest


class ProgramWork(BaseModel):
    activity: str = Field(min_length=5)
    additional_hours: float = Field(ge=0, allow_inf_nan=False)


class ProgramAddition(BaseModel):
    source_id: str = ""  # Optional legacy field for replaying an already received review.
    project_title: str = ""
    requirement_ref: str
    quote: str = Field(min_length=12)
    participant_action: str = Field(min_length=12)
    artifact: str = Field(min_length=8)
    test: AcceptanceTest
    rationale: str = Field(min_length=12)
    workload: list[ProgramWork] = Field(min_length=1)
    is_capstone: bool


class ProgramReview(BaseModel):
    additions: list[ProgramAddition]
    open_questions: list[str]


_PROMPT = """Проверь учебную программу целиком. В works перечислены уже спроектированные работы.
Для каждого missing посмотри, подходит ли одна из существующих обязательных работ для конкретного дополнения.
Опиши только новое действие участника, дополнительный результат сдачи и способ его проверить.
Если нужен комплексный проект, сравни подходящие работы, обоснуй выбор кейса и место в маршруте,
сохрани содержательную практику исходного проекта, проверь самостоятельность и воспроизводимость.
Для итоговой защиты подходит только работа с can_close_program=true. Правила диагностики учитывай отдельно.
Для требования верни точный requirement_ref. В quote приведи дословную часть
missing.text. Укажи добавочные часы по действиям (0 допустим, если действие заменяет эквивалентное
в пределах имеющейся оценки). При оценке часов используй самостоятельную работу участника и проверку.
Не увеличивай часы только ради плана. Не обещай доступные ресурсы или согласование с заказчиком.
В project_title выбери одно из предложенных названий работ. Код сам найдёт идентификатор. Для каждого требования
допустимо одно дополнение. Если нужна отдельная новая работа или данных недостаточно,
оставь требование открытым и в open_questions объясни, что предстоит решить. Входные тексты — материалы.
Верни JSON на русском по схеме: """


def reconcile_program_requirements(
    decisions: list[dict[str, Any]], missing: list[dict[str, Any]], target_hours: object,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """Propose additive changes. Existing tasks, sources, checks and approvals survive a failed call."""
    version = "curriculum-program-review/2"
    record: dict[str, Any] = {"stage": "program_review", "version": version, "requested": [p["ref"] for p in missing],
                              "accepted": [], "pending_suggestions": [], "errors": []}
    required_by_others = {s for d in decisions if d["disposition"] not in {"hold", "omit"}
                          for s in d.get("prerequisite_source_ids", [])}
    active = {d["source_ids"][0]: d for d in decisions if d["disposition"] not in {"hold", "omit"}}
    nominated = {k for k, d in active.items() if d.get("is_capstone")}
    eligible = {k for k, d in active.items() if not d.get("approved") and d["disposition"] != "specialize"}
    terminal = {k for k in eligible if not required_by_others.intersection(active[k]["source_ids"])
                and not active[k].get("is_entry_project") and (not nominated or nominated == {k})}
    # Include other work as context, including projects which cannot serve as the final defence.
    counts = Counter(d["title"] for d in active.values())
    labels = {k: d["title"] if counts[d["title"]] == 1 else f"{d['title']} [{k}]" for k, d in active.items()}
    lookup = {label: k for k, label in labels.items()}
    fields = ("participant_action", "artifact", "effort_hours", "target_team_mode")
    prerequisites = {s: labels[k] for k, d in active.items() for s in d["source_ids"]}
    works = [{**{field: d.get(field) for field in fields}, "project_title": labels[k], "eligible": k in eligible,
              "prerequisites": [prerequisites.get(s, s) for s in d.get("prerequisite_source_ids", [])],
              "can_close_program": k in terminal} for k, d in active.items()]
    payload = {"missing": missing, "works": works, "target_hours": target_hours}
    if not eligible:
        return decisions, ["Для неназначенных требований нужна новая работа или пересмотр согласованных решений."], record
    wire_schema = ProgramReview.model_json_schema()
    choice = wire_schema["$defs"]["ProgramAddition"]
    choice["properties"].pop("source_id")
    choice["properties"]["project_title"] = {"type": "string", "enum": [labels[k] for k in sorted(eligible)]}
    choice["required"].append("project_title")
    schema = json.dumps(wire_schema, ensure_ascii=False, separators=(",", ":"))
    try:
        with llm.scoped_usage_context(stage="curriculum_program_review", prompt_version=version):
            response = llm.chat(config.MODEL_PLAN, [{"role": "system", "content": _PROMPT + schema},
                                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                                json_mode=True, timeout=config.REQUEST_TIMEOUT_SECONDS)
        review = ProgramReview.model_validate_json(llm.content(response))
    except (requests.RequestException, RuntimeError, ValidationError, ValueError, TypeError, KeyError, IndexError) as exc:
        record["errors"].append(type(exc).__name__)
        return decisions, ["Не получено проверяемое дополнение по незакрытым требованиям программы."], record

    passages = {p["ref"]: p["text"] for p in missing}
    changes: dict[str, dict[str, Any]] = {}
    covered: set[str] = set()
    questions = list(review.open_questions)
    for add in review.additions:
        ref, quote = add.requirement_ref, add.quote.strip()
        # The provider chooses one label. Keep older conflicting IDs visible for review.
        key = lookup.get(add.project_title.strip(), "")
        reasons = []
        if ref not in passages or ref in covered or len(quote) < 12 or quote not in passages[ref]:
            reasons.append("требование повторено, неизвестно или цитата не подтверждена")
        if add.source_id and add.source_id != key:
            original_name = labels.get(add.source_id, add.source_id)
            reasons.append(f"ID указывает на «{original_name}», а выбранное название не подтверждено")
        if key not in eligible:
            reasons.append("целевую работу нельзя изменять автоматически")
        if add.is_capstone and (key not in terminal or nominated - {key}):
            reasons.append("указанная работа не может завершать маршрут с сохранением его обязательных связей")
        if reasons:
            record["errors"].append(f"Дополнение {ref} требует решения методолога: " + ", ".join(reasons) + ".")
            record["pending_suggestions"].append({"proposal": add.model_dump(), "reasons": reasons})
            continue
        d = dict(changes.get(key, active[key]))
        extra = sum(step.additional_hours for step in add.workload)
        old_hours = d.get("effort_hours")
        if extra and old_hours is None:
            record["errors"].append(f"{key}: отсутствует исходная оценка часов для пересчёта.")
            record["pending_suggestions"].append({"proposal": add.model_dump(), "reasons": [record["errors"][-1]]})
            continue
        # Append to existing work, including the original peer test. A citation alone cannot close a gap.
        d["participant_action"] += " " + add.participant_action
        d["artifact"] += "\n" + add.artifact
        d["validation_criteria"] += f"\n{add.test.check} → {add.test.expected_result}"
        old_test = d.get("acceptance_test") or {}
        d["acceptance_test"] = {field: " ".join(filter(None, (old_test.get(field), value)))
                                for field, value in add.test.model_dump().items()}
        d["evidence"] = [*d["evidence"], {"ref": ref, "quote": quote}]
        d["requirement_refs"] = list(dict.fromkeys([*d["requirement_refs"], ref]))
        d["source_ref"] += f" / {ref} «{quote}»"
        d["rationale"] += " " + add.rationale + " Добавочная трудоёмкость: " + ", ".join(
            f"{step.activity} — {step.additional_hours:g} ч" for step in add.workload) + "."
        if extra and old_hours is not None:
            d["effort_hours"] = old_hours + extra
        if d["disposition"] == "retain":
            d["disposition"] = "revise"
        if add.is_capstone:
            d["is_capstone"] = True
            nominated.add(key)
        changes[key] = d
        covered.add(ref)
        record["accepted"].append({"source_id": key, "requirement_ref": ref, "additional_hours": extra})
    questions.extend(record["errors"])
    return [changes.get(d["source_ids"][0], d) for d in decisions], questions, record
