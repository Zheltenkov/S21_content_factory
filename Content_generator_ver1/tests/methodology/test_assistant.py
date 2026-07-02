from content_gen.methodology import (
    MethodologyAssistantCommandParser,
    MethodologyAssistantParseContext,
    SectionTarget,
    SectionTargetRegistry,
)


def test_assistant_parser_binds_approve_to_checkpoint() -> None:
    parser = MethodologyAssistantCommandParser()
    command = parser.parse(
        "Продолжить",
        MethodologyAssistantParseContext(
            checkpoint={"id": "practice-check", "stage": "practice", "node_id": "practice"},
        ),
    )

    assert command.command == "approve"
    assert command.checkpoint_id == "practice-check"
    assert command.checkpoint_stage == "practice"
    assert command.node_id == "practice"


def test_assistant_parser_simplify_task_targets_practice_task() -> None:
    parser = MethodologyAssistantCommandParser()
    registry = SectionTargetRegistry(
        targets=[
            SectionTarget(
                id="practice.task.1",
                kind="markdown_section",
                label="Задание 1. SQL-агрегации",
                stage="practice",
                selector="practice.task.1",
                scope="task_only",
            ),
            SectionTarget(
                id="practice.task.2",
                kind="markdown_section",
                label="Задание 2. Визуализация",
                stage="practice",
                selector="practice.task.2",
                scope="task_only",
            ),
        ]
    )

    command = parser.parse(
        "Упрости задачу 2, она слишком большая",
        MethodologyAssistantParseContext(target_registry=registry),
    )

    assert command.command == "simplify_task"
    assert command.target_stage == "practice"
    assert command.target_id == "practice.task.2"
    assert command.scope == "task_only"
    assert "Упрости выбранную практическую задачу" in command.instruction


def test_assistant_parser_fix_failed_criteria_uses_failed_matrix_ids() -> None:
    parser = MethodologyAssistantCommandParser()
    registry = SectionTargetRegistry(
        targets=[
            SectionTarget(
                id="final",
                kind="markdown_section",
                label="Финальная сборка",
                stage="final",
                selector="final",
                scope="local_section_only",
            )
        ]
    )
    context = MethodologyAssistantParseContext(
        checkpoint={
            "id": "final-check",
            "stage": "evaluation",
            "artifact": {
                "requirements_matrix": [
                    {"id": "R-18", "status": "failed"},
                    {"id": "S-01", "status": "passed"},
                    {"id": "T-06", "passed": False},
                ]
            },
        },
        target_registry=registry,
    )

    command = parser.parse("Исправь непройденные критерии", context)

    assert command.command == "fix_failed_criteria"
    assert command.target_stage == "final"
    assert command.target_id == "final"
    assert command.issue_codes == ["R-18", "T-06"]
    assert "R-18" in command.instruction


def test_assistant_parser_regenerate_section_targets_workflow_node() -> None:
    parser = MethodologyAssistantCommandParser()
    registry = SectionTargetRegistry(
        targets=[
            SectionTarget(
                id="theory.chapter",
                kind="markdown_section",
                label="Глава 2. Теоретический блок",
                stage="theory",
                selector="chapter_2",
                scope="local_section_only",
            )
        ]
    )

    command = parser.parse(
        "Перегенерируй главу 2, текущая теория слишком общая",
        MethodologyAssistantParseContext(
            checkpoint={"id": "theory-review", "stage": "theory", "node_id": "theory"},
            target_registry=registry,
        ),
    )

    assert command.command == "regenerate_section"
    assert command.checkpoint_id == "theory-review"
    assert command.target_stage == "theory"
    assert command.target_id == "theory.chapter"
    assert command.workflow_node_id == "theory"


def test_assistant_parser_routes_diagram_fix_to_current_section() -> None:
    parser = MethodologyAssistantCommandParser()
    registry = SectionTargetRegistry(
        targets=[
            SectionTarget(
                id="theory.chapter",
                kind="markdown_section",
                label="Глава 2. Теоретический блок",
                stage="theory",
                selector="chapter_2",
                scope="local_section_only",
            )
        ]
    )

    command = parser.parse(
        "Поправь диаграмму: блоки черные, сделай светлыми и читаемыми",
        MethodologyAssistantParseContext(
            checkpoint={"id": "theory-review", "stage": "theory", "node_id": "theory"},
            target_registry=registry,
        ),
    )

    assert command.command == "request_changes"
    assert command.target_stage == "theory"
    assert command.target_id == "theory.chapter"
    assert command.scope == "local_section_only"
    assert "Mermaid" in command.instruction
    assert "classDef" in command.instruction
