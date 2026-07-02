from __future__ import annotations

import json

from content_gen.subtitles.burned_pipeline import translate_segments_llm


class RecordingSubtitleLLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def complete(self, system: str, user: str, response_format=None, **kwargs) -> str:
        self.calls.append({"system": system, "user": user})
        return json.dumps([{"id": 1, "text": "Кыргызча текст"}], ensure_ascii=False)


def test_subtitle_translation_uses_language_script_profile_for_kyrgyz() -> None:
    llm = RecordingSubtitleLLM()

    result = translate_segments_llm(
        [{"id": 1, "start": 0.0, "end": 1.0, "text": "Русский текст"}],
        "kg",
        llm,
    )

    assert result[0]["text"] == "Кыргызча текст"
    assert "кыргызской кириллицей" in llm.calls[0]["system"]
    assert "кыргызской кириллицей" in llm.calls[0]["user"]
    assert "Пиши результат латиницей" not in llm.calls[0]["user"]
