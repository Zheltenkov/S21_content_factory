from __future__ import annotations

from content_factory.catalog.pipeline.curriculum.brief_questions import question_key
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


def test_live_brief_area_paraphrases_count_as_covered() -> None:
    area_pairs = [
        (
            "выявление проблемы и понимание клиента",
            "Постановка и первичное исследование проблемы, понимание клиента, формулирование боли и контекста использования",
        ),
        (
            "исследование рынка и проверка гипотез",
            "Анализ рынка, конкурентной среды и первичная валидация продуктовых гипотез",
        ),
        (
            "формулирование целевого сегмента и ценностного предложения",
            "Определение целевого сегмента, задачи пользователя, ценностного предложения и ключевых сценариев",
        ),
        (
            "постановка продуктовой стратегии и приоритизация",
            "Постановка продуктовой стратегии, выбор фокуса и приоритизация работ для запуска",
        ),
        (
            "определение границ MVP и сборка минимально жизнеспособного продукта",
            "Выбор состава MVP, его ограничений и реализация минимально жизнеспособного решения",
        ),
        (
            "архитектурное мышление и базовая разработка с AI",
            "Проектирование простой архитектуры цифрового продукта и использование AI в разработке",
        ),
        (
            "базовая инженерная дисциплина: репозиторий, CI, тесты, релизы",
            "Работа с репозиторием, тестированием, непрерывной интеграцией и релизами",
        ),
        (
            "инфраструктура продукта: развертывание, наблюдаемость, резервное копирование, инциденты",
            "Развертывание, наблюдаемость, резервное копирование и готовность к инцидентам",
        ),
        (
            "маркетинг цифрового продукта: позиционирование, сообщение, лендинг, каналы привлечения",
            "Позиционирование, ключевое сообщение, лендинг и каналы привлечения",
        ),
        (
            "продажи и монетизация: тарифы, пробный доступ, коммерческие материалы, unit economics",
            "Тарифы, пробный доступ, коммерческие материалы, базовые продажи и unit economics",
        ),
        (
            "поддержка пользователей и работа с обратной связью",
            "Организация помощи пользователям, сбор и обработка обратной связи, улучшение продукта",
        ),
        (
            "правовые, финансовые и административные основы продукта",
            "Базовые юридические, финансовые и административные вопросы запуска цифрового продукта",
        ),
        (
            "стратегия, управление рисками и регулярный управленческий ритм",
            "Постановка целей, управленческий ритм, оценка рисков и дальнейшее развитие продукта",
        ),
        (
            "контроль качества результатов AI, безопасность и human-in-the-loop",
            "Проверка качества результатов AI, безопасность, соблюдение требований и human-in-the-loop",
        ),
    ]
    required_areas = [required for required, _candidate in area_pairs]
    nodes = [_node(f"L{index}", candidate) for index, (_required, candidate) in enumerate(area_pairs, start=1)]

    design = build_curriculum_design_spec(
        {"must_include_areas": required_areas},
        nodes,
        {"order": [{"id": node.tmp_id} for node in nodes], "final_edges": []},
    )

    assert design.uncovered_required_areas == ()
    assert [area for stage in design.stages for area in stage.coverage_areas] == required_areas


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


def test_approved_design_with_blocking_question_is_blocked_by_questions() -> None:
    # An unclassified methodical question blocks publication by default (slice 7); the
    # draft is still buildable (ready is True), so this is coherent with the epic invariant.
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
    assert accepted.blocking_question_count == 1
    assert accepted.readiness_state == "blocked_by_questions"


def test_approved_design_with_only_editorial_question_is_ready_with_questions() -> None:
    design = build_curriculum_design_spec(
        {
            "must_include_areas": ["Сбор данных", "Анализ", "Публикация"],
            "raw_text": "Можно переформулировать название второго блока?",
        },
        [_node("D1", "Сбор данных"), _node("D2", "Анализ"), _node("D3", "Публикация")],
        {"order": [{"id": "D1"}, {"id": "D2"}, {"id": "D3"}], "final_edges": []},
    )

    accepted = approve_curriculum_design_spec(design)

    assert accepted.ready is True
    assert accepted.blocking_question_count == 0
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


def test_question_answers_are_versioned_in_design_and_close_blocker() -> None:
    question = "Какая целевая аудитория программы?"
    design = build_curriculum_design_spec(
        {
            "raw_text": question,
            "curriculum_design_spec": {
                "version": "curriculum-design:v3",
                "question_answers": {
                    question_key(question): "Junior-специалисты, запускающие первый продукт."
                },
            },
        },
        [_node("A", "Основы")],
        {"order": [{"id": "A"}], "final_edges": []},
    )

    assert design.blocking_question_count == 0
    assert design.brief_questions[0].status == "answered"
    assert approve_curriculum_design_spec(design).readiness_state == "ready"
    assert design.as_dict()["question_answers"] == {
        question_key(question): "Junior-специалисты, запускающие первый продукт."
    }
    assert len(design.design_hash) == 64


def test_legacy_design_reextracts_questions_with_current_parser() -> None:
    raw_text = (
        "Портрет участника: способен запускать продукт. "
        "Какие дополнительные ресурсы нужны, чтобы создать и запустить продукт?"
    )
    design = build_curriculum_design_spec(
        {
            "raw_text": raw_text,
            "curriculum_design_spec": {
                "version": "curriculum-design:v3",
                "open_questions": [raw_text],
            },
        },
        [],
        {},
    )

    assert design.version == "curriculum-design:v5"
    assert design.open_questions == (
        "Какие дополнительные ресурсы нужны, чтобы создать и запустить продукт?",
    )
