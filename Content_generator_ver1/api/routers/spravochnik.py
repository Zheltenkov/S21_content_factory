"""Spravochnik integration API for migration and status checks."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.db.models import SpravochnikCatalogEntity
from api.db.session import get_db_session
from api.db.tool_runs_db import import_spravochnik_catalog
from api.dependencies import get_current_user
from api.integrations.project_paths import spravochnik_sqlite_path

router = APIRouter(prefix="/spravochnik", tags=["spravochnik"])


@router.get("/migration/status")
async def get_spravochnik_migration_status(
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> dict[str, object]:
    """Return source and target counts for the catalog migration."""

    sqlite_path = spravochnik_sqlite_path()
    target_counts = {
        row.entity_type: row.count
        for row in db.query(
            SpravochnikCatalogEntity.entity_type,
            func.count(SpravochnikCatalogEntity.id).label("count"),
        )
        .group_by(SpravochnikCatalogEntity.entity_type)
        .all()
    }
    return {
        "user_id": user.get("id"),
        "source": str(sqlite_path),
        "source_exists": sqlite_path.exists(),
        "target_counts": target_counts,
    }


@router.post("/migration/import")
async def import_spravochnik_to_common_db(
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> dict[str, object]:
    """Import current Spravochnik catalog rows into the unified PostgreSQL database."""

    sqlite_path = spravochnik_sqlite_path()
    if not sqlite_path.exists():
        raise HTTPException(status_code=404, detail=f"SQLite справочника не найден: {sqlite_path}")
    counts = import_spravochnik_catalog(db, sqlite_path)
    return {
        "user_id": user.get("id"),
        "source": str(sqlite_path),
        "imported": counts,
    }
