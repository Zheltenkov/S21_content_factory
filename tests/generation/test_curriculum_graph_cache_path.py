"""Cache path for a curriculum track must survive real UP stage titles."""

from __future__ import annotations

from content_factory.generation.curriculum.graph import _graph_path, _track_slug

# Verbatim from the pilot log that failed with OSError 36 (File name too long).
LONG_TRACK = (
    "маркетинг технологического продукта: позиционирование, сообщение, страница, каналы "
    "→ продажи и монетизация: тарифы, trial, материалы для продаж, unit economics"
)


def test_long_stage_title_produces_a_writable_filename() -> None:
    name = _graph_path(LONG_TRACK).name

    # ext4/APFS cap filenames at 255 BYTES — Cyrillic is 2 bytes per char in UTF-8.
    assert len(name.encode("utf-8")) < 255
    # ':' is illegal on Windows, '/' would split the path into directories.
    assert not {":", "/", "\\"} & set(name)
    assert name.endswith("_curriculum.json")


def test_distinct_tracks_do_not_collide_after_truncation() -> None:
    shared_prefix = "маркетинг технологического продукта: позиционирование, сообщение, страница"
    first = _track_slug(shared_prefix + " → продажи и монетизация: тарифы и unit economics")
    second = _track_slug(shared_prefix + " → поддержка пользователей и цикл обратной связи")

    assert first != second


def test_same_track_is_stable_across_calls() -> None:
    assert _track_slug(LONG_TRACK) == _track_slug(LONG_TRACK)


def test_plain_track_stays_readable() -> None:
    assert _track_slug("python-backend").startswith("python-backend-")
