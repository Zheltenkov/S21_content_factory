"""Freshness check: a web-search model looks for what in the concept depends on time and may be outdated.

It runs only on the methodologist's request, after a concept exists, and never edits the concept or the brief.
The search model sees the concept, not the chat or the uploaded materials. A link in the report is always one
the provider returned as a citation: [n] marks pointing elsewhere are counted and dropped.
"""

import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import date
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException

from ..core.llm import ModelUnavailable
from ..core.search import ask, citations, pick, search_model
from ..core.web import Page, open_pages
from .models import CheckedIdea, CheckedItem, Concept, Freshness, Run, SearchAnswer

PROMPT = Path(__file__).parent / "prompts" / "freshness.md"
# Что открывает ссылки и что ищет: подменяются в тестах, чтобы не ходить в сеть.
Opener = Callable[[list[str]], Awaitable[Sequence[Page]]]
Search = Callable[[str, str], Awaitable[tuple[SearchAnswer, list[str], dict[str, int | str]]]]
router = APIRouter()
__all__ = ["ModelUnavailable", "citations", "search_model"]


def search_payload(run: Run, today: date) -> dict[str, object]:
    """Only what can be outdated lives here: tools, cases, tasks. No chat, no materials, no personal data."""
    brief, concept = run.request.brief, run.concept
    assert concept is not None
    return {
        "today": today.isoformat(),
        "title": brief.title,
        "description": brief.description,
        "outcomes": [line.strip() for line in brief.outcomes.splitlines() if line.strip()],
        "tools": brief.tools,
        "storytelling": brief.storytelling,
        "tasks": [{"number": i, "title": t.title, "action": t.action, "deliverable": t.deliverable, "support": t.support}
                  for i, t in enumerate(concept.tasks, 1)],
        "material_gaps": concept.material_gaps,
    }


def attach_sources(answer: SearchAnswer, sources: list[str]) -> tuple[list[CheckedItem], list[CheckedIdea], int]:
    """Replace [n] marks with the provider's links; a mark outside the list is dropped and counted."""
    dropped = 0

    def links(numbers: list[int]) -> list[str]:
        nonlocal dropped
        kept, missing = pick(sources, numbers)
        dropped += missing
        return kept

    items = []
    for item in answer.items:
        found = links(item.source_numbers)
        # A verdict without a single real source is not a verdict.
        status = item.status if found else "unclear"
        items.append(CheckedItem(**{**item.model_dump(), "status": status}, sources=found))
    ideas = [CheckedIdea(**idea.model_dump(), sources=links(idea.source_numbers)) for idea in answer.ideas]
    return items, ideas, dropped


async def search(system: str, payload: str) -> tuple[SearchAnswer, list[str], dict[str, int | str]]:
    answer, sources, metrics = await ask(SearchAnswer, system, payload)
    return answer, sources, {"model": metrics.model, "elapsed_ms": metrics.elapsed_ms,
                             "input_tokens": metrics.input_tokens, "output_tokens": metrics.output_tokens}


async def unreachable_sources(sources: list[str], opener: Opener) -> list[str]:
    """Ссылки, которые не открылись. Пустой список — не повод ходить в сеть.

    Модуль контента каждую найденную ссылку открывает и мёртвую отбрасывает, а проверка
    актуальности принимала ссылки провайдера на слово — методолог и видел в отчёте то, что
    не открывается. Здесь тот же код, но ссылка не выбрасывается: отчёт рекомендательный,
    а выброшенная ссылка унесла бы с собой и основание вывода.

    Проверка идёт **с сервера**: страница, недоступная из корпоративной сети методолога,
    отсюда может открыться. Поэтому молчание отчёта — не обещание, что ссылка откроется у всех.
    """
    if not sources:
        return []
    opened = {page.url for page in await opener(sources)}
    return [url for url in sources if url not in opened]


async def check(run: Run, today: date | None = None, *, opener: Opener = open_pages,
                search_for: Search | None = None) -> Freshness:
    answer, sources, metrics = await (search_for or search)(
        PROMPT.read_text(encoding="utf-8"),
        json.dumps(search_payload(run, today or date.today()), ensure_ascii=False))
    items, ideas, dropped = attach_sources(answer, sources)
    return Freshness(model=str(metrics["model"]), summary=answer.summary, items=items, ideas=ideas, sources=sources,
                     unreachable=await unreachable_sources(sources, opener),
                     dropped_references=dropped, elapsed_ms=int(metrics["elapsed_ms"]),
                     input_tokens=int(metrics["input_tokens"]), output_tokens=int(metrics["output_tokens"]))


def api():  # noqa: ANN201 - lazy import breaks the app ↔ freshness cycle
    from . import runs as module

    return module


@router.post("/api/runs/{run_id}/freshness")
async def run_freshness(run_id: UUID) -> Run:
    run: Run = api().load_run(run_id)
    if not isinstance(run.concept, Concept) or run.status not in {"review", "approved"}:
        raise HTTPException(409, "Актуальность проверяется после того, как замысел и проверка готовы")
    try:
        result = await check(run)
    except ModelUnavailable as error:
        raise HTTPException(502, str(error)) from error
    # Reload: the run may have been approved while the search was running.
    run = api().load_run(run_id)
    run.freshness = result
    api().save_run(run)
    return run
