"""Реестр: по расширению файла находится обработчик, и ровно один."""

from __future__ import annotations

from content_factory.translation.registry import all_handlers, handler_for


def test_markdown_and_text_go_to_the_plain_handler() -> None:
    for extension in (".md", ".markdown", ".txt"):
        handler = handler_for(extension)
        assert handler is not None, extension
        assert extension in handler.extensions


def test_an_unknown_extension_has_no_handler() -> None:
    assert handler_for(".bin") is None
    assert handler_for("") is None


def test_the_extension_is_matched_regardless_of_case() -> None:
    assert handler_for(".MD") is handler_for(".md")


def test_no_two_handlers_claim_the_same_extension() -> None:
    seen: dict[str, object] = {}
    for handler in all_handlers():
        for extension in handler.extensions:
            assert extension not in seen, f"{extension} заявлен дважды"
            seen[extension] = handler


def test_docx_has_its_own_handler() -> None:
    handler = handler_for(".docx")

    assert handler is not None
    assert ".docx" in handler.extensions


def test_yaml_has_its_own_handler() -> None:
    for extension in (".yml", ".yaml"):
        handler = handler_for(extension)
        assert handler is not None, extension
        assert extension in handler.extensions


def test_xlsx_has_its_own_handler() -> None:
    handler = handler_for(".xlsx")

    assert handler is not None
    assert ".xlsx" in handler.extensions


def test_pptx_has_its_own_handler() -> None:
    handler = handler_for(".pptx")

    assert handler is not None
    assert ".pptx" in handler.extensions


def test_pdf_has_its_own_handler() -> None:
    handler = handler_for(".pdf")

    assert handler is not None
    assert ".pdf" in handler.extensions


def test_every_format_from_the_design_is_covered() -> None:
    from content_factory.translation.registry import supported_extensions

    expected = {
        ".md", ".markdown", ".txt", ".html", ".htm",
        ".docx", ".yml", ".yaml", ".xlsx", ".pptx", ".pdf",
    }

    assert expected <= supported_extensions()
