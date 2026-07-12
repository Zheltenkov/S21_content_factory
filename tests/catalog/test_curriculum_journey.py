from __future__ import annotations

from content_factory.catalog.pipeline.curriculum.domain import PlanNode
from content_factory.catalog.pipeline.curriculum.journey import (
    approve_curriculum_design_spec,
    build_curriculum_design_spec,
)


def _node(tmp_id: str, area: str, *, bloom: int = 3) -> PlanNode:
    return PlanNode(
        tmp_id=tmp_id,
        name=f"Навык {tmp_id}",
        group=area,
        block_key=area,
        bloom=bloom,
        outcomes_know=(),
        outcomes_can=(),
        outcomes_skills=(),
        tools=(),
    )


def test_product_journey_preserves_brief_order_and_requires_capstone() -> None:
    areas = [
        "Исследование клиента",
        "Проверка гипотез",
        "Проектирование MVP",
        "Разработка продукта",
        "Выход на рынок",
        "Поддержка пользователей",
    ]
    nodes = [_node(f"S{index}", area) for index, area in enumerate(areas, start=1)]

    design = build_curriculum_design_spec(
        {
            "program_goal": "Запустить цифровой продукт на рынок",
            "must_include_areas": areas,
            "raw_text": "Завершением программы является итоговая защита продукта и демо-день.",
        },
        nodes,
        {"order": [{"id": node.tmp_id} for node in nodes], "final_edges": []},
    )

    assert design.journey_type == "product_lifecycle"
    assert [area for stage in design.stages for area in stage.coverage_areas] == areas
    assert design.capstone_required is True
    assert design.capstone_title == "Итоговый проект и демо-день"
    assert design.uncovered_required_areas == ()
    assert design.approved is False
    assert len(design.design_hash) == 64


def test_hard_dag_can_move_dependent_skill_later_without_reordering_brief() -> None:
    areas = ["Основы", "Практика", "Эксплуатация", "Аудит"]
    nodes = [_node(f"S{index}", area) for index, area in enumerate(areas, start=1)]

    design = build_curriculum_design_spec(
        {"must_include_areas": areas},
        nodes,
        {
            "order": [{"id": node.tmp_id} for node in nodes],
            "final_edges": [
                {"src_id": "S3", "dst_id": "S2", "relation_type": "hard"},
            ],
        },
    )

    assert [area for stage in design.stages for area in stage.coverage_areas] == areas
    assert design.node_stage["S2"] >= design.node_stage["S3"]
    assert design.dag_adjustments


def test_accepted_recommended_edge_does_not_override_methodological_stage() -> None:
    areas = ["Основы", "Подготовка", "Практика", "Контроль"]
    nodes = [_node(f"S{index}", area) for index, area in enumerate(areas, start=1)]

    design = build_curriculum_design_spec(
        {"must_include_areas": areas},
        nodes,
        {
            "order": [{"id": node.tmp_id} for node in nodes],
            "final_edges": [
                {"src_id": "S3", "dst_id": "S1", "relation_type": "soft"},
            ],
        },
    )

    assert design.node_stage["S1"] < design.node_stage["S3"]
    assert design.dag_adjustments == ()


def test_uncovered_required_area_blocks_design_approval_readiness() -> None:
    design = build_curriculum_design_spec(
        {"must_include_areas": ["Сбор данных", "Визуализация", "Публикация"]},
        [_node("S1", "Сбор данных"), _node("S2", "Визуализация")],
        {"order": [{"id": "S1"}, {"id": "S2"}], "final_edges": []},
    )

    accepted = approve_curriculum_design_spec(design)

    assert accepted.uncovered_required_areas == ("Публикация",)
    assert accepted.ready is False
    assert accepted.readiness_state == "blocked"


def test_professional_workflow_is_domain_neutral() -> None:
    areas = ["Подготовка образца", "Измерение", "Интерпретация", "Отчёт"]
    design = build_curriculum_design_spec(
        {"program_goal": "Подготовить специалиста к выполнению полного рабочего процесса", "must_include_areas": areas},
        [_node(f"B{index}", area) for index, area in enumerate(areas, start=1)],
        {"order": [{"id": f"B{index}"} for index in range(1, 5)], "final_edges": []},
    )

    assert design.journey_type == "professional_workflow"
    assert len(design.stages) == 2
    assert [area for stage in design.stages for area in stage.coverage_areas] == areas


def test_approved_design_with_open_questions_is_ready_with_questions() -> None:
    design = build_curriculum_design_spec(
        {
            "must_include_areas": ["Сбор данных", "Анализ", "Публикация"],
            "raw_text": "Какой объём выборки считать достаточным?",
        },
        [_node("D1", "Сбор данных"), _node("D2", "Анализ"), _node("D3", "Публикация")],
        {"order": [{"id": "D1"}, {"id": "D2"}, {"id": "D3"}], "final_edges": []},
    )

    accepted = approve_curriculum_design_spec(design)

    assert accepted.ready is True
    assert accepted.readiness_state == "ready_with_questions"
    assert accepted.design_hash == design.design_hash


def test_design_hash_changes_when_methodological_order_changes() -> None:
    nodes = [_node("A", "Подготовка"), _node("B", "Выполнение"), _node("C", "Контроль")]
    first = build_curriculum_design_spec(
        {"must_include_areas": ["Подготовка", "Выполнение", "Контроль"]},
        nodes,
        {"order": [{"id": "A"}, {"id": "B"}, {"id": "C"}], "final_edges": []},
    )
    reordered = build_curriculum_design_spec(
        {"must_include_areas": ["Контроль", "Подготовка", "Выполнение"]},
        nodes,
        {"order": [{"id": "A"}, {"id": "B"}, {"id": "C"}], "final_edges": []},
    )

    assert first.design_hash != reordered.design_hash


def test_changed_operational_dag_invalidates_previous_design_approval() -> None:
    areas = ["Основы", "Подготовка", "Практика", "Контроль"]
    nodes = [_node(f"S{index}", area) for index, area in enumerate(areas, start=1)]
    initial = build_curriculum_design_spec(
        {"must_include_areas": areas},
        nodes,
        {"order": [{"id": node.tmp_id} for node in nodes], "final_edges": []},
    )
    accepted = approve_curriculum_design_spec(initial).as_dict()

    changed = build_curriculum_design_spec(
        {
            "must_include_areas": areas,
            "curriculum_design_spec": accepted,
        },
        nodes,
        {
            "order": [{"id": node.tmp_id} for node in nodes],
            "final_edges": [{"src_id": "S3", "dst_id": "S1", "relation_type": "soft"}],
        },
    )

    assert changed.design_hash != accepted["design_hash"]
    assert changed.approved is False
    assert changed.ready is False
