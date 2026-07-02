"""Document-level renderer/repair contracts used by generation workflow."""

from content_gen.models.readme_document import ReadmeDocument
from content_gen.renderers.toc import TOCRenderer
from content_gen.repair.style_guard import StyleGuardRepair


def test_toc_build_and_inject_document_contract():
    renderer = TOCRenderer()
    document = ReadmeDocument.from_markdown(
        "# Проект\n\nАннотация.\n\n## Глава 1. Введение\n\n### Инструкция\n\nТекст."
    )

    toc = renderer.build_document(document, language="ru")
    updated = renderer.inject_document(document, toc.toc_md, language="ru")

    assert updated.sections[0].title == "Содержание"
    assert "- [Глава 1. Введение](#глава-1-введение)" in updated.sections[0].body
    assert "  - [Инструкция](#инструкция)" in updated.sections[0].body


def test_style_guard_document_contract():
    repair = StyleGuardRepair()
    document = ReadmeDocument.from_markdown("# Проект\n\nНажми кнопку для продолжения.")

    issues = repair.lint_document(document, "ru")
    fixed_document = repair.rewrite_document(document, "ru")

    assert issues
    assert isinstance(fixed_document, ReadmeDocument)
    assert "нажми" not in fixed_document.to_markdown().lower()
