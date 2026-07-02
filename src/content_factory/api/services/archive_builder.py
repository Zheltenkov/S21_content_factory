"""Shared ZIP/archive helpers for generated README artifacts."""

from __future__ import annotations

import base64
import re
import unicodedata
import zipfile
from collections.abc import Iterable
from typing import Any


def build_readme_filename(report_json: dict[str, Any] | None, suffix: str = "") -> str:
    """Build README filename as <Track><order>_<EnglishTitle>.md."""
    track_code = "PROJECT"
    hierarchy_index = 1
    title_en = "README"

    if report_json and isinstance(report_json, dict):
        context_meta = report_json.get("context") or {}
        if isinstance(context_meta, dict):
            if context_meta.get("track"):
                track_code = str(context_meta["track"]).strip() or track_code
            elif context_meta.get("thematic_block"):
                track_code = str(context_meta["thematic_block"]).strip() or track_code
            last_order = context_meta.get("last_order")
            if isinstance(last_order, int):
                hierarchy_index = max(1, last_order + 1)

        raw_title = report_json.get("title_en") or report_json.get("title") or title_en
        safe_title = re.sub(r"[^A-Za-z0-9_-]+", "", str(raw_title))
        title_en = safe_title or title_en

    track_code = re.sub(r"[^A-Za-z0-9]+", "", track_code) or "PROJECT"
    base_name = f"{track_code}{hierarchy_index}_{title_en}"
    if suffix:
        base_name = f"{base_name}_{suffix}"
    return f"{base_name}.md"


def transliterate_filename(filename: str) -> str:
    """Return an ASCII-safe filename for HTTP Content-Disposition."""
    cleaned = filename
    for char in r'<>:"/\|?*':
        cleaned = cleaned.replace(char, "_")
    try:
        cleaned = unicodedata.normalize("NFKD", cleaned).encode("ascii", "ignore").decode("ascii")
    except Exception:
        pass
    return "_".join(cleaned.split()).replace("__", "_").replace("__", "_")


def merge_assets(
    report_assets: dict[str, Any] | None,
    result_assets: dict[str, Any] | None,
) -> dict[str, list[dict[str, Any]]]:
    """Merge base64 report assets with optional in-memory binary result assets."""
    report_assets = report_assets if isinstance(report_assets, dict) else {}
    result_assets = result_assets if isinstance(result_assets, dict) else {}

    merged: dict[str, list[dict[str, Any]]] = {"images": [], "files": []}
    report_images = report_assets.get("images") or []
    result_images = result_assets.get("images") or []
    merged["images"] = list(report_images if report_images else result_images)

    files_by_path: dict[str, dict[str, Any]] = {}
    for asset in list(report_assets.get("files") or []) + list(result_assets.get("files") or []):
        if not isinstance(asset, dict):
            continue
        path = asset.get("path")
        if path and path not in files_by_path:
            files_by_path[path] = asset
    merged["files"] = list(files_by_path.values())
    return merged


def decode_asset_bytes(data: Any) -> bytes | None:
    """Decode asset payload from base64 string or pass through bytes."""
    if isinstance(data, bytes):
        return data if data else None
    if isinstance(data, str):
        try:
            decoded = base64.b64decode(data)
        except Exception:
            return None
        return decoded if decoded else None
    return None


def safe_archive_path(path: str, fallback_name: str | None = None) -> str | None:
    """Normalize a path for ZIP entry names and prevent traversal entries."""
    cleaned = (path or "").strip().replace("\\", "/")
    cleaned = re.sub(r"^[a-zA-Z]:", "", cleaned)
    cleaned = re.sub(r"^[a-zA-Z]+://", "", cleaned)
    parts = [part for part in cleaned.split("/") if part and part not in {".", ".."}]
    if not parts and fallback_name:
        parts = [fallback_name]
    if not parts:
        return None
    return "/".join(parts)


def _iter_valid_assets(assets: Iterable[dict[str, Any]] | None) -> Iterable[dict[str, Any]]:
    if not assets:
        return []
    return [asset for asset in assets if isinstance(asset, dict)]


def add_images_to_zip(
    archive: zipfile.ZipFile,
    images: Iterable[dict[str, Any]] | None,
    logger: Any,
    prefix: str = "images",
) -> int:
    """Add image assets to an archive and return added count."""
    added = 0
    for image in _iter_valid_assets(images):
        name = image.get("name")
        payload = decode_asset_bytes(image.get("data"))
        safe_name = safe_archive_path(str(name or ""), fallback_name=f"image-{added + 1}.bin")
        if not safe_name or not payload:
            logger.warning("Пропущено изображение без валидных данных или имени: %s", image)
            continue
        archive.writestr(f"{prefix}/{safe_name.split('/')[-1]}", payload)
        added += 1
    return added


def add_files_to_zip(
    archive: zipfile.ZipFile,
    files: Iterable[dict[str, Any]] | None,
    logger: Any,
) -> int:
    """Add non-image file assets to an archive and return added count."""
    added = 0
    for asset in _iter_valid_assets(files):
        path = str(asset.get("path") or "")
        path_lower = path.lower()
        if ("materials/" in path_lower or "data/" in path_lower) and "readme" in path_lower:
            logger.warning("Пропущен README в папке данных: %s", path)
            continue

        payload = decode_asset_bytes(asset.get("data"))
        safe_path = safe_archive_path(path)
        if not safe_path or not payload:
            logger.warning("Пропущен файл без валидных данных или пути: %s", asset)
            continue
        archive.writestr(safe_path, payload)
        added += 1
    return added


def add_assets_to_zip(
    archive: zipfile.ZipFile,
    assets: dict[str, Any] | None,
    logger: Any,
) -> tuple[int, int]:
    """Add images and files from a normalized assets dict."""
    assets = assets if isinstance(assets, dict) else {}
    images_count = add_images_to_zip(archive, assets.get("images"), logger)
    files_count = add_files_to_zip(archive, assets.get("files"), logger)
    return images_count, files_count
