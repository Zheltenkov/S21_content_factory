"""PDF: перевод вписывается в те же рамки, где стоял исходный текст.

Единица — текстовый блок страницы, то есть примерно абзац: по нему у PyMuPDF есть и рамка, и
кегль. Замена идёт в два приёма: исходный текст затирается, на его место кладётся перевод.

Два подводных камня, оба проверены опытом и закрыты тестами.

Первый: базовые шрифты PDF не содержат кириллицы, и ``insert_textbox`` с ними пишет «??????».
Поэтому текст кладётся через ``TextWriter`` с объектом ``Font`` — он встраивает шрифт в файл
целиком, и «Ёжик» остаётся «Ёжиком».

Второй: затирание по умолчанию сносит и картинки, задетые рамкой. Флаги оставляют изображения и
векторную графику нетронутыми — иначе перевод подписи стирал бы схему под ней.

Русский длиннее английского примерно на четверть, а рамка блока обжимает исходный текст вплотную:
заголовок «Chapter I» занимает полоску, в которую «Глава I» уже не встаёт. Поэтому переводу
отдаётся место, которое на странице и так свободно — вправо до края текстовой колонки и вниз до
следующего блока. Кегль после этого подбирается по рамке, но не ниже семидесяти процентов
исходного: мельче текст нечитаем. Не поместилось и так — блок остаётся на исходном языке.
Обрезанный или вылезший за поля перевод хуже непереведённого абзаца.

Разметку в две колонки это не разбирает: рост вправо ограничен самым правым блоком страницы, то
есть в двухколоночном PDF перевод левой колонки может дойти до правой. Для учебных материалов,
ради которых всё затевалось, разметка одноколоночная.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable

import pymupdf

from content_factory.translation.errors import DocumentFormatError
from content_factory.translation.units import TextUnit

# Ниже этой доли исходного кегля не опускаемся даже ради того, чтобы текст поместился.
_MIN_SCALE = 0.7
_SCALE_STEP = 0.05
# Запас внутри рамки: вплотную к краю текст выглядит обрезанным.
_PADDING = 1.0
# Зазор до следующего блока, чтобы выросший перевод не налезал на соседний абзац.
_BLOCK_GAP = 2.0
# Нижнее поле страницы, если блок последний.
_PAGE_MARGIN = 36.0
# Пределы разбора: файл приходит из загруженного архива. Числа с запасом для учебных материалов,
# но не дают собранному нарочно PDF съесть память и время.
MAX_PAGES = 500
MAX_UNITS = 20_000
MAX_CHARS = 2_000_000


class PdfHandler:
    """Переводит текстовые блоки PDF, сохраняя расположение, картинки и графику."""

    extensions = frozenset({".pdf"})

    def extract(self, content: bytes) -> list[TextUnit]:
        with _open(content) as document:
            return [
                TextUnit(unit_id=unit_id, text=text, hint="абзац PDF")
                for unit_id, _page, _rect, _size, text in _blocks(document)
            ]

    def apply(self, content: bytes, translations: dict[str, str]) -> bytes:
        with _open(content) as document:
            pending: dict[int, list[tuple[pymupdf.Rect, float, str]]] = {}
            for unit_id, page_number, rect, size, _text in _blocks(document):
                translated = translations.get(unit_id)
                if translated:
                    pending.setdefault(page_number, []).append((rect, size, translated))

            for page_number, items in pending.items():
                page = document[page_number]
                # Подбор кегля и запись — один проход: писатель, сложивший текст при примерке,
                # потом просто выкладывается на страницу. Иначе вся раскладка считается дважды.
                prepared = [
                    (rect, writer)
                    for rect, size, text in items
                    if (writer := _prepare(page, rect, size, text)) is not None
                ]
                if not prepared:
                    continue
                for rect, _writer in prepared:
                    page.add_redact_annot(rect)
                # Картинки и векторная графика под рамкой должны пережить затирание текста.
                page.apply_redactions(
                    # Константы есть в библиотеке, но не в её заглушках типов.
                    images=pymupdf.PDF_REDACT_IMAGE_NONE,  # type: ignore[attr-defined]
                    graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,  # type: ignore[attr-defined]
                )
                for _rect, writer in prepared:
                    writer.write_text(page)
            return bytes(document.tobytes())


def _open(content: bytes) -> pymupdf.Document:
    try:
        return pymupdf.open("pdf", content)
    except Exception as exc:  # noqa: BLE001 - библиотека сообщает об этом по-разному
        raise DocumentFormatError("PDF-файл повреждён или имеет неверный формат") from exc


def _blocks(document: pymupdf.Document) -> list[tuple[str, int, pymupdf.Rect, float, str]]:
    """Текстовые блоки: ``(ключ, страница, рамка, кегль, текст)`` в устойчивом порядке.

    Рамка возвращается уже расширенной до свободного места: иначе перевод, который длиннее
    оригинала, не поместится почти никогда.
    """
    if document.page_count > MAX_PAGES:
        raise DocumentFormatError(
            f"В PDF {document.page_count} страниц — больше предела в {MAX_PAGES}"
        )

    found: list[tuple[str, int, pymupdf.Rect, float, str]] = []
    counter = 1
    characters = 0
    for page_number in range(document.page_count):
        page = document[page_number]
        raw: list[tuple[pymupdf.Rect, float, str]] = []
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:  # 0 — текст, 1 — картинка
                continue
            spans = [span for line in block.get("lines", []) for span in line.get("spans", [])]
            text = " ".join(
                span.get("text", "").strip() for span in spans if span.get("text", "").strip()
            )
            if not text.strip():
                continue
            size = max((float(span.get("size", 11)) for span in spans), default=11.0)
            raw.append((pymupdf.Rect(block["bbox"]), size, text))

        room = _room_finder(raw, page)
        for rect, size, text in raw:
            if counter > MAX_UNITS:
                raise DocumentFormatError(f"В PDF больше {MAX_UNITS} переводимых блоков")
            characters += len(text)
            if characters > MAX_CHARS:
                raise DocumentFormatError(f"В PDF больше {MAX_CHARS} знаков текста")
            found.append((f"{counter:04d}", page_number, room(rect), size, text))
            counter += 1
    return found


def _room_finder(
    blocks: list[tuple[pymupdf.Rect, float, str]], page: pymupdf.Page
) -> Callable[[pymupdf.Rect], pymupdf.Rect]:
    """Готовит поиск свободного места: вправо до края колонки, вниз до следующего блока.

    Границы считаются один раз на страницу. Наивный вариант искал соседа перебором всех блоков
    для каждого блока, то есть за O(n²): на плотной таблице в полторы тысячи ячеек это уже
    заметная пауза, а на нарочно собранном файле — зависание.
    """
    right = max(
        max((other.x1 for other, _size, _text in blocks), default=page.rect.x1),
        page.rect.x1 - _PAGE_MARGIN,
    )
    tops = sorted(other.y0 for other, _size, _text in blocks)
    floor = page.rect.y1 - _PAGE_MARGIN

    def room(rect: pymupdf.Rect) -> pymupdf.Rect:
        index = bisect_left(tops, rect.y1 - 0.5)
        bottom = tops[index] - _BLOCK_GAP if index < len(tops) else floor
        return pymupdf.Rect(rect.x0, rect.y0, max(rect.x1, right), max(rect.y1, bottom))

    return room


def _prepare(
    page: pymupdf.Page, rect: pymupdf.Rect, size: float, text: str
) -> pymupdf.TextWriter | None:
    """Подбирает кегль от исходного вниз до предела. ``None`` — не поместилось даже так."""
    font = pymupdf.Font("helv")
    box = pymupdf.Rect(
        rect.x0 - _PADDING, rect.y0 - _PADDING, rect.x1 + _PADDING, rect.y1 + _PADDING
    )
    scale = 1.0
    while scale >= _MIN_SCALE:
        writer = pymupdf.TextWriter(page.rect)
        if not writer.fill_textbox(box, text, font=font, fontsize=size * scale):
            return writer
        scale -= _SCALE_STEP
    return None
