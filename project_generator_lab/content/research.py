"""Links for the theory and the factual check: a small agent of three steps, each doing what it is good at.

1. The search model (Perplexity) looks for sources in free text. The links are taken by code from its citations
   in whatever format the gateway returns them, and from addresses written in the answer.
2. Code opens every link. A dead page, an error or a non-document is dropped.
3. The writer model reads the titles and first lines of the live pages and chooses the relevant ones, by number.
   It cannot type an address: a number outside the list of opened pages is dropped by code.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date

from ..core.llm import CallMetrics
from ..core.search import links_in_text
from ..core.web import Page
from .models import EXCERPT_LIMIT, ContentDraft, Excerpt, FactReport, Link, LinkSelection, TheoryTopic
from .stages import FIND_EXCERPT
from .writer import Writer


@dataclass
class Found:
    links: dict[int, list[Link]] = field(default_factory=dict)
    # Every page code opened successfully: only these may become links in the text.
    live: list[str] = field(default_factory=list)
    note: str = ""
    models: list[str] = field(default_factory=list)
    metrics: list[CallMetrics] = field(default_factory=list)


def candidates(pages: list[Page]) -> list[dict[str, object]]:
    return [{"number": i, "url": p.url, "page_title": p.title, "page_start": p.text} for i, p in enumerate(pages, 1)]


async def find_links(writer: Writer, draft: ContentDraft, topics: list[TheoryTopic], today: date) -> Found:
    h = draft.handoff
    query = {"today": today.isoformat(), "language": "ru", "tools": h.tools, "audience": h.audience, "start_level": h.start_level,
             "project": draft.summary.title if draft.summary else h.title,
             "topics": [{"number": t.number, "title": t.title, "text": t.text, "keywords": t.keywords} for t in topics]}
    text, urls, search_metrics = await writer.search.text(draft.prompt_snapshots["sources"], json.dumps(query, ensure_ascii=False))
    found = Found(models=[search_metrics.model], metrics=[search_metrics])
    pages = await writer.open_pages(urls)
    found.live = [p.url for p in pages]
    if not pages:
        found.note = (f"Поиск вернул {len(urls)} ссылок, ни одна не открылась." if urls else
                      "Поиск не вернул ни одной ссылки.") + " Добавьте источники в чат или повторите поиск позже."
        return found
    payload = {"topics": query["topics"], "search_answer": text[:6000], "candidates": candidates(pages)}
    selection, metrics = await writer.client.generate(LinkSelection, writer.system(draft, "links"), json.dumps(payload, ensure_ascii=False))
    found.models.append(metrics.model)
    found.metrics.append(metrics)
    numbers = {t.number for t in topics}
    for choice in selection.picks:
        if choice.topic in numbers and 1 <= choice.candidate <= len(pages):
            page = pages[choice.candidate - 1]
            chosen = found.links.setdefault(choice.topic, [])
            if page.url not in {link.url for link in chosen} and len(chosen) < 4:
                chosen.append(Link(title=choice.title, url=page.url, why=choice.why))
    empty = [t.number for t in topics if not found.links.get(t.number)]
    if empty:
        found.note = selection.note or f"Для тем {', '.join(map(str, empty))} среди открывшихся страниц подходящих не нашлось."
    return found


LETTER = re.compile(r"[^\W\d_]")
LATIN_WORD = re.compile(r"[A-Za-z']+")
# The commonest words of English prose: a text in Latin letters without them is another language, a transliteration or code.
COMMON = frozenset("the of and to a in is that for it with as on be this are or by an not was from at which you can have has will but".split())
# A page is shown to the model that chooses the excerpt in this much of its text; shorter than this is no excerpt.
SHOWN_CHARS, SHORTEST = 6000, 40
# A sentence ends with one of these; an excerpt is whole sentences, so it ends with one too.
CLOSING = ".!?…\"”’»)"


def english(text: str) -> bool:
    """The text is English prose: written in Latin letters and with the common words of English among its words. By
    the letters alone German, French, a transliteration and a line of code passed for it (independent review 06.10.2026)."""
    letters = LETTER.findall(text)
    if len(letters) < 20 or sum(1 for char in letters if char.isascii()) / len(letters) <= 0.9:
        return False
    words = [word.lower() for word in LATIN_WORD.findall(text)]
    common = sum(1 for word in words if word in COMMON)
    return common >= 2 and common / len(words) >= 0.12


def squeezed(text: str) -> str:
    return " ".join(text.split())


def stands_whole(quote: str, page: str) -> bool:
    """The quote stands on the page word for word and is not cut out of the middle of a word or of a sentence: «safe
    to force-push to a shared branch» is on a page that says «It is unsafe to force-push…», and says the opposite.
    Where it stands, a word begins before it and ends after it; it begins as a sentence does and ends as one."""
    if not (quote[:1].isupper() or quote[:1].isdigit() or quote[:1] in "\"“‘«(") or quote[-1:] not in CLOSING:
        return False
    def joins(char: str) -> bool:
        # A letter, a figure or a mark that binds a word together: with it next to the quote, the quote is cut out of a word.
        return bool(char) and (char.isalnum() or char in "-'’")

    start = page.find(quote)
    while start >= 0:
        before, after = page[start - 1:start] if start else "", page[start + len(quote):start + len(quote) + 1]
        if not joins(before) and not joins(after):
            return True
        start = page.find(quote, start + 1)
    return False


@dataclass
class FoundExcerpt:
    excerpt: Excerpt | None = None
    # Why there is none, in words for the methodologist.
    note: str = ""
    live: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)


async def find_excerpt(writer: Writer, draft: ContentDraft, topics: list[TheoryTopic], today: date) -> FoundExcerpt:
    """A short excerpt of a primary source in English for one section of the theory (decided 06.10.2026: only a real
    source). The search names pages, code opens them and keeps those written in English, the writer model chooses a
    page and marks an excerpt in its text — and code takes it only if it stands on that page word for word. A model
    asked to write such a text itself composes «a letter of 1987»: a made-up quotation shown as the source."""
    h = draft.handoff
    asked = [{"number": t.number, "title": t.title, "keywords": t.keywords} for t in topics]
    query = {"looking_for": "primary_source", "today": today.isoformat(), "tools": h.tools, "audience": h.audience,
             "start_level": h.start_level, "project": draft.summary.title if draft.summary else h.title, "topics": asked}
    text, urls, search_metrics = await writer.search.text(draft.prompt_snapshots["primary"], json.dumps(query, ensure_ascii=False))
    found = FoundExcerpt(models=[search_metrics.model])
    # The pages the answer names are opened first: the gateway lists every page it looked through before them, and
    # only the first few are read — the source the search chose must be among them (independent review 06.10.2026).
    named = links_in_text(text)
    urls = list(dict.fromkeys([*named, *urls]))
    pages = [page for page in await writer.read_pages(urls) if english(page.text)]
    found.live = [page.url for page in pages]
    if not pages:
        found.note = (f"Выдержку из первоисточника не взял: поиск назвал {len(urls)} страниц, текста на английском среди открывшихся нет."
                      if urls else "Выдержку из первоисточника не взял: поиск не назвал ни одной страницы.")
        return found
    payload = {"topics": asked, "search_answer": text[:4000], "limit": EXCERPT_LIMIT,
               "candidates": [{"number": i, "url": p.url, "page_title": p.title, "page_text": p.text[:SHOWN_CHARS]} for i, p in enumerate(pages, 1)]}
    pick, metrics = await writer.excerpt(draft, payload)
    found.models.append(metrics.model)
    quote = squeezed(pick.quote)
    if not quote or not 1 <= pick.candidate <= len(pages):
        found.note = "Выдержку из первоисточника не взял: " + (pick.note or "среди открывшихся страниц подходящей не нашлось") + "."
        return found
    page = pages[pick.candidate - 1]
    # A quotation is shown as the source says it or not at all; why it was not taken is said as it is.
    why = (f"она короче {SHORTEST} знаков" if len(quote) < SHORTEST else f"она длиннее {EXCERPT_LIMIT} знаков" if len(quote) > EXCERPT_LIMIT
           else "это не английский текст" if not english(quote)
           else f"такого текста нет на странице {page.url} дословно, целыми предложениями" if not stands_whole(quote, squeezed(page.text)) else "")
    if why:
        found.note = f"Выдержку из первоисточника не взял: модель предложила выдержку, но {why}. Найти другую — кнопкой «{FIND_EXCERPT}»."
        return found
    numbers = [t.number for t in topics]
    found.excerpt = Excerpt(topic=pick.topic if pick.topic in numbers else numbers[0], title=pick.title.strip() or page.title or page.url,
                            url=page.url, quote=quote)
    return found


@dataclass
class Facts:
    report: FactReport | None
    pages: list[Page]
    note: str = ""
    models: list[str] = field(default_factory=list)


async def check_facts(writer: Writer, draft: ContentDraft, payload: dict[str, object]) -> Facts:
    """The search model checks what depends on time in free text; the writer model turns it into findings whose
    sources are only pages code has opened."""
    text, urls, search_metrics = await writer.search.text(draft.prompt_snapshots["facts"], json.dumps(payload, ensure_ascii=False))
    pages = await writer.open_pages(urls)
    report, metrics = await writer.client.generate(
        FactReport, writer.system(draft, "facts_select"),
        json.dumps({"search_answer": text[:8000], "candidates": candidates(pages), "parts": payload}, ensure_ascii=False))
    return Facts(report=report, pages=pages, models=[search_metrics.model, metrics.model])
