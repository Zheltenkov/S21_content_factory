from content_gen.models.readme_blocks import ReadmeBlockKind
from content_gen.models.readme_document import ReadmeDocument, ReadmeSection


def test_readme_document_renders_stable_markdown_spacing() -> None:
    document = ReadmeDocument(
        title="Проект",
        annotation="Аннотация.",
        sections=[
            ReadmeSection(
                title="Содержание",
                level=2,
                body="- [Глава 1](#глава-1)",
            ),
            ReadmeSection(
                title="Глава 1. Введение",
                level=2,
                body="Текст.",
            ),
        ],
    )

    markdown = document.to_markdown()

    assert markdown.startswith("# Проект\n\nАннотация.")
    assert "\n\n## Содержание\n\n- [Глава 1](#глава-1)\n\n## Глава 1. Введение\n\nТекст.\n" in markdown


def test_readme_document_parses_nested_sections_from_markdown() -> None:
    document = ReadmeDocument.from_markdown(
        "# Проект\n\nАннотация.\n\n## Глава 2. Теория\n\nТело.\n\n### 2.1. Раздел\n\nДетали."
    )

    assert document.title == "Проект"
    assert document.annotation == "Аннотация."
    assert document.outline() == [
        {"level": 1, "title": "Проект"},
        {"level": 2, "title": "Глава 2. Теория"},
        {"level": 3, "title": "2.1. Раздел"},
    ]
    assert document.section_by_title_fragment("2.1").body == "Детали."


def test_readme_document_ignores_headings_inside_fenced_code_blocks() -> None:
    document = ReadmeDocument.from_markdown(
        "# Проект\n\n"
        "## Глава 2. Теория\n\n"
        "```md\n"
        "# Не заголовок документа\n"
        "## Не секция\n"
        "```\n\n"
        "### 2.1. Настоящий раздел\n\n"
        "Детали."
    )

    assert document.title == "Проект"
    assert document.section_by_title_fragment("Не секция") is None
    assert document.section_by_title_fragment("2.1").body == "Детали."
    assert "# Не заголовок документа" in document.section_by_title_fragment("Глава 2").body


def test_readme_document_renders_selected_section_and_subsections() -> None:
    document = ReadmeDocument.from_markdown(
        "# Проект\n\n"
        "Аннотация.\n\n"
        "## Глава 2. Теория\n\n"
        "Тело.\n\n"
        "### 2.1. Раздел\n\n"
        "Детали.\n\n"
        "## Глава 3. Практика\n\n"
        "Задание."
    )

    chapter = document.section_markdown_by_title_fragment("Глава 2")
    subsections = document.markdown_subsections(min_level=2)

    assert "## Глава 2. Теория" in chapter
    assert "### 2.1. Раздел" in chapter
    assert [section["title"] for section in subsections] == [
        "Глава 2. Теория",
        "2.1. Раздел",
        "Глава 3. Практика",
    ]


def test_readme_document_from_value_hydrates_dict_or_markdown_fallback() -> None:
    document = ReadmeDocument.from_markdown("# Проект\n\nBody")

    from_dict = ReadmeDocument.from_value(document.model_dump())
    from_fallback = ReadmeDocument.from_value(None, fallback_markdown="# Fallback\n\nBody")

    assert from_dict.title == "Проект"
    assert from_fallback.title == "Fallback"


def test_readme_document_replaces_chapter_body_as_typed_section_tree() -> None:
    document = ReadmeDocument.from_markdown(
        "# Проект\n\n"
        "Аннотация.\n\n"
        "## Глава 1. Введение\n\n"
        "Интро.\n\n"
        "## Глава 2. Теоретический блок\n\n"
        "Старый текст.\n\n"
        "### 2.0. Старый раздел\n\n"
        "Старые детали.\n\n"
        "## Глава 3. Практический блок\n\n"
        "Практика."
    )

    updated, changed = document.with_replaced_chapter_body(
        2,
        "Новый текст.\n\n### 2.1. Новый раздел\n\nНовые детали.",
        language="ru",
    )
    markdown = updated.to_markdown()

    assert changed is True
    assert "## Глава 2. Теоретический блок" in markdown
    assert "Новый текст." in markdown
    assert "### 2.1. Новый раздел" in markdown
    assert "Старый текст." not in markdown
    assert "## Глава 3. Практический блок" in markdown
    assert updated.section_by_title_fragment("2.1").body == "Новые детали."


def test_readme_document_replaces_chapter_children_without_markdown_body_parse() -> None:
    document = ReadmeDocument.from_markdown(
        "# Проект\n\n"
        "## Глава 2. Теоретический блок\n\n"
        "Старый текст.\n\n"
        "### 2.0. Старый раздел\n\n"
        "Старые детали."
    )
    child = ReadmeSection(title="2.1. Новый раздел", level=3, body="Новые детали.")

    updated, changed = document.with_replaced_chapter_children(2, [child], chapter_body="Новая вводная.")

    assert changed is True
    assert updated.section_by_title_fragment("Глава 2").body == "Новая вводная."
    assert updated.section_by_title_fragment("2.1").body == "Новые детали."
    assert updated.section_by_title_fragment("2.0") is None


def test_readme_document_upserts_section_when_fragment_is_absent() -> None:
    document = ReadmeDocument.from_markdown("# Проект\n\nАннотация.")

    updated = document.with_upserted_section_by_title_fragment(
        "Глава 2",
        "## Глава 2. Теоретический блок\n\nТеория.",
    )

    assert updated.section_by_title_fragment("Глава 2").body == "Теория."
    assert document.section_by_title_fragment("Глава 2") is None


def test_readme_document_removes_section_by_title_fragment() -> None:
    document = ReadmeDocument.from_markdown(
        "# Проект\n\n"
        "Аннотация.\n\n"
        "## Глава 3. Практика\n\n"
        "Практика.\n\n"
        "## Бонус\n\n"
        "Бонусный текст."
    )

    updated, removed = document.without_section_by_title_fragment("Бонус")

    assert removed is True
    assert updated.section_by_title_fragment("Бонус") is None
    assert "## Глава 3. Практика" in updated.to_markdown()
    assert document.section_by_title_fragment("Бонус") is not None


def test_readme_document_extracts_typed_blocks_and_section_metadata() -> None:
    document = ReadmeDocument.from_markdown(
        "# Проект\n\n"
        "## Глава 2. Теоретический блок\n\n"
        "### 2.1. Процесс\n\n"
        "```mermaid\n"
        "flowchart TD\n"
        "    A --> B\n"
        "```\n"
        "_Процесс проверки_\n\n"
        "| Поле | Значение |\n"
        "| --- | --- |\n"
        "| A | B |\n\n"
        "$$x = y + z$$\n"
    )

    section = document.section_by_title_fragment("2.1")
    blocks = document.content_blocks()

    assert section.metadata["section_kind"] == "theory_part"
    assert section.metadata["section_number"] == "2.1"
    assert document.block_counts() == {"mermaid": 1, "table": 1, "formula": 1}
    assert [block.kind.value for block in blocks] == ["mermaid", "table", "formula"]
    assert blocks[0].caption == "Процесс проверки"
    assert blocks[0].section_path == ["Проект", "Глава 2. Теоретический блок", "2.1. Процесс"]


def test_readme_document_materializes_full_typed_block_model() -> None:
    document = ReadmeDocument.from_markdown(
        "# Проект\n\n"
        "## Глава 3. Практический блок\n\n"
        "### Задание 1. Артефакт\n\n"
        "Собери результат.\n\n"
        "```python\n"
        "print('ok')\n"
        "```\n\n"
        "**Что должно получиться**\n\n"
        "- [ ] Файл лежит в `repo/part-03/task-01/result.md`.\n"
        "- [ ] Таблица содержит проверяемые строки.\n\n"
        "| Поле | Значение |\n"
        "| --- | --- |\n"
        "| status | ok |\n"
    )

    section = document.section_by_title_fragment("Задание 1")
    assert section is not None
    blocks = section.content_blocks(recursive=False, include_paragraphs=True)
    kinds = [block.kind for block in blocks]

    assert kinds == [
        ReadmeBlockKind.PARAGRAPH,
        ReadmeBlockKind.CODE,
        ReadmeBlockKind.PARAGRAPH,
        ReadmeBlockKind.CRITERIA,
        ReadmeBlockKind.TABLE,
    ]
    assert blocks[1].language == "python"
    assert blocks[3].items == [
        "Файл лежит в `repo/part-03/task-01/result.md`.",
        "Таблица содержит проверяемые строки.",
    ]
    assert blocks[4].headers == ["Поле", "Значение"]
    assert blocks[4].rows == [["status", "ok"]]
    assert document.block_counts(include_paragraphs=True)["criteria"] == 1
