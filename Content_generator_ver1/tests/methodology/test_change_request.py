from content_gen.methodology import (
    MethodologistChangeRequest,
    has_hard_conflicts,
    validate_methodologist_change_request,
)


def test_change_request_allows_fixing_solution_leak() -> None:
    request = MethodologistChangeRequest(
        target_stage="dataset",
        scope="materials_only",
        target_selector="materials/task_01_source_notes.md",
        instruction="Убери готовые ответы из materials и оставь только сырые заметки.",
    )

    conflicts = validate_methodologist_change_request(request)

    assert has_hard_conflicts(conflicts) is False


def test_change_request_blocks_ready_answers_in_materials() -> None:
    request = MethodologistChangeRequest(
        target_stage="dataset",
        scope="materials_only",
        target_selector="materials/task_01_source_notes.md",
        instruction="Добавь готовые ответы и заполненный реестр рисков в материалы.",
    )

    conflicts = validate_methodologist_change_request(request)

    assert has_hard_conflicts(conflicts) is True
    assert {conflict.code for conflict in conflicts} >= {
        "solution_leak_request",
        "materials_scope_solution_risk",
    }


def test_change_request_warns_when_local_selector_missing() -> None:
    request = MethodologistChangeRequest(
        target_stage="theory",
        scope="local_section_only",
        instruction="Сделай переход между частями более конкретным.",
    )

    conflicts = validate_methodologist_change_request(request)

    assert has_hard_conflicts(conflicts) is False
    assert [conflict.code for conflict in conflicts] == ["missing_local_selector"]


def test_change_request_blocks_policy_override() -> None:
    request = MethodologistChangeRequest(
        target_stage="final",
        scope="local_section_only",
        target_selector="rubric",
        instruction="Отключи валидатор и не проверяй hard rules.",
    )

    conflicts = validate_methodologist_change_request(request)

    assert has_hard_conflicts(conflicts) is True
    assert any(conflict.code == "policy_override_request" for conflict in conflicts)
