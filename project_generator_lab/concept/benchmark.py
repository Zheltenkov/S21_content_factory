"""Price-quality comparison: the same project through the full pipeline on several models.

    uv run python -m project_generator_lab.concept.benchmark --models openai/gpt-5.4-mini openai/gpt-5.4
    uv run python -m project_generator_lab.concept.benchmark --row 1 --level zero
    uv run python -m project_generator_lab.concept.benchmark --plan <УП.csv или паспорт.xlsx> --row 3

Each model interprets the inputs, gets the card approved automatically, designs the concept and
reviews it itself. Nothing is compared by the model: the summary counts what code can count
(tokens, cost, time, rule checks, outcome coverage). Reading the concepts is left to people.
Results: data/benchmarks/<time>/summary.md, summary.json and one run JSON per model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv

from ..core.llm import ModelClient
from . import skills
from .ingestion import parse_curriculum
from .models import Brief, Run, StartRequest
from .planning import fingerprint, prepare

ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT.parent
PRICES = ROOT / "config" / "model_prices.json"
DEFAULT_MODELS = ["openai/gpt-5.4-mini", "openai/gpt-5.4", "openai/gpt-5.6-sol", "openai/gpt-6-sol"]


def example_request(row_index: int, level: str, plan: Path | None = None, skills_tab: str = "") -> StartRequest:
    """Build the brief the same way the form does when a project of the plan is chosen (chat.js applyRow): the
    description is the plan's theses and its cases. plan — a plan of one's own instead of the example; row_index
    counts its lines with a title, exams among them (an exam is refused, as in the form)."""
    source = plan or ROOT / "examples" / "curriculum.csv"
    curriculum = parse_curriculum(source.name if plan else "Пример УП.csv", source.read_bytes(), skills_tab)
    for note in curriculum.skill_notes:
        print(note, flush=True)
    if not 1 <= row_index <= len(curriculum.rows):
        raise SystemExit(f"В УП {len(curriculum.rows)} строк с названием: --row от 1 до {len(curriculum.rows)}.")
    row = curriculum.rows[row_index - 1]
    tools = "; ".join(dict.fromkeys(x for x in (row.tools, row.software) if x))
    group = bool(re.search("групп|group", row.format, re.I))
    brief = Brief(
        title=row.title,
        description="\n\n".join(x for x in (row.description, f"Кейсы проекта:\n{row.cases}" if row.cases else "") if x),
        outcomes=row.outcomes,
        skills=row.skills,
        bonus=row.bonus,
        audience=row.audience,
        start_level=level,
        tools=tools,
        storytelling=row.storytelling,
        storytelling_type="sjm" if row.storytelling else "none",
        format="group" if group else "individual",
        group_size=int(row.group_size) if group and row.group_size.isdigit() else None,
        hours=float(str(row.hours).replace(",", ".")) if row.hours else None,
        references=row.materials,
        checking=row.checking,
    )
    return StartRequest(request_id=uuid4(), brief=brief, curriculum=curriculum, selected_row_id=row.id)


def cost(model: str, tokens_in: int, tokens_out: int) -> float | None:
    price = json.loads(PRICES.read_text(encoding="utf-8"))["prices"].get(model)
    return None if not price else round((tokens_in * price["input"] + tokens_out * price["output"]) / 1e6, 4)


def summarize(run: Run, model: str, seconds: float) -> dict[str, Any]:
    calls = [e for e in run.events if e.input_tokens]
    tokens_in = sum(e.input_tokens or 0 for e in calls)
    tokens_out = sum(e.output_tokens or 0 for e in calls)
    concept, review, card = run.concept, run.review, run.input_card
    main = [t for t in concept.tasks if not t.optional] if concept else []
    findings = review.findings if review else []
    outcomes = len([x for x in run.request.brief.outcomes.splitlines() if x.strip()])
    covered = sorted({n for t in main for n in t.outcome_numbers}) if concept else []
    return {
        "model": model,
        "status": run.status,
        "error": run.error,
        "seconds": round(seconds),
        "calls": len(calls),
        "schema_repairs": sum(e.schema_repairs for e in calls),
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "cost_usd": cost(model, tokens_in, tokens_out),
        "card_claims": len(card.claims) if card else None,
        "card_questions": len(card.questions) if card else None,
        "card_blocking_questions": sum(q.blocking for q in card.questions) if card else None,
        "tasks_main": len(main),
        "tasks_bonus": len(concept.tasks) - len(main) if concept else None,
        "hours_main": round(sum(t.hours for t in main), 2),
        "hours_brief": run.request.brief.hours,
        "outcomes_total": outcomes,
        "outcomes_covered": len([n for n in covered if 1 <= n <= outcomes]),
        "blockers_code": sum(f.severity == "blocker" and f.origin == "system" for f in findings),
        "warnings_code": sum(f.severity == "warning" and f.origin == "system" for f in findings),
        "blockers_model": sum(f.severity == "blocker" and f.origin == "model" for f in findings),
        "warnings_model": sum(f.severity == "warning" and f.origin == "model" for f in findings),
        "code_findings": [f.message for f in findings if f.origin == "system"],
        # The weights the designer gave are shown with the task; the block is what code counted from them.
        "task_titles": [f"{t.title} — {t.hours} ч" + (" · " + ", ".join(f"{name}: {weight}" for name, weight in t.skills.items()) if t.skills else "")
                        for t in (concept.tasks if concept else [])],
        "skills_table": skills.table(run.skill_block) if run.skill_block else [],
    }


async def one(model: str, request: StartRequest, out: Path) -> dict[str, Any]:
    run = Run(id=str(request.request_id), request=request, fingerprint=fingerprint(request))
    path = out / f"{model.replace('/', '_')}.json"

    def save(r: Run) -> None:
        path.write_text(r.model_dump_json(indent=1), encoding="utf-8")

    client = ModelClient(model=model)
    started = time.monotonic()
    await prepare(run, save, client)
    if run.status == "input_review" and run.input_card:
        # The benchmark stands in for the methodologist: the card is accepted as is.
        run.input_approved_at = datetime.now(timezone.utc).isoformat()
        run.status = "running"
        await prepare(run, save, client)
    save(run)
    result = summarize(run, model, time.monotonic() - started)
    print(f"{model}: {result['status']} за {result['seconds']} с, ${result['cost_usd']}", flush=True)
    return result


def table(rows: list[dict[str, Any]]) -> str:
    cols = [
        ("model", "Модель"),
        ("status", "Итог"),
        ("seconds", "Время, с"),
        ("input_tokens", "Вход, ток."),
        ("output_tokens", "Выход, ток."),
        ("cost_usd", "Цена, $"),
        ("schema_repairs", "Починок JSON"),
        ("card_questions", "Вопросов к входу"),
        ("tasks_main", "Заданий"),
        ("hours_main", "Часы заданий"),
        ("outcomes_covered", "ОР покрыто"),
        ("blockers_code", "Блокеры кода"),
        ("warnings_code", "Замечания кода"),
        ("blockers_model", "Блокеры ИИ"),
        ("warnings_model", "Замечания ИИ"),
    ]
    lines = ["| " + " | ".join(t for _, t in cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(k, "")) for k, _ in cols) + " |")
    return "\n".join(lines)


def markdown(lines: list[list[str]]) -> str:
    head, *rest = lines
    return "\n".join("| " + " | ".join(line) + " |" for line in (head, ["---"] * len(head), *rest))


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--plan", type=Path, default=None, help="свой УП (CSV или книга паспорта XLSX) вместо примера")
    parser.add_argument("--skills-tab", default="", help="вкладка развесовки книги паспорта, если их в ней несколько")
    parser.add_argument("--row", type=int, default=1, help="номер строки с названием в УП, с 1 (экзамены считаются)")
    parser.add_argument("--level", default="zero", choices=["", "zero", "basic", "intermediate", "advanced"])
    parser.add_argument("--max-output", type=int, default=32000, help="потолок ответа на вызов для всех моделей")
    parser.add_argument("--timeout", type=int, default=600, help="таймаут одного вызова, с")
    options = parser.parse_args()
    load_dotenv(PACKAGE.parent / ".env", override=False)
    # One ceiling for every model, high enough not to truncate a verbose tokenizer.
    os.environ["PROJECT_LAB_MAX_OUTPUT_TOKENS"] = str(options.max_output)
    os.environ["PROJECT_LAB_TIMEOUT_SECONDS"] = str(options.timeout)
    if not os.getenv("POLZA_AI_API_KEY"):
        raise SystemExit("В .env нет POLZA_AI_API_KEY")
    request = example_request(options.row, options.level, options.plan, options.skills_tab)
    out = PACKAGE / "data" / "benchmarks" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    print(
        f"Проект «{request.brief.title}», {request.brief.hours} ч, уровень {options.level or 'не указан'}; результаты: {out}",
        flush=True,
    )
    rows = list(await asyncio.gather(*(one(m, request.model_copy(), out) for m in options.models)))
    (out / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    details = "\n\n".join(
        f"### {r['model']}\n\n"
        + ("\n".join(f"- {t}" for t in r["task_titles"]) or f"- ошибка: {r['error']}")
        + ("\n\nПроверка кодом:\n" + "\n".join(f"- {m}" for m in r["code_findings"]) if r["code_findings"] else "")
        + ("\n\nРазвесовка навыков по заданиям (как на вкладке паспорта):\n\n" + markdown(r["skills_table"]) if r["skills_table"] else "")
        for r in rows
    )
    note = json.loads(PRICES.read_text(encoding="utf-8"))["status"]
    (out / "summary.md").write_text(
        f"# Сравнение моделей — «{request.brief.title}»\n\n{table(rows)}\n\n{note}\n\n{details}\n", encoding="utf-8"
    )
    print(table(rows))


if __name__ == "__main__":
    asyncio.run(main())
