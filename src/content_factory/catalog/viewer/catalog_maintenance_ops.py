"""Catalog maintenance helpers shared by legacy and native UI paths."""

from __future__ import annotations

from content_factory.catalog.db import CatalogConnection
from content_factory.catalog.viewer._common import clean_profile_name, clean_profile_slug, table_exists, utc_now_iso


def repair_dirty_profile_names(conn: CatalogConnection) -> int:
    """Normalize legacy profile names/slugs that carried warning suffixes."""

    if not table_exists(conn, "profile"):
        return 0
    updated = 0
    for row in conn.execute("SELECT id, name, slug FROM profile ORDER BY id").fetchall():
        current_name = str(row["name"] or "")
        cleaned_name = clean_profile_name(current_name)
        current_slug = str(row["slug"] or "")
        cleaned_slug = clean_profile_slug(current_slug)
        if cleaned_name and cleaned_name != current_name:
            conn.execute("UPDATE profile SET name = ? WHERE id = ?", (cleaned_name, row["id"]))
            updated += 1
        if cleaned_slug and cleaned_slug != current_slug:
            exists = conn.execute(
                "SELECT 1 FROM profile WHERE slug = ? AND id != ?",
                (cleaned_slug, row["id"]),
            ).fetchone()
            if not exists:
                conn.execute("UPDATE profile SET slug = ? WHERE id = ?", (cleaned_slug, row["id"]))
                updated += 1
    if updated:
        conn.commit()
    return updated


def ensure_catalog_group(
    conn: CatalogConnection,
    code: str,
    name: str,
    sort_order: int,
    status: str = "active",
    source: str = "derived",
) -> int:
    """Create or update a skill group and return its id."""

    row = conn.execute(
        "SELECT id FROM skill_group WHERE code = ? OR name = ? ORDER BY id LIMIT 1",
        (code, name),
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE skill_group
            SET code = ?,
                name = ?,
                sort_order = ?,
                status = ?,
                source = COALESCE(NULLIF(source, ''), ?),
                updated_at = ?
            WHERE id = ?
            """,
            (code, name, sort_order, status, source, utc_now_iso(), int(row["id"])),
        )
        return int(row["id"])
    cursor = conn.execute(
        """
        INSERT INTO skill_group(code, name, sort_order, status, source, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (code, name, sort_order, status, source, utc_now_iso()),
    )
    return int(cursor.lastrowid or 0)
