from content_gen.methodology import build_section_target_registry


def test_target_registry_builds_stable_markdown_and_material_targets() -> None:
    context = {
        "title": "Project",
        "markdown": """# Project

## Глава 2. Теоретический блок

### 2.1. Риски

Текст.

## Глава 3. Практический блок

### Задание 1. Собрать реестр

Текст.
""",
        "dataset_files": [{"path": "materials/task_01_source_notes.md", "data": b"raw"}],
    }

    registry = build_section_target_registry(context)

    ids = {target.id for target in registry.targets}
    assert "title" in ids
    assert "annotation" in ids
    assert "chapter_2" in ids
    assert "chapter_2.part_1" in ids
    assert "chapter_3.task_1" in ids
    assert "material.materials_task_01_source_notes_md" in ids

    part = registry.find("chapter_2.part_1")
    assert part is not None
    assert part.stage == "theory"
    assert part.kind == "markdown_section"
    assert part.start is not None
    assert part.end is not None

    annotation = registry.find("annotation")
    assert annotation is not None
    assert annotation.stage == "annotation"
    assert annotation.kind == "markdown_section"
    assert annotation.label == "Аннотация"

    material = registry.find("material.materials_task_01_source_notes_md")
    assert material is not None
    assert material.scope == "materials_only"
    assert material.metadata["path"] == "materials/task_01_source_notes.md"


def test_target_registry_exposes_title_and_annotation_before_markdown() -> None:
    registry = build_section_target_registry(
        {
            "title": "Frontend и backend",
            "annotation": {"text": "Короткая аннотация.", "chars": 20},
        }
    )

    title = registry.find("title")
    assert title is not None
    assert title.kind == "field"
    assert title.stage == "title"

    annotation = registry.find("annotation")
    assert annotation is not None
    assert annotation.kind == "field"
    assert annotation.stage == "annotation"


def test_target_registry_supports_canonical_theory_and_task_headings() -> None:
    registry = build_section_target_registry(
        {
            "markdown": """# Project

Annotation.

## Глава 2. Теоретический блок

### 2.1. Критерии выбора

Текст.

## Глава 3. Практический блок

### Задание 1. Собрать карту

Текст.
""",
        }
    )

    assert registry.find("chapter_2.part_1").stage == "theory"
    assert registry.find("chapter_3.task_1").stage == "practice"
