"""PDF: перевод вписывается в те же рамки, где стоял исходный текст.

Базовые шрифты PDF кириллицы не содержат — ``insert_textbox`` с ними пишет «??????». Поэтому
текст кладётся через ``TextWriter`` с объектом шрифта, который встраивается в файл целиком.

Русский длиннее английского примерно на четверть, поэтому кегль ужимается по рамке, но не ниже
семидесяти процентов исходного: мельче текст становится нечитаемым. Не поместилось и так — блок
остаётся на исходном языке, а не вылезает за рамку и не обрезается.
"""

from __future__ import annotations

import pymupdf
import pytest

from content_factory.translation.errors import DocumentFormatError
from content_factory.translation.handlers.pdf import MAX_PAGES, PdfHandler

_PAGES_OVER_LIMIT = MAX_PAGES + 1


def _pdf_bytes(*paragraphs: str, with_image: bool = False) -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    top = 60.0
    for text in paragraphs:
        page.insert_textbox(pymupdf.Rect(50, top, 540, top + 90), text, fontsize=11)
        top += 100
    if with_image:
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20))
        pixmap.set_rect(pixmap.irect, (255, 0, 0))
        page.insert_image(pymupdf.Rect(400, 600, 460, 660), pixmap=pixmap)
    return document.tobytes()


def _text_of(content: bytes) -> str:
    """Текст страниц без учёта переносов: PDF расставляет их сам, по ширине рамки."""
    raw = " ".join(page.get_text() for page in pymupdf.open("pdf", content))
    return " ".join(raw.split())


@pytest.fixture
def handler() -> PdfHandler:
    return PdfHandler()


def test_paragraphs_become_units(handler: PdfHandler) -> None:
    units = handler.extract(_pdf_bytes("Intro to Machine Learning", "Decision boundaries"))

    texts = " ".join(u.text for u in units)
    assert "Intro to Machine Learning" in texts
    assert "Decision boundaries" in texts


def test_without_translations_the_text_is_unchanged(handler: PdfHandler) -> None:
    source = _pdf_bytes("Intro to Machine Learning", "Decision boundaries")

    assert _text_of(handler.apply(source, {})) == _text_of(source)


def test_a_translated_block_replaces_only_itself(handler: PdfHandler) -> None:
    source = _pdf_bytes("Intro to Machine Learning", "Decision boundaries")
    unit = next(u for u in handler.extract(source) if "Intro" in u.text)

    result = _text_of(handler.apply(source, {unit.unit_id: "Введение в машинное обучение"}))

    assert "Введение в машинное обучение" in result
    assert "Decision boundaries" in result, "соседний блок не тронут"
    assert "Intro to Machine Learning" not in result, "исходный текст затёрт, а не оставлен рядом"


def test_cyrillic_survives_the_round_trip(handler: PdfHandler) -> None:
    source = _pdf_bytes("Chapter I")
    unit = handler.extract(source)[0]

    result = _text_of(handler.apply(source, {unit.unit_id: "Глава I. Ёжик, йод и щука"}))

    assert "Глава I. Ёжик, йод и щука" in result, "буквы вроде Ё и Щ не должны превращаться в ??"


def test_an_image_is_not_wiped_by_the_replacement(handler: PdfHandler) -> None:
    source = _pdf_bytes("Intro to Machine Learning", with_image=True)
    unit = handler.extract(source)[0]

    result = handler.apply(source, {unit.unit_id: "Введение"})

    assert len(pymupdf.open("pdf", result)[0].get_images()) == 1, "картинка осталась на месте"


def test_a_translation_that_cannot_fit_leaves_the_block_alone(handler: PdfHandler) -> None:
    source = _pdf_bytes("Short")
    unit = handler.extract(source)[0]
    # Заведомо больше страницы: блок растёт до свободного места, поэтому обычная длина влезает.
    huge = "Совершенно непомерный перевод, который не поместится ни при каком кегле. " * 200

    result = _text_of(handler.apply(source, {unit.unit_id: huge}))

    assert "Short" in result, "лучше исходный язык, чем обрезанный или вылезший текст"


def test_a_broken_file_is_a_format_error(handler: PdfHandler) -> None:
    with pytest.raises(DocumentFormatError):
        handler.extract(b"not a pdf at all")


# ------------------------------------------------------------------ пределы разбора

def test_a_document_with_too_many_pages_is_refused(handler: PdfHandler) -> None:
    """Файл приходит из загруженного архива, поэтому число страниц ограничено."""
    document = pymupdf.open()
    for _ in range(_PAGES_OVER_LIMIT):
        document.new_page()
    source = document.tobytes()

    with pytest.raises(DocumentFormatError, match="страниц"):
        handler.extract(source)


def test_a_dense_page_does_not_take_quadratic_time(handler: PdfHandler) -> None:
    """Рамка блока расширяется до соседей, и наивный поиск соседа делал это за O(n²).

    Полторы тысячи блоков — обычная плотная таблица, а не нападение: разбор обязан укладываться
    в секунды, иначе страница сгенерированного отчёта вешает перевод.
    """
    import time

    document = pymupdf.open()
    page = document.new_page()  # A4: 595 x 842
    for column in range(14):
        for row in range(100):
            # По точке, а не в рамку: в рамку высотой 7pt текст 5pt не влезает и не рисуется.
            page.insert_text(pymupdf.Point(20 + column * 40, 28 + row * 8), f"c{column}-{row}", fontsize=5)
    source = document.tobytes()

    started = time.monotonic()
    units = handler.extract(source)
    elapsed = time.monotonic() - started

    assert len(units) > 300, "блоки действительно нашлись"
    assert elapsed < 10, f"разбор занял {elapsed:.1f} с — похоже на квадратичный обход"
