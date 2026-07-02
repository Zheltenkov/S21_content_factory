import base64
import io
import logging
import zipfile

from api.services.archive_builder import (
    add_assets_to_zip,
    build_readme_filename,
    merge_assets,
)


def test_build_readme_filename_uses_context_metadata() -> None:
    report_json = {
        "context": {"track": "PjM", "last_order": 14},
        "title_en": "Effective Planning!",
    }

    assert build_readme_filename(report_json) == "PjM15_EffectivePlanning.md"


def test_merge_assets_prefers_report_images_and_deduplicates_files() -> None:
    report_assets = {
        "images": [{"name": "diagram.png", "data": "report"}],
        "files": [{"path": "data/input.csv", "data": "report"}],
    }
    result_assets = {
        "images": [{"name": "fallback.png", "data": "result"}],
        "files": [
            {"path": "data/input.csv", "data": "duplicate"},
            {"path": "materials/help.txt", "data": "result"},
        ],
    }

    merged = merge_assets(report_assets, result_assets)

    assert merged["images"] == report_assets["images"]
    assert [file["path"] for file in merged["files"]] == ["data/input.csv", "materials/help.txt"]


def test_add_assets_to_zip_decodes_payloads_and_sanitizes_paths() -> None:
    payload = base64.b64encode(b"hello").decode("ascii")
    assets = {
        "images": [{"name": "../diagram.png", "data": payload}],
        "files": [
            {"path": "../data/input.csv", "data": payload},
            {"path": "data/README.md", "data": payload},
        ],
    }

    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w", zipfile.ZIP_DEFLATED) as archive:
        image_count, file_count = add_assets_to_zip(archive, assets, logging.getLogger(__name__))

    with zipfile.ZipFile(io.BytesIO(archive_bytes.getvalue())) as archive:
        names = set(archive.namelist())
        assert image_count == 1
        assert file_count == 1
        assert names == {"images/diagram.png", "data/input.csv"}
        assert archive.read("data/input.csv") == b"hello"
