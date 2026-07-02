from pathlib import Path

from content_audit.image_rights import assess_image_rights, image_rights_signals
from content_audit.extraction import extract_entities
from content_audit.ingestion import discover_content_units, load_unit_files


def _png_header(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
    )


def _unit(project: Path):
    return load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)


def test_assess_image_rights_reports_missing_local_metadata(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("![architecture](diagram.png)\n", encoding="utf-8")
    (project / "diagram.png").write_bytes(_png_header(480, 320))
    unit = _unit(project)
    entity = extract_entities(unit)[0]

    assessment = assess_image_rights(unit, entity)
    signals = image_rights_signals(unit, [entity])

    assert assessment.status == "missing_local_metadata"
    assert assessment.local_path == project / "diagram.png"
    assert len(signals) == 1
    assert signals[0].kind == "image_provenance"


def test_assess_image_rights_accepts_inline_license_context(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "![architecture](diagram.png)\nИсточник: внутренняя схема, лицензия MIT.\n",
        encoding="utf-8",
    )
    (project / "diagram.png").write_bytes(_png_header(480, 320))
    unit = _unit(project)
    entity = extract_entities(unit)[0]

    assessment = assess_image_rights(unit, entity)

    assert assessment.status == "confirmed_inline"
    assert image_rights_signals(unit, [entity]) == []


def test_assess_image_rights_ignores_decorative_images(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("![logo](logo.png)\n", encoding="utf-8")
    (project / "logo.png").write_bytes(_png_header(48, 48))
    unit = _unit(project)
    entity = extract_entities(unit)[0]

    assessment = assess_image_rights(unit, entity)

    assert assessment.status == "ignored_decorative"
    assert image_rights_signals(unit, [entity]) == []
