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


def test_python_sources_have_a_comment_handler() -> None:
    handler = handler_for(".py")

    assert handler is not None
    assert ".py" in handler.extensions


def test_shell_scripts_have_a_comment_handler() -> None:
    for extension in (".sh", ".bash"):
        handler = handler_for(extension)
        assert handler is not None, extension
        assert extension in handler.extensions


def test_c_like_languages_and_sql_have_comment_handlers() -> None:
    for extension in (".js", ".ts", ".c", ".cpp", ".h", ".go", ".java", ".sql"):
        handler = handler_for(extension)
        assert handler is not None, extension
        assert extension in handler.extensions


def test_code_is_parsed_by_real_grammars() -> None:
    """Ручные сканеры заменены грамматиками: шесть разборов безопасности подряд были о них."""
    from content_factory.translation.handlers.code_treesitter import TreeSitterCommentHandler

    for extension in (".sh", ".js", ".ts", ".c", ".cpp", ".go", ".java", ".sql"):
        handler = handler_for(extension)
        assert isinstance(handler, TreeSitterCommentHandler), extension


def test_python_keeps_the_standard_library_parser() -> None:
    """Для Python настоящий разбор уже есть в tokenize и ast — менять его было не на что."""
    from content_factory.translation.handlers.code import PythonCommentHandler

    assert isinstance(handler_for(".py"), PythonCommentHandler)


def test_data_examples_have_a_handler() -> None:
    for extension in (".csv", ".tsv"):
        handler = handler_for(extension)
        assert handler is not None, extension
        assert extension in handler.extensions


def test_the_page_offers_documents_but_not_source_files() -> None:
    """На странице одиночной загрузки языкам программирования не место.

    Реестр знает их ради перевода архива, где скрипты проекта идут вместе с документами, но
    методист не приносит один файл кода.
    """
    from content_factory.translation.registry import page_extensions, supported_extensions

    visible = page_extensions()

    assert {".md", ".docx", ".pdf", ".yml", ".xlsx", ".pptx", ".csv"} <= visible
    for hidden in (".py", ".sh", ".js", ".c", ".go", ".java", ".sql"):
        assert hidden not in visible, hidden
        assert hidden in supported_extensions(), "в архиве такой файл всё равно переводится"
