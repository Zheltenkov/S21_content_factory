"""Deterministic handoff, model and search for content tests; never used by the application."""

import asyncio
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from project_generator_lab.content.handoff import ConceptHandoff, PlannedTask, SourceText
from project_generator_lab.content.models import (
    ContentRevision,
    CriticFinding,
    CriticReport,
    ExcerptPick,
    FactItem,
    FactReport,
    FrameDraft,
    LinkPick,
    LinkSelection,
    MaterialDraft,
    MaterialNeed,
    NotedTheoryDraft,
    NotedTopicDraft,
    Rationale,
    StyleDraft,
    SummaryDraft,
    TaskOpening,
    TheoryDraft,
    TheoryPlanDraft,
    TitledSummaryDraft,
    TopicDraft,
)
from project_generator_lab.core.llm import CallMetrics, ModelUnavailable
from project_generator_lab.core.web import Page

TASK_TEXT = {
    1: ("Роли в команде", "Сопоставь роли и зоны ответственности по данным команды", "team.csv"),
    2: ("План встречи", "Составь план kick-off встречи и зафиксируй договорённости", "meeting_template.md"),
    3: ("Бэклог первого спринта", "Разложи фичу на задачи и выбери три на спринт", ""),
}
SEARCH_SOURCES = ["https://docs.example.org/roles", "https://docs.example.org/meetings", "https://docs.example.org/backlog"]
DEAD = "https://dead.example.org/404"
FACT_SOURCE = "https://news.example.org/tracker"


async def open_pages(urls: list[str]) -> list[Page]:
    """Every address opens, except the dead one: what code would find on the network."""
    return [Page(url=u, title=f"Страница {u.rsplit('/', 1)[-1]}", text="Начало страницы") for u in urls if u != DEAD]


# A primary source in English and a page in Russian about it: what the search for an excerpt may name.
PRIMARY = "https://git-scm.example.org/docs/roles"
RETOLD = "https://habr.example.org/roles-retold"
QUOTE = "A role is a set of responsibilities, not a job title. One person may hold several roles, and a role may be shared by several people."
PRIMARY_TEXT = f"Roles Guide Contents Search. {QUOTE} The rest of this guide explains how a team agrees on them and where the agreement is kept."


async def read_pages(urls: list[str]) -> list[Page]:
    """The pages opened to take an excerpt from, with enough of their text."""
    texts = {PRIMARY: PRIMARY_TEXT, RETOLD: "Пересказ руководства о ролях: роль — это набор обязанностей, а не должность в команде."}
    return [Page(url=u, title="Roles Guide" if u == PRIMARY else "Пересказ", text=texts[u]) for u in urls if u in texts]


def handoff_fixture(run_id: str | None = None, optional_last: bool = False) -> ConceptHandoff:
    run_id = run_id or str(uuid4())
    tasks = [PlannedTask(number=n, title=title, action=action, deliverable=f"Файл задания {n} в src/",
                         peer_check=f"Пир открывает файл задания {n} и сверяет с данными", independent_choice="Формат таблицы",
                         support="Документация трекера", hours=4, outcome_numbers=[n], optional=optional_last and n == 3)
             for n, (title, action, _) in TASK_TEXT.items()]
    return ConceptHandoff(
        run_id=run_id, approved_at="2026-09-29T10:00:00+00:00", concept_version="0.6", title="Основы работы продакт-менеджера",
        description="Запуск фичи в EdTech-команде", outcomes=["Разграничивать роли в команде", "Фиксировать договорённости", "Декомпозировать фичу"],
        audience="Начинающие продакт-менеджеры", start_level="нулевой", learner_term="участник", hours=12, tools="Таблицы, трекер задач",
        storytelling_type="рабочий кейс", storytelling="Новый продакт в команде приложения для изучения языков",
        customer_requirements="Только отечественные сервисы", program_context="Блок «Продукт»: проект 1 из 3.", tasks=tasks,
        decisions=["предложено: Сдача таблицей — пир сверяет роли с данными"], open_questions=["Какой трекер доступен в кампусе?"],
        review_notes=["Задание 3 без явного критерия приоритета."],
        sources=[SourceText(name="бриф.docx", role="customer", text="Бриф заказчика", total_chars=14),
                 SourceText(name="ответы.docx", role="reviewer", text="Эталон: CEO отвечает за сроки", total_chars=30)],
        rules={"program_rules": "# Правила программы\n- P2P"},
    )


def metrics(model: str = "fake-model") -> CallMetrics:
    return CallMetrics(model, 1, 100, 50)


class FakeModel:
    """Scripted answers by schema. Records every call; can fail a chosen call to test recovery."""

    def __init__(self, name: str = "fake-model") -> None:
        self.name = name
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        # The schema each call was asked to answer by: it goes to the real model with the prompt.
        self.schemas: list[type] = []
        self.revisions: list[ContentRevision] = []
        self.findings: list[CriticFinding] = []
        self.fail_on: set[str] = set()
        # The style the assistant would choose by itself, and what it would ask about the story.
        self.profile = "legend"
        self.questions: list[str] = []
        # Lines the model gives as examples of a task, by its number.
        self.examples: dict[int, list[str]] = {}
        self.delay = 0.0
        self.extra_materials = 0
        # How the assistant would give the theory by itself, and what it would ask about it.
        self.presentation, self.internet, self.english = "chapter", "yes", "no"
        self.plan_questions: list[str] = []
        # The excerpt the model marks in the page it was shown; None — it finds no place worth taking.
        self.quote: str | None = QUOTE
        self.excerpt_topic: int | None = None
        # The titles the model offers since 0.9, the first being the one it thinks best.
        self.titles: list[str] = ["Основы работы продакт-менеджера", "Три недели до фичи", "Кто отвечает за бэклог?"]

    async def generate(self, schema, system, payload):
        data = json.loads(payload)
        # A task and a theory are «TaskDraft» and «TheoryDraft» to the tests whichever of their two shapes is asked for.
        called = ("TaskDraft" if issubclass(schema, TaskOpening) else "TheoryDraft" if schema is NotedTheoryDraft
                  else "SummaryDraft" if schema is TitledSummaryDraft else schema.__name__)
        self.calls.append((called, system, data))
        self.schemas.append(schema)
        if self.delay:
            await asyncio.sleep(self.delay)
        # A task is «TaskDraft» to the tests whichever of the two shapes its draft is asked for.
        task = issubclass(schema, TaskOpening)
        key = called + (f":{data['planned_task']['number']}" if task else f":{data['material']['path']}" if schema is MaterialDraft else "")
        if key in self.fail_on or called in self.fail_on or schema.__name__ in self.fail_on:
            raise ModelUnavailable("Шлюз модели ответил ошибкой 503. Попробуйте повторить запуск.")
        if schema is StyleDraft:
            # The model answers with its own choice even when a profile was given: code is what keeps the given one.
            return StyleDraft(profile=self.profile, reason="Сюжета серии во входных данных нет, тема нетехническая: рабочая легенда яснее.",
                              story="Ты — новый продакт-менеджер в EdTech-команде: фичу ждут через три недели.",
                              questions=list(self.questions)), metrics(self.name)
        if schema is TitledSummaryDraft:
            return TitledSummaryDraft(titles=list(self.titles),
                                      annotation="В этом проекте ты запустишь фичу в EdTech-команде: от ролей до бэклога."), metrics(self.name)
        if schema is SummaryDraft:
            return SummaryDraft(title="Основы работы продакт-менеджера",
                                annotation="В этом проекте ты запустишь фичу в EdTech-команде: от ролей до бэклога."), metrics(self.name)
        if issubclass(schema, TaskOpening):
            number = data["planned_task"]["number"]
            title, action, material = TASK_TEXT[number]
            needs = [MaterialNeed(path=material, kind="data" if material.endswith(".csv") else "template",
                                  purpose=f"Исходные данные задания {number}", contains="Роли и ожидания участников команды",
                                  not_contains="Готовое распределение ролей")] if material else []
            needs += [MaterialNeed(path=f"extra{i}.md", kind="reference", purpose="Справка по инструменту", contains="Как пользоваться")
                      for i in range(1, self.extra_materials + 1)]
            return schema(title=title, goal=f"Научиться: {action.lower()}.", context=f"Ситуация задания {number}: команда ждёт решения.",
                             steps=[f"{action} (шаг {number}).", f"Опиши, почему выбрал именно такой вариант, для задания {number}."],
                             expected=[f"В src/task{number}.md есть решение с обоснованием."], constraints=["Без сторонних шаблонов"],
                             result=f"Файл src/task{number}.md.", transition="" if number == 3 else f"Результат понадобится в задании {number + 1}.",
                             peer_checks=[f"Файл src/task{number}.md открывается.", f"Попроси рассказать, как принималось решение в задании {number}."],
                             needs=[f"Понятие для задания {number}"], materials=needs,
                             **({"examples": self.examples.get(number, [])} if "examples" in schema.model_fields else {}),
                             rationale=Rationale(concept=f"Реализует пункт замысла {number}: {action}.", evidence="Таблица показывает сопоставление.",
                                                 choice="Формат таблицы", peer="Пир открывает файл и сверяет с данными.")), metrics(self.name)
        if schema is TheoryPlanDraft:
            # The model answers with its own choice even when a presentation was given: code is what keeps the given one.
            return TheoryPlanDraft(presentation=self.presentation, internet=self.internet, english=self.english,
                                   reason="Интернет у участников есть, аудитория не новички: короткой главы со ссылками хватит.",
                                   sample="Роль — это набор обязанностей, а не должность.\nГде изучить: руководство по ролям.",
                                   questions=list(self.plan_questions)), metrics(self.name)
        if schema is NotedTheoryDraft:
            numbers = [t["number"] for t in data["tasks"]]
            return NotedTheoryDraft(topics=[NotedTopicDraft(
                title=f"Тема для задания {n}", text=f"Понятие {n} — это то, что нужно в задании {n}; направление, а не готовый вывод.",
                tasks=[n], question=f"Как понятие {n} меняет твоё решение?", keywords=[f"ключ {n}"],
                notes=(f"Понятие {n} — это способ договориться о работе. Оно устроено так: сначала называют участников, затем их "
                       f"обязанности. Пример на других данных: в команде из двух человек один отвечает за сроки, другой — за качество. "
                       f"Типичная ошибка — путать роль и должность."))
                for n in numbers]), metrics(self.name)
        if schema is ExcerptPick:
            if self.quote is None:
                return ExcerptPick(note="на страницах нет места, где автор объясняет идею"), metrics(self.name)
            return ExcerptPick(candidate=1, topic=self.excerpt_topic or data["topics"][0]["number"], title="Руководство по ролям: определение роли",
                               quote=self.quote), metrics(self.name)
        if schema is TheoryDraft:
            numbers = [t["number"] for t in data["tasks"]]
            return TheoryDraft(topics=[TopicDraft(title=f"Тема для задания {n}", text=f"Понятие {n} — это то, что нужно в задании {n}; направление без ответа.",
                                                  tasks=[n], question=f"Как понятие {n} меняет твоё решение?", keywords=[f"ключ {n}"])
                                       for n in numbers]), metrics(self.name)
        if schema is LinkSelection:
            candidates = {c["url"]: c["number"] for c in data["candidates"]}
            return LinkSelection(picks=[LinkPick(topic=t["number"], candidate=candidates[SEARCH_SOURCES[t["number"] - 1]],
                                                 title=f"Документация к теме {t['number']}", why="Раздел про основы")
                                        for t in data["topics"] if SEARCH_SOURCES[t["number"] - 1] in candidates]
                                 + [LinkPick(topic=1, candidate=99, title="Выдуманный номер")]), metrics(self.name)
        if schema is FrameDraft:
            return FrameDraft(introduction="Ты — новый продакт-менеджер в EdTech-команде. CEO «Петя» хочет фичу через три недели.",
                              instruction=["Обязательно: таблицы и трекер задач.", "Допускается работа с нейросетями, если ты проверяешь выводы."],
                              conclusion="Ты прошёл путь от ролей до бэклога."), metrics(self.name)
        if schema is FactReport:
            candidates = {c["url"]: c["number"] for c in data["candidates"]}
            return FactReport(summary="Один пункт требует внимания.", items=[
                FactItem(part="task:3", topic="Трекер задач", status="outdated", finding="Сервис закрылся в 2026 году.",
                         suggestion="Заменить на отечественный аналог.", source_numbers=[candidates[FACT_SOURCE]]),
                FactItem(part="topic:1", topic="Ссылка", status="current", finding="Открывается.", source_numbers=[1]),
                FactItem(part="nowhere", topic="Слухи", status="outdated", finding="Без источника.", source_numbers=[77]),
            ]), metrics(self.name)
        if schema is MaterialDraft:
            path = data["material"]["path"]
            content = "role,person,expectation\nCEO,Петя,Запуск через три недели\nDev,Саша,Понятное ТЗ\n" if path.endswith(".csv") else "# Шаблон встречи\n\n## Цели\n\n## Договорённости\n"
            return MaterialDraft(content=content, note="Вымышленные данные команды."), metrics(self.name)
        if schema is CriticReport:
            return CriticReport(summary="Одна проблема с проверкой пиром.", findings=list(self.findings)), metrics(self.name)
        if schema is ContentRevision:
            return (self.revisions.pop(0) if self.revisions else ContentRevision(reply="Уточните, какую часть поменять?")), metrics(self.name)
        raise AssertionError(schema)


class FakeSearch:
    """Search model: free text; links come from its citations and from addresses in the text."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail = False
        self.no_links = False

    async def text(self, system, payload):
        data = json.loads(payload)
        primary = data.get("looking_for") == "primary_source"
        self.calls.append(("primary" if primary else "sources" if "topics" in data and "tasks" not in data else "facts", data))
        if self.fail:
            raise ModelUnavailable("Шлюз поисковой модели ответил ошибкой 404.")
        if self.no_links:
            return "Подходящих источников не нашлось.", [], metrics("perplexity/sonar-pro")
        if primary:
            # A retelling in Russian is named first: code keeps the pages written in English.
            return f"Раздел 1 — пересказ — {RETOLD}\nРаздел 1 — Roles Guide — {PRIMARY} — руководство авторов", [RETOLD, PRIMARY], metrics("perplexity/sonar-pro")
        if "tasks" in data:
            return f"task:3 — сервис закрылся, см. {FACT_SOURCE}", [FACT_SOURCE], metrics("perplexity/sonar-pro")
        text = "\n".join(f"Раздел {t['number']}\nДокументация — {SEARCH_SOURCES[t['number'] - 1]} — основы" for t in data["topics"])
        return text, [*SEARCH_SOURCES, DEAD], metrics("perplexity/sonar-pro")


def wait_idle(client, run_id: str, timeout: float = 5.0) -> dict[str, Any]:
    """Background writers finish after the response; the page polls the same way."""
    deadline = time.monotonic() + timeout
    while True:
        view: dict[str, Any] = client.get(f"/api/content/{run_id}").json()
        if not view.get("busy"):
            return view
        if time.monotonic() > deadline:
            raise AssertionError(f"draft still busy: {view.get('busy')}")
        time.sleep(0.02)


# ---------- the page of a document, asked what it shows ----------

PROBE = Path(__file__).resolve().parents[1] / "js" / "probe_document.mjs"


def probe(given: str, argument: str) -> Any:
    """The page's own script (static/document.js) run by node on a text: tests/js/probe_document.mjs says what it made."""
    done = subprocess.run(["node", str(PROBE), argument], input=given, capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout)


def shown(markdown: str, search: str = "?run=r1&doc=preview") -> dict[str, Any]:
    """What the page of a document shows of a text: its tables, headings, links, numbered lists, code."""
    found: dict[str, Any] = probe(markdown, search)
    return found


def tables_written(markdown: str) -> int:
    """Tables of a markdown text: a row of dashes under a head, outside code."""
    count, fenced = 0, False
    for line in markdown.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and "|" in line and re.fullmatch(r"\s*(?:>\s*)*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*", line):
            count += 1
    return count
