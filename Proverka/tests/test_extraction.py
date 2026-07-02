from pathlib import Path

from content_audit.extraction import extract_entities
from content_audit.ingestion import discover_content_units, load_unit_files


def test_extracts_links_versions_dates_and_images(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "![pic](misc/image.png)\n"
        "Use Java 21 and POSIX.1-2017.\n"
        "Docs: https://example.com/docs\n",
        encoding="utf-8",
    )

    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    entities = extract_entities(unit)
    values = {entity.value for entity in entities}

    assert "misc/image.png" in values
    assert "Java 21" in values
    assert "POSIX.1-2017" in values
    assert "https://example.com/docs" in values


def test_does_not_extract_scheme_example_as_link(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "**https://** — scheme, transport indication, protocol.\n"
        "Docs: https://example.com/docs\n",
        encoding="utf-8",
    )

    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    values = {entity.value for entity in extract_entities(unit)}

    assert "https://" not in values
    assert "https://example.com/docs" in values


def test_strips_markdown_emphasis_from_link_edges(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "**Resources: https://21-school.ru/blog**\n",
        encoding="utf-8",
    )

    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    values = {entity.value for entity in extract_entities(unit)}

    assert "https://21-school.ru/blog" in values
    assert "https://21-school.ru/blog**" not in values


def test_version_extraction_requires_known_technology_prefix(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "Part 2 covers figures.\n"
        "In 3 steps the script generates 5 files.\n"
        "Use Ubuntu Server 24.04 and Python 3.12.\n",
        encoding="utf-8",
    )

    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    values = {entity.value for entity in extract_entities(unit)}

    assert "Ubuntu Server 24.04" in values
    assert "Python 3.12" in values
    assert "Part 2" not in values
    assert "In 3" not in values
    assert "generates 5" not in values
    assert "Server 24.04" not in values
