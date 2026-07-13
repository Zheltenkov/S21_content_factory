from __future__ import annotations

import json

from content_factory.catalog.pipeline import brief_evidence


def test_live_search_normalizes_string_and_partial_object_items(monkeypatch) -> None:
    monkeypatch.setattr(brief_evidence.config, "USE_LIVE", True)
    monkeypatch.setattr(brief_evidence.llm, "chat", lambda *_args, **_kwargs: {"citations": ["https://one"]})
    monkeypatch.setattr(
        brief_evidence.llm,
        "content",
        lambda _response: json.dumps(
            [
                "Проводит интервью с клиентами",
                {"title": "Проверяет продуктовые гипотезы", "source_type": "unknown"},
                42,
            ],
            ensure_ascii=False,
        ),
    )
    monkeypatch.setattr(brief_evidence.llm, "citations", lambda _response: ["https://one", "https://two"])

    items = brief_evidence.search("customer discovery")

    assert items == [
        {
            "claim": "Проводит интервью с клиентами",
            "source_type": "other",
            "url": "https://one",
            "snippet": "",
            "retrieved_at": items[0]["retrieved_at"],
        },
        {
            "claim": "Проверяет продуктовые гипотезы",
            "source_type": "other",
            "url": "https://two",
            "snippet": "",
            "retrieved_at": items[1]["retrieved_at"],
        },
    ]


def test_search_normalizer_unwraps_provider_container() -> None:
    items = brief_evidence._normalize_search_items(
        {
            "results": [
                {
                    "claim": "Настраивает CI",
                    "source_type": "framework",
                    "snippet": "Pipeline quality gate",
                }
            ]
        }
    )

    assert len(items) == 1
    assert items[0]["claim"] == "Настраивает CI"
    assert items[0]["source_type"] == "framework"
    assert items[0]["snippet"] == "Pipeline quality gate"
