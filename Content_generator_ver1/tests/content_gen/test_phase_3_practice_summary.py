from types import SimpleNamespace

from content_gen.models.schemas import TheoryPart
from content_gen.practice_phase_executor import _build_theory_summary


def test_build_theory_summary_uses_structured_parts_and_extracts_terms():
    orchestrator = SimpleNamespace(
        theory_parts=[
            TheoryPart(
                title="Работа с волнением",
                body="**Волнение** — это нормальная реакция перед выступлением.",
                example="Ты готовишься к защите проекта.",
                bridge_questions=["Как ты снизишь волнение?"],
            ),
            TheoryPart(
                title="Сторителлинг",
                body="Сторителлинг — это способ передать идею через историю и эмоциональную связку.",
                example="Команда объясняет ценность продукта через кейс.",
                bridge_questions=["Как история помогает удержать внимание?"],
            ),
        ]
    )

    summary, parts_count, terms_count = _build_theory_summary(orchestrator, md="", language="ru")

    assert parts_count == 2
    assert terms_count >= 2
    assert "Работа с волнением" in summary
    assert "Сторителлинг" in summary
    assert "Волнение" in summary
