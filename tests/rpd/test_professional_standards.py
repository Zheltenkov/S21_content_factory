"""Local professional-standard catalog: deterministic, versioned and failure-tolerant."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from content_factory.rpd.professional_standards import (
    ProfessionalStandardCatalog,
    load_configured_professional_standard_catalog,
)
from content_factory.rpd.up_models import from_registry_plan


def _payload() -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "snapshot_at": "2026-08-07T00:00:00Z",
            "standards": [
                {
                    "registry_number": "1000",
                    "code": "06.035",
                    "title": "Разработчик Web и мультимедийных приложений",
                    "activity_type": "Разработка цифровых продуктов",
                    "functions": [
                        {
                            "code": "A/01.6",
                            "title": "Проектирование цифрового продукта",
                            "required_skills": ["Проверять продуктовые гипотезы"],
                        }
                    ],
                }
            ],
        },
        ensure_ascii=False,
    ).encode()


def test_catalog_validates_fingerprints_and_searches_snapshot() -> None:
    payload = _payload()
    catalog = ProfessionalStandardCatalog.from_json_bytes(payload)
    up = from_registry_plan(
        {
            "title": "Разработка цифровых продуктов",
            "rows": [
                {
                    "block_title": "Продукт",
                    "skills": ["Проверять продуктовые гипотезы"],
                }
            ],
        }
    )

    assert catalog.source_sha256 == hashlib.sha256(payload).hexdigest()
    assert catalog.by_code("06.035") is not None
    assert [item.code for item in catalog.search(up)] == ["06.035"]


def test_broken_configured_snapshot_degrades_to_empty_catalog(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("not-json", encoding="utf-8")

    catalog = load_configured_professional_standard_catalog(path)

    assert catalog.standards == []
    assert catalog.load_error
