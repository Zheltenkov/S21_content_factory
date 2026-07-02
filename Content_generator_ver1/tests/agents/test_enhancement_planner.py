from content_gen.agents.base.llm_client import LLMClientProtocol
from content_gen.agents.enhancement_planner import EnhancementPlanLLMResponse, EnhancementPlanner
from content_gen.models.schemas import ProjectSeed, TheoryPart


class StubLLM(LLMClientProtocol):
    model = "gpt-4o-mini"

    def complete(self, system: str, user: str, response_format=None, **kwargs) -> str:
        raise AssertionError("Raw LLM fallback should not be used in this test")


def _seed() -> ProjectSeed:
    return ProjectSeed(
        language="ru",
        project_type="individual",
        direction="PjM",
        thematic_block="Блок 5. Soft skills",
        project_description="Проект про публичные выступления и сторителлинг.",
        learning_outcomes=["Уметь структурировать выступление"],
        skills=["Сторителлинг", "Публичные выступления"],
    )


def test_create_plan_supports_structured_per_part_list(monkeypatch):
    planner = EnhancementPlanner(StubLLM())
    response = EnhancementPlanLLMResponse(
        per_part=[
            EnhancementPlanLLMResponse.PartPlanData(
                part_index=1,
                topic="Работа с волнением",
                formulas="no",
                tables="must",
                diagrams="nice_to_have",
                code_examples="no",
                reasoning="Для первой части полезна таблица техник.",
                anchor_hints=EnhancementPlanLLMResponse.AnchorHintsData(
                    table="после перечисления техник"
                ),
            )
        ],
        reasoning="Глобальный план",
    )

    monkeypatch.setattr(planner.structured_client, "complete_structured", lambda **kwargs: response)

    plan = planner.create_plan(
        parts=[
            TheoryPart(
                title="Работа с волнением",
                body="**Волнение** — это естественная реакция перед выступлением.",
                example="Команда готовится к питчу.",
                bridge_questions=["Как ты подготовишься к выступлению?"],
            )
        ],
        seed=_seed(),
    )

    assert 1 in plan.per_part
    assert plan.per_part[1].topic == "Работа с волнением"
    assert plan.per_part[1].tables.value == "must"
    assert plan.per_part[1].anchor_hints == {"table": "после перечисления техник"}
    assert plan.fallback_traces == []


def test_create_plan_records_fallback_trace_for_empty_structured_plan(monkeypatch):
    planner = EnhancementPlanner(StubLLM())
    response = EnhancementPlanLLMResponse(per_part=[], reasoning="Пустой план")
    monkeypatch.setattr(planner.structured_client, "complete_structured", lambda **kwargs: response)

    plan = planner.create_plan(
        parts=[
            TheoryPart(
                title="Работа с волнением",
                body="**Волнение** — это естественная реакция перед выступлением.",
                example="Команда готовится к питчу.",
                bridge_questions=["Как ты подготовишься к выступлению?"],
            )
        ],
        seed=_seed(),
    )

    assert plan.fallback_traces
    assert plan.fallback_traces[0]["node"] == "theory_enhancement"
    assert plan.fallback_traces[0]["fallback_type"] == "empty_enhancement_plan"
    assert plan.fallback_traces[0]["quality_risk"] == "medium"
