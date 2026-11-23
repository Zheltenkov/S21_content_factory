"""What one project cost, the whole chain: the reading of the inputs, the concept and its review, the dialogue, every
part of the content, the critics, the searches — and each reply in the two chats. For the developer, not for the
methodologist: the page does not show it.

    uv run python -m project_generator_lab.usage                # the last projects, a line each
    uv run python -m project_generator_lab.usage d3f55f69       # one project by stage — by the id of its dialogue,
                                                                # of a version of its concept or of its content draft
    (Windows: .tmp\\lab\\usage.cmd [id])

The same account as JSON, for the project's owner: GET /api/usage/{id}.

Two sums are given and they are not the same thing. «По прайс-листу» — tokens by concept/config/model_prices.json, in
dollars: comparable between models, and silent about a model the list does not have (the report says how many calls
were left out). «Списано» — what the gateway itself said each call cost: Polza names it beside the tokens, in roubles
(«cost_rub» — «списано с баланса клиента»), the search included. That is what was really charged; a gateway that
names no such sum leaves it unknown, and the report says how many calls named one.

The calls are written by the adapters of the models (core/usage.py) since 07.10.2026: a project made before has no
account, and the report says so instead of showing zeros.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException

from .core import usage
from .core.access import current_owner

PACKAGE = Path(__file__).resolve().parent
PRICES = PACKAGE / "concept" / "config" / "model_prices.json"
SINCE = "07.10.2026"
# The stage of the chain a call belongs to, by what it asked the model for, in the order of the chain.
STAGES = {
    "InputCard": "Замысел · разбор входов", "AnswerCheck": "Замысел · разбор ответа методолога", "Proposal": "Замысел · диалог",
    "Concept": "Замысел · план заданий", "GroundedReview": "Замысел · проверка замысла", "SearchAnswer": "Замысел · проверка актуальности (поиск)",
    "StyleDraft": "Контент · стиль", "SummaryDraft": "Контент · название и аннотация",
    "TitledSummaryDraft": "Контент · название и аннотация",
    "TaskDraft": "Контент · задания", "CorpusTaskDraft": "Контент · задания", "PlainTaskDraft": "Контент · задания", "MaterialDraft": "Контент · материалы",
    "TheoryPlanDraft": "Контент · подача теории", "TheoryDraft": "Контент · теория", "NotedTheoryDraft": "Контент · теория",
    "LinkSelection": "Контент · отбор ссылок", "ExcerptPick": "Контент · выдержка из первоисточника", "FrameDraft": "Контент · введение и заключение",
    "search_text": "Контент · поиск (ссылки, факты, первоисточник)",
    "CriticReport": "Контент · методический критик", "FactReport": "Контент · проверка фактов", "ContentRevision": "Контент · диалог",
    "EvaluationBatch": "Контент · проверка по критериям",
}
# An id of a project or its first characters, as «content-d3f55f69» names a draft: six at least, so that a slip of
# the hand names nothing; hexadecimal and hyphens only — it is looked for among the names of files.
PREFIX = re.compile(r"[0-9a-f][0-9a-f-]{5,35}")
router = APIRouter()


def runs_dir() -> Path:
    from .concept import runs

    return runs.RUNS_DIR


def content_dir() -> Path:
    from .content import store

    return store.CONTENT_DIR


def threads(owner: str | None = None) -> list[dict[str, Any]]:
    """The dialogues kept on this server, the newest first: id, runs, title, owner, when begun. Read as plain data — a
    file an older or a newer version wrote has its ids and its title all the same."""
    found = []
    folder = runs_dir() / "threads"
    for path in folder.glob("*.json") if folder.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            one = {"id": str(data["id"]), "runs": [str(run) for run in data["runs"]], "owner": str(data.get("owner_id", "")),
                   "title": str((data.get("draft") or {}).get("title", "")), "created_at": str(data.get("created_at", "")),
                   "replies": sum(m.get("role") == "methodologist" for m in data.get("messages", []) if isinstance(m, dict))}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        if owner is None or one["owner"] == owner:
            found.append(one)
    return sorted(found, key=lambda one: one["created_at"], reverse=True)


def lone_runs(begins: str = "", owner: str | None = None, taken: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """Runs that are in no dialogue — begun on the page without a chat — as projects of one version each. `begins` —
    only those whose id begins so: the file of a run is named by its id."""
    found = []
    folder = runs_dir()
    for path in folder.glob(f"{begins}*.json") if folder.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            one = {"id": str(data["id"]), "runs": [str(data["id"])], "owner": str(data.get("owner_id", "local")),
                   "title": str(data["request"]["brief"].get("title", "")), "created_at": str(data.get("created_at", "")), "replies": 0}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        if one["id"] not in taken and (owner is None or one["owner"] == owner):
            found.append(one)
    return sorted(found, key=lambda one: one["created_at"], reverse=True)


def mine(dialogues: list[dict[str, Any]], owner: str | None) -> tuple[list[dict[str, Any]], frozenset[str]]:
    """The dialogues of this owner, and every id a dialogue of anyone has: a run that stands in a dialogue is not a
    project by itself, whoever the dialogue belongs to."""
    taken = frozenset(key for one in dialogues for key in (one["id"], *one["runs"]))
    return [one for one in dialogues if owner is None or one["owner"] == owner], taken


def projects(owner: str | None = None) -> list[dict[str, Any]]:
    """Every project kept on this server, the newest first: the dialogues, and the runs no dialogue has."""
    dialogues, taken = mine(threads(), owner)
    return sorted([*dialogues, *lone_runs("", owner, taken)], key=lambda one: one["created_at"], reverse=True)


def project_of(ident: str, owner: str | None = None) -> dict[str, Any] | None:
    """The project an id belongs to: the id of its dialogue, of a version of its concept (a content draft has its
    version's id), or the first characters of either — as «content-d3f55f69» names a draft. None — no project of this
    owner has it; characters that fit several ids name none."""
    wanted = ident.strip().lower().removeprefix("content-")
    if not PREFIX.fullmatch(wanted):
        return None
    dialogues, taken = mine(threads(), owner)
    named = {key for one in dialogues for key in (one["id"], *one["runs"]) if key.startswith(wanted)}
    lone = lone_runs(wanted, owner, taken)
    if len(named | {one["id"] for one in lone}) != 1:
        return None
    if lone:
        return lone[0]
    # A version opened as a dialogue of its own stands in two dialogues: the project is the one that has more of the
    # versions, and of two alike — the one begun first.
    fits = [one for one in dialogues if named & {one["id"], *one["runs"]}]
    return min(fits, key=lambda one: (-len(one["runs"]), one["created_at"]))


def content_replies(run_ids: list[str]) -> int:
    """How many steps the methodologist made in the content chats of these runs: a chat keeps a pressed chip of the
    route as a message of the methodologist, so these are the messages and the chips together. What a typed message
    cost is the row of the dialogue in the account: a chip of the route asks the model for a part, not for a reply."""
    count = 0
    for run_id in run_ids:
        path = content_dir() / f"{run_id}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, ValueError):
            continue
        count += sum(m.get("role") == "methodologist" for m in data.get("messages", []) if isinstance(m, dict))
    return count


def report(project: dict[str, Any], owner: str | None = None) -> dict[str, Any]:
    """The account of one project: every call written under its dialogue and under each version of its concept — the
    content draft of a version has that version's id."""
    scopes = list(dict.fromkeys([project["id"], *project["runs"]]))
    calls = usage.calls_of(scopes, owner)
    prices = usage.read_prices(PRICES)
    drafts = [run for run in project["runs"] if (content_dir() / f"{run}.json").is_file()]
    return {
        "project": project["id"], "title": project["title"], "created_at": project["created_at"], "concept_versions": len(project["runs"]),
        "content_drafts": drafts, "replies": {"concept": project["replies"], "content": content_replies(project["runs"])},
        "recorded": bool(calls), "since": SINCE, **usage.summary(calls, prices, STAGES),
        "unpriced_models": sorted({call.model for call in calls if call.model not in prices}),
    }


@router.get("/api/usage/{ident}")
async def project_usage(ident: UUID) -> dict[str, Any]:
    """What the project of this dialogue, run or draft cost — for its owner."""
    owner = current_owner.get()
    project = project_of(str(ident), owner)
    if project is None:
        raise HTTPException(404, "Проект не найден")
    return report(project, owner)


def number(value: float, digits: int = 0) -> str:
    return f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")


def money(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else number(value, digits)


def dollars(row: dict[str, Any], digits: int = 2) -> str:
    """The price of some calls by the list: «—» when none of them is in the list, a star when some are not."""
    if row["unpriced"] >= row["calls"]:
        return "—"
    return money(row["price_usd"], digits) + ("*" if row["unpriced"] else "")


def lines(found: dict[str, Any]) -> list[str]:
    """The account as text for a console: the whole, then a row a stage."""
    out = [f"Проект: «{found['title']}» · диалог {found['project'][:8]} · версий замысла: {found['concept_versions']} · "
           f"контент: {', '.join(run[:8] for run in found['content_drafts']) or 'не начат'}",
           f"Шагов методолога в чатах (сообщения и кнопки маршрута): замысел — {found['replies']['concept']}, контент — {found['replies']['content']}."]
    if not found["recorded"]:
        return [*out, f"Записей о вызовах нет: учёт ведётся с {found['since']}, а этот проект сделан раньше — или в нём ещё не было ни одного вызова модели."]
    out.append(f"Вызовов: {found['calls']} (не удалось: {found['failed']}, исправлений формата: {found['repairs']}) · "
               f"вход {number(found['input_tokens'])} · выход {number(found['output_tokens'])} токенов · время моделей {number(found['seconds'] / 60, 1)} мин")
    left = f" — без {found['unpriced']} вызовов моделей, которых нет в прайс-листе: {', '.join(found['unpriced_models'])}" if found["unpriced"] else ""
    out.append(f"По прайс-листу: {money(found['price_usd'])} ${left}")
    out.append(f"Списано: {money(found['charged_rub'])} ₽ — по данным шлюза (cost_rub); сумму назвали {found['charged_calls']} вызовов из {found['calls']}"
               if found["charged_calls"] else "Списано: шлюз не назвал сумму ни одного вызова (поля cost_rub в ответах нет).")
    width = max(len(row["stage"]) for row in found["stages"])
    out += ["", f"{'Этап'.ljust(width)}  {'Вызовов':>7}  {'Вход':>9}  {'Выход':>8}  {'$':>7}  {'₽':>8}"]
    for row in found["stages"]:
        out.append(f"{row['stage'].ljust(width)}  {row['calls']:>7}  {number(row['input_tokens']):>9}  {number(row['output_tokens']):>8}  "
                   f"{dollars(row, 3):>7}  {money(row['charged_rub']):>8}")
    if any(0 < row["unpriced"] < row["calls"] for row in found["stages"]):
        out.append("* без вызовов моделей, которых нет в прайс-листе")
    if len(found["models"]) > 1:
        out += ["", "По моделям: " + "; ".join(f"{row['model']} — {row['calls']} выз., {dollars(row)} $" for row in found["models"])]
    return out


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m project_generator_lab.usage", description="Сколько стоила генерация проекта: вся цепочка с диалогами.")
    parser.add_argument("id", nargs="?", default="", help="id диалога, версии замысла или черновика контента (можно первые 8 знаков, как в content-d3f55f69)")
    parser.add_argument("--last", type=int, default=10, help="без id: сколько последних проектов показать (не меньше одного)")
    parser.add_argument("--json", action="store_true", help="вывести отчёт как JSON")
    options = parser.parse_args(arguments)
    if options.id:
        project = project_of(options.id)
        if project is None:
            print(f"Проект «{options.id}» не найден (или первые знаки id подходят нескольким проектам).", file=sys.stderr)
            return 1
        found = report(project)
        print(json.dumps(found, ensure_ascii=False, indent=1) if options.json else "\n".join(lines(found)))
        return 0
    rows = [report(project) for project in projects()[:max(options.last, 1)]]
    if options.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    if not rows:
        print("Проектов нет.")
        return 0
    print(f"{'Диалог':8}  {'Начат':16}  {'Вызовов':>7}  {'$':>7}  {'₽':>8}  Проект")
    for found in rows:
        print(f"{found['project'][:8]}  {found['created_at'][:16].replace('T', ' ')}  {found['calls'] if found['recorded'] else '—':>7}  "
              f"{dollars(found) if found['recorded'] else '—':>7}  {money(found['charged_rub']):>8}  {found['title'][:70]}")
    print(f"\nУчёт вызовов ведётся с {SINCE}: у проектов, сделанных раньше, записей нет. Подробно по этапам: python -m project_generator_lab.usage <id>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
