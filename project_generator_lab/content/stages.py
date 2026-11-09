"""Where the draft stands and what the methodologist can do next, computed from the state only.

Route: title and annotation → each task with its reasons, then its materials, one task at a time (task 1 first, as the
model for the rest) → theory and introduction → the whole text → critics → README by code. The chat never guesses the route: a chip shows exactly
the step computed here, and the server refuses a chip that no longer matches the state.
"""

from .handoff import STYLE_NAMES
from .models import PRESENTATIONS, TITLE_CHOICES, ContentDraft, ContentMessage, Summary, other

FIND_EXCERPT = "Найти выдержку из первоисточника"
# A chip under the proposed title for each of the other titles the assistant offered (content 0.9): «title_2» puts
# the second of them in. No model is asked.
TITLE_ACTIONS = {f"title_{place}": place - 1 for place in range(1, TITLE_CHOICES + 1)}

ROUTE = ("Этапы контента: стиль проекта → название и аннотация → задания по одному: задание с пояснением, затем его материалы → "
         "как подать теорию → теория, введение, инструкция → весь текст целиком → проверка критиками → перенос в README кодом.")
# A draft begun before the question of how the theory is given (content 0.5) has no such step and is not told of it;
# one begun before the styles has neither.
ROUTE_BEFORE_PLAN = ("Этапы контента: стиль проекта → название и аннотация → задания по одному: задание с пояснением, затем его материалы → "
                     "теория со ссылками, введение, инструкция → весь текст целиком → проверка критиками → перенос в README кодом.")
ROUTE_BEFORE_STYLES = ROUTE_BEFORE_PLAN.replace("стиль проекта → ", "")
START = {
    "action": "start", "button": "Подготовить контент", "title": "Замысел согласован",
    "text": "Дальше — учебный контент в этом же чате, по частям. Сначала покажу, что получил из замысла, и предложу, "
            "в каком стиле рассказать проект: квестом или рабочей легендой. "
            "Потом название и аннотация, затем задания по одному: задание с пояснением, почему оно такое, и сразу его материалы. "
            "После заданий спрошу, как подать теорию, и напишу её, введение и инструкцию. "
            "Весь текст вы прочитаете целиком, его проверят критики, и только потом код перенесёт его в README и чек-лист.",
}


def title_chips(summary: Summary) -> list[dict[str, object]]:
    """The titles offered that are not the one in force: a chip each, while the title is not agreed."""
    def short(title: str) -> str:
        return title if len(title) <= 60 else title[:59].rstrip() + "…"

    return [{"action": action, "button": f"Назвать «{short(summary.title_options[place])}»"}
            for action, place in TITLE_ACTIONS.items()
            if place < len(summary.title_options) and summary.title_options[place] != summary.title]


def planned(draft: ContentDraft) -> list[int]:
    return [t.number for t in draft.handoff.tasks]


def missing_tasks(draft: ContentDraft) -> list[int]:
    return [n for n in planned(draft) if draft.task(n) is None]


def draft_tasks(draft: ContentDraft) -> list[int]:
    return [t.number for t in sorted(draft.tasks, key=lambda t: t.number) if t.status == "draft"]


def planned_materials(draft: ContentDraft) -> list[str]:
    return [m.path for m in draft.materials if m.status == "planned"]


def draft_materials(draft: ContentDraft) -> list[str]:
    return [m.path for m in draft.materials if m.status == "draft"]


def task_materials(draft: ContentDraft, number: int, status: str) -> list[str]:
    """Materials first needed in this task: they are written and agreed right after it."""
    return [m.path for m in draft.materials if m.status == status and m.tasks and min(m.tasks) == number]


def support_missing(draft: ContentDraft) -> list[str]:
    """Theory, introduction and materials not yet written, in the order they are written. Materials are normally written
    with their task; here are only those added later, from the chat."""
    return (["theory"] if draft.theory is None else []) + (["frame"] if draft.frame is None else []) \
        + [f"material:{p}" for p in planned_materials(draft)]


def support_drafts(draft: ContentDraft) -> list[str]:
    return (["theory"] if draft.theory and draft.theory.status == "draft" else []) \
        + (["frame"] if draft.frame and draft.frame.status == "draft" else []) \
        + [f"material:{p}" for p in draft_materials(draft)]


def latest_pending(draft: ContentDraft) -> ContentMessage | None:
    return next((m for m in reversed(draft.messages) if m.status == "pending"), None)


def review_stale(draft: ContentDraft) -> bool:
    return bool(draft.review and draft.review.digest != draft.digest())


def links_chip(draft: ContentDraft) -> list[dict[str, str]]:
    """A second search is offered while any theory section has fewer than two links — where links are of use at all."""
    if draft.theory and draft.online() and any(len(t.links) < 2 for t in draft.theory.topics):
        found = any(t.links for t in draft.theory.topics)
        return [{"action": "find_links", "button": "Найти ещё ссылки для теории" if found else "Найти ссылки для теории"}]
    return []


def excerpt_chip(draft: ContentDraft) -> list[dict[str, str]]:
    """The search for an excerpt of a primary source is offered again where the plan asks for one: none was found, the
    one found was taken out, or another is wanted. Nothing else can put a quotation into the text."""
    if draft.theory and draft.theory_plan and draft.theory_plan.english == "yes":
        return [{"action": "find_excerpt", "button": "Найти другую выдержку из первоисточника" if draft.theory.quoted() else FIND_EXCERPT}]
    return []


def numbers(values: list[int]) -> str:
    if len(values) == 1:
        return f"задание {values[0]}"
    if values == list(range(values[0], values[-1] + 1)):
        return f"задания {values[0]}–{values[-1]}"
    return "задания " + ", ".join(map(str, values))


def next_step(draft: ContentDraft | None, blocks: list[str] | None = None) -> dict[str, object]:
    step: dict[str, object] = {"action": None, "number": None, "button": "", "title": "", "text": "", "route": ROUTE,
                               "edit_hint": "", "alternatives": []}
    if draft is None:
        return {**step, **START}
    if not draft.plans_theory():
        step["route"] = ROUTE_BEFORE_PLAN if draft.styled() else ROUTE_BEFORE_STYLES
    if draft.busy:
        return {**step, "title": draft.busy,
                "text": "Готовые части появляются в чате сразу. Пока идёт работа, можно писать правки к уже готовым частям."}
    if latest_pending(draft):
        return {**step, "action": "apply_pending", "button": "Применить правки", "title": "Есть правки без решения",
                "text": "Примените, измените или отклоните правки выше. Кнопка применит последние предложенные правки."}
    # The style comes before any text: every part is written in it. A draft begun before the styles has none.
    if draft.styled() and draft.style is None:
        return {**step, "action": "write_style", "button": "Предложить стиль проекта", "title": "Начнём со стиля",
                "text": "Сначала договоримся, как рассказать проект: квестом, как в бассейне, или рабочей легендой. "
                        "Я предложу стиль и сюжет по замыслу и скажу почему."}
    if draft.style and draft.style.status == "draft" and draft.style.stale():
        # The profile was changed and the style has not been written for it: the chip pressed and the job failed, or
        # the profile changed by words. What stands on the card is the story of the other style.
        name, another = STYLE_NAMES[draft.style.profile], other(draft.style.profile)
        return {**step, "action": "write_style", "button": f"Написать сюжет для стиля «{name}»", "title": f"Стиль сменён на «{name}»",
                "text": "Сюжет на карточке написан для прежнего стиля. Напишу его заново для выбранного — или оставьте как есть, "
                        "если он подходит.",
                "edit_hint": "Что поменять в стиле или сюжете…",
                "alternatives": [{"action": "approve_style", "button": "Сюжет подходит и так — предложить название и аннотацию"},
                                 {"action": "switch_style", "button": f"Вернуть стиль «{STYLE_NAMES[another]}»"}]}
    if draft.style and draft.style.status == "draft":
        another = other(draft.style.profile)
        asked = (" На вопросы по сюжету ответьте в чате: я впишу ответы в сюжет. Без ответов напишу по своим допущениям."
                 if draft.style.questions else "")
        return {**step, "action": "approve_style", "button": "Стиль подходит — предложить название и аннотацию",
                "title": "Стиль проекта предложен",
                "text": "Проверьте, как я понял сюжет: по нему будут написаны все части. Стиль можно сменить кнопкой или словами, "
                        "сюжет — поправить в чате." + asked,
                "edit_hint": "Что поменять в стиле или сюжете…",
                "alternatives": [{"action": "switch_style", "button": f"Сменить стиль на «{STYLE_NAMES[another]}»"}]}
    if draft.summary is None:
        return {**step, "action": "write_summary", "button": "Предложить название и аннотацию", "title": "Начнём с названия",
                "text": "Название и аннотация — обещание проекта: что участник сделает и что получит. Смотрим их вместе."}
    if draft.summary.status == "draft":
        chips = title_chips(draft.summary)
        choose = "Название — одно из предложенных: другое ставится кнопкой, своё — словами в чате. " if chips else ""
        return {**step, "action": "approve_summary", "button": "Название и аннотация подходят — писать задание 1",
                "title": "Название и аннотация готовы",
                "text": choose + "Нужна правка — напишите, что поменять. Если всё верно — пишу задание 1 с пояснением, почему оно такое: "
                        "оно станет образцом для остальных.", "edit_hint": "Что поменять в названии или аннотации…", "alternatives": chips}
    # Tasks strictly one by one: write → the methodologist agrees → the next one. The first is the model for the rest.
    order = planned(draft)
    for i, n in enumerate(order):
        task = draft.task(n)
        if task is None:
            return {**step, "action": "write_task", "number": n, "button": f"Написать задание {n}",
                    "title": "Название согласовано" if i == 0 else f"Задание {order[i - 1]} согласовано",
                    "text": "Первое задание станет образцом для остальных." if i == 0 else "Пишу следующее задание по образцу первого."}
        after = f"писать задание {order[i + 1]}" if i + 1 < len(order) else "как подать теорию" if draft.plans_theory() else "теория и введение"
        if task.status == "draft":
            needs = task_materials(draft, n, "planned")
            follow = f"материалы к нему ({len(needs)})" if needs else after
            text = ("Сверьте задание с пояснением «Почему такое задание»: какой пункт замысла и ОР оно реализует, почему сдача "
                    "это доказывает, что участник решает сам и как проверит пир. Пояснение пишет та же модель — "
                    "проверяйте само задание по замыслу, а не убедительность пояснения.")
            if i == 0 and len(order) > 1:
                text += " Остальные задания пойдут по этому образцу."
            return {**step, "action": "approve_task", "number": n, "button": f"Задание {n} подходит — {follow}",
                    "title": f"Задание {n} готово", "text": text, "edit_hint": f"Что поменять в задании {n}…"}
        if task_materials(draft, n, "planned"):
            return {**step, "action": "write_materials", "number": n, "button": f"Подготовить материалы к заданию {n}",
                    "title": f"Задание {n} согласовано", "text": "Готовлю файлы, без которых задание не сделать."}
        if task_materials(draft, n, "draft"):
            return {**step, "action": "approve_materials", "number": n, "button": f"Материалы к заданию {n} подходят — {after}",
                    "title": f"Материалы к заданию {n} готовы",
                    "text": "Проверьте, что в файлах те же люди, числа и сроки, что в задании, что это сырьё для работы, а не ответ, "
                            "и что без них задание действительно не сделать.",
                    "edit_hint": f"Что поменять в материалах задания {n}…"}
    # How the theory is given comes before the theory: it decides what is written — a pointer or a summary to work from.
    plan = draft.theory_plan
    if draft.plans_theory() and draft.theory is None and plan is None:
        return {**step, "action": "write_theory_plan", "button": "Предложить, как подать теорию", "title": "Задания и материалы согласованы",
                "text": "Перед теорией договоримся, как её подать: короткой главой в README, конспектом в README или в материалах. "
                        "Я предложу вариант по замыслу, скажу почему и покажу пример."}
    if draft.plans_theory() and draft.theory is None and plan is not None and plan.status == "draft":
        others = [{"action": f"plan_{name}", "button": f"Выбрать: {said}"} for name, said in PRESENTATIONS.items() if name != plan.presentation]
        asked = " На вопросы ответьте в чате: я учту ответы." if plan.questions else ""
        if plan.stale():
            # The presentation was chosen by the chip or by words: the reasons and the sample on the card are of another one.
            return {**step, "action": "approve_theory_plan", "button": "Так и сделать — писать теорию и введение",
                    "title": f"Подача теории: {PRESENTATIONS[plan.presentation]}",
                    "text": "Вариант выбрали вы; пример на карточке написан для другого. Могу показать пример для выбранного — "
                            "или сразу пишу теорию." + asked,
                    "edit_hint": "Что поменять в подаче теории…",
                    "alternatives": [{"action": "write_theory_plan", "button": "Показать пример для этого варианта"}, *others]}
        return {**step, "action": "approve_theory_plan", "button": "Так и сделать — писать теорию и введение",
                "title": "Как подать теорию",
                "text": "Проверьте, подходит ли вариант: от него зависит, что я напишу — короткую справку со ссылками или конспект, "
                        "по которому можно работать без интернета. Сменить вариант можно кнопкой или словами." + asked,
                "edit_hint": "Что поменять в подаче теории…", "alternatives": others}
    if support_missing(draft):
        if draft.plans_theory():
            return {**step, "action": "write_support", "button": "Подготовить теорию и введение", "title": "Задания и материалы согласованы",
                    "text": "Пишу теорию так, как договорились, затем введение, инструкцию и заключение. Ссылки ищет Perplexity, код "
                            "открывает каждую страницу, модель выбирает подходящие."}
        return {**step, "action": "write_support", "button": "Подготовить теорию и введение", "title": "Задания и материалы согласованы",
                "text": "Теоретический блок — 2–5 коротких разделов со ссылками: ищет Perplexity, код открывает каждую страницу, "
                        "модель выбирает подходящие. Потом введение, инструкция и заключение."}
    if support_drafts(draft):
        return {**step, "action": "approve_support", "button": "Всё подходит — показать весь текст", "title": "Теория и введение готовы",
                "text": "Проверьте, что теория указывает направление, а не решает задания, и что введение описывает ту же ситуацию, "
                        "что задания и материалы. Дальше покажу весь материал одним текстом.",
                "edit_hint": "Что поменять в теории или введении…", "alternatives": [*links_chip(draft), *excerpt_chip(draft)]}
    if draft.review is None:
        return {**step, "action": "run_review", "button": "Проверить критиками", "title": "Весь текст собран",
                "alternatives": [*links_chip(draft), *excerpt_chip(draft)],
                "text": "Прочитайте материал целиком выше. Затем два критика: методический (отдельная модель) проверит P2P, "
                        "действия под ОР, проверку пиром и уровень, фактический (поиск) — актуальность инструментов и ссылок. "
                        "Их замечания — советы, решаете вы."}
    if blocks:
        return {**step, "title": "Перед переносом в README нужно поправить", "text": " ".join(blocks) + " Напишите в чат, что изменить.",
                "alternatives": [{"action": "run_review", "button": "Проверить критиками ещё раз"}]}
    again = [{"action": "run_review", "button": "Проверить критиками ещё раз"}, *links_chip(draft), *excerpt_chip(draft)]
    if not draft.assembled_at:
        stale = " Текст менялся после проверки критиками — при желании проверьте ещё раз." if review_stale(draft) else ""
        return {**step, "action": "assemble", "button": "Перенести в README и чек-лист", "title": "Критики высказались",
                "text": "Учтите замечания критиков, которые считаете верными: кнопка «Учесть» у замечания превратит его в правку. "
                        "Затем код перенесёт текст в README.md и check-list.yml по шаблону школы — модель в этом не участвует." + stale,
                "alternatives": again}
    return {**step, "title": "README и чек-лист готовы",
            "text": "Файлы собираются из текущего черновика: после правок в чате достаточно скачать их снова.",
            "alternatives": again}
