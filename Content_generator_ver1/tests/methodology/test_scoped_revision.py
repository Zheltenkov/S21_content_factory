import pytest

from content_gen.exceptions import ContentGenerationError
from content_gen.agents.flow import FlowExecutionStep
from content_gen.methodology import MethodologistChangeRequest, ScopedRevisionExecutor
from content_gen.methodology.scoped_revision import ScopedRevisionResult


class FakeLLM:
    def __init__(self, response: str | None = None) -> None:
        self.response = response
        self.prompts: list[dict[str, str]] = []

    def complete(self, system: str, user: str, **_kwargs) -> str:
        self.prompts.append({"system": system, "user": user})
        if self.response is not None:
            return self.response
        return user.split("ФРАГМЕНТ ДЛЯ ПРАВКИ:", 1)[1].strip().replace("Старый текст", "Новый текст")


def test_scoped_revision_edits_only_selected_markdown_section_and_preserves_blocks() -> None:
    markdown = """# Проект

## Глава 2. Теоретический блок

Старый текст.

```mermaid
flowchart TD
    A[Старт] --> B[Финиш]
```

| Поле | Значение |
| --- | --- |
| A | B |

## Глава 3. Практический блок

Практика без изменений.
"""
    context = {"markdown": markdown}
    executor = ScopedRevisionExecutor(FakeLLM())
    request = MethodologistChangeRequest(
        target_stage="theory",
        target_selector="Глава 2",
        scope="local_section_only",
        instruction="Уточни формулировку в теории.",
    )

    result = executor.apply_change_request(context, request, action_id="a1")

    assert result.status == "applied"
    assert "Новый текст" in context["markdown"]
    assert "Практика без изменений." in context["markdown"]
    assert "```mermaid" in context["markdown"]
    assert "| Поле | Значение |" in context["markdown"]
    assert context["practice_tasks"] == []
    assert context["dataset_files"] == []
    assert result.recommended_resume_node == "practice"
    assert result.target_id == "chapter_2"
    assert result.diff_preview
    assert result.before_hash != result.after_hash


def test_scoped_revision_can_target_nested_section_by_registry_id() -> None:
    markdown = """# Проект

## Глава 2. Теоретический блок

### 2.1. Старый раздел

Старый текст.

### 2.2. Другой раздел

Старый текст, который нельзя менять.
"""
    context = {"markdown": markdown}
    executor = ScopedRevisionExecutor(FakeLLM())
    request = MethodologistChangeRequest(
        target_stage="theory",
        target_selector="chapter_2.part_1",
        scope="local_section_only",
        instruction="Уточни только первую часть.",
    )

    result = executor.apply_change_request(context, request, action_id="a1")

    assert result.status == "applied"
    assert result.target_id == "chapter_2.part_1"
    assert "### 2.1. Старый раздел\n\nНовый текст." in context["markdown"]
    assert "Старый текст, который нельзя менять." in context["markdown"]


def test_scoped_revision_can_target_annotation_without_editing_intro() -> None:
    markdown = """# Проект

Старый текст аннотации.

## Глава 1. Введение и инструкция

### Введение

Введение без изменений.
"""
    context = {
        "markdown": markdown,
        "title": "Проект",
        "annotation": {"text": "Старый текст аннотации.", "chars": 24},
    }
    executor = ScopedRevisionExecutor(FakeLLM())
    request = MethodologistChangeRequest(
        target_stage="annotation",
        target_selector="annotation",
        scope="local_section_only",
        instruction="Уточни только аннотацию.",
    )

    result = executor.apply_change_request(context, request, action_id="a1")

    assert result.status == "applied"
    assert result.target_id == "annotation"
    assert result.recommended_resume_node == "theory"
    assert "Новый текст аннотации." in context["markdown"]
    assert "Введение без изменений." in context["markdown"]
    assert context["annotation"]["text"] == "Новый текст аннотации."
    assert context["title"] == "Проект"


def test_scoped_revision_can_target_title_field_before_markdown() -> None:
    context = {
        "title": "Старый проект",
        "annotation": {"text": "Аннотация.", "chars": 10},
    }
    executor = ScopedRevisionExecutor(FakeLLM("Новый проект"))
    request = MethodologistChangeRequest(
        target_stage="title",
        target_selector="title",
        scope="local_section_only",
        instruction="Сделай название конкретнее.",
    )

    result = executor.apply_change_request(context, request, action_id="a1")

    assert result.status == "applied"
    assert result.target_kind == "field"
    assert result.recommended_resume_node == "skeleton"
    assert context["title"] == "Новый проект"
    assert context["theory_parts"] == []


def test_scoped_revision_rejects_when_llm_drops_protected_marker() -> None:
    context = {
        "markdown": """## Глава 2. Теоретический блок

Текст.

```mermaid
flowchart TD
    A --> B
```
"""
    }
    executor = ScopedRevisionExecutor(FakeLLM("## Глава 2. Теоретический блок\n\nТекст без маркера."))
    request = MethodologistChangeRequest(
        target_stage="theory",
        target_selector="Глава 2",
        scope="local_section_only",
        instruction="Уточни текст.",
    )

    result = executor.apply_change_request(context, request, action_id="a1")

    assert result.status == "rejected"
    assert "protected block marker" in result.issues[0]
    assert "```mermaid" in context["markdown"]


def test_scoped_revision_allows_explicit_mermaid_repair_and_sanitizes_style() -> None:
    markdown = """# Проект

## Глава 2. Теоретический блок

Текст.

```mermaid
%%{init: {"theme":"dark"}}%%
flowchart TD
    A[Клиент] --> B[Сервер]
    classDef dark fill:#0f1419,color:#0f1419
    class A,B dark
```
"""
    llm = FakeLLM(
        """## Глава 2. Теоретический блок

Текст.

```mermaid
%%{init: {"theme":"dark"}}%%
flowchart TD
    A[Клиент] --> B[Сервер]
    classDef dark fill:#0f1419,color:#0f1419
    class A,B dark
```
"""
    )
    context = {"markdown": markdown}
    executor = ScopedRevisionExecutor(llm)
    request = MethodologistChangeRequest(
        target_stage="theory",
        target_selector="Глава 2",
        scope="local_section_only",
        instruction="Поправь диаграмму: убери черные блоки.",
    )

    result = executor.apply_change_request(context, request, action_id="a1")

    assert result.status == "applied"
    assert "```mermaid\nflowchart TD" in context["markdown"]
    assert "%%{init:" not in context["markdown"]
    assert "classDef" not in context["markdown"]
    assert "#0f1419" not in context["markdown"]
    assert "Mermaid-диаграммы" in llm.prompts[0]["user"]


def test_scoped_revision_allows_explicit_markdown_table_repair() -> None:
    markdown = """# Проект

## Глава 2. Теоретический блок

| Поле | Значение |
| --- | --- |
| Ошибка | Старое значение |
"""
    llm = FakeLLM(
        """## Глава 2. Теоретический блок

| Поле | Значение |
| --- | --- |
| Ошибка | Новое значение |
"""
    )
    context = {"markdown": markdown}
    executor = ScopedRevisionExecutor(llm)
    request = MethodologistChangeRequest(
        target_stage="theory",
        target_selector="Глава 2",
        scope="local_section_only",
        instruction="Поправь таблицу: обнови значение в строке ошибки.",
    )

    result = executor.apply_change_request(context, request, action_id="a1")

    assert result.status == "applied"
    assert "| Ошибка | Новое значение |" in context["markdown"]
    assert "| Поле | Значение |" in context["markdown"]


def test_scoped_revision_edits_only_selected_material_file() -> None:
    context = {
        "dataset_files": [
            {"path": "materials/task_01_source_notes.md", "data": b"Raw note"},
            {"path": "materials/task_02_source_notes.md", "data": b"Other note"},
        ]
    }
    executor = ScopedRevisionExecutor(FakeLLM("Raw note updated"))
    request = MethodologistChangeRequest(
        target_stage="dataset",
        target_selector="materials/task_01_source_notes.md",
        scope="materials_only",
        instruction="Уточни сырые заметки без готовых выводов.",
    )

    result = executor.apply_change_request(context, request, action_id="a1")

    assert result.status == "applied"
    assert context["dataset_files"][0]["data"] == b"Raw note updated"
    assert context["dataset_files"][1]["data"] == b"Other note"
    assert result.recommended_resume_node == "global_quality"
    assert result.target_id == "material.materials_task_01_source_notes_md"
    assert result.diff_preview


def test_pending_change_requests_are_applied_once_and_rejections_raise() -> None:
    request = MethodologistChangeRequest(
        target_stage="dataset",
        target_selector="materials/task_01_source_notes.md",
        scope="materials_only",
        instruction="Добавь готовые ответы в материалы.",
    )
    context = {
        "methodology_review_actions": [
            {
                "action": "changes_requested",
                "timestamp": "2026-04-27T00:00:00",
                "details": {"change_request": request.model_dump(mode="json")},
            }
        ]
    }
    executor = ScopedRevisionExecutor(FakeLLM())

    with pytest.raises(ContentGenerationError):
        executor.apply_pending_change_requests(context)

    assert context["methodology_revision_results"][0]["status"] == "rejected"
    assert len(context["processed_methodology_change_ids"]) == 1


def test_resume_plan_moves_back_to_invalidated_stage() -> None:
    executor = ScopedRevisionExecutor(FakeLLM())
    result = executor.apply_change_request(
        {"markdown": "## Глава 2. Теоретический блок\n\nСтарый текст."},
        MethodologistChangeRequest(
            target_stage="theory",
            target_selector="Глава 2",
            scope="local_section_only",
            instruction="Уточни теорию.",
        ),
        action_id="a1",
    )

    start_index = executor.build_resume_plan(
        5,
        ["context", "task_planning", "skeleton", "theory", "practice", "global_quality"],
        [result],
    ).resume_from_index

    assert start_index == 4


def test_resume_plan_ignores_unchanged_revision_and_trims_stale_steps() -> None:
    executor = ScopedRevisionExecutor(FakeLLM())
    execution_plan = [
        "context",
        "task_planning",
        "title_annotation",
        "skeleton",
        "theory",
        "practice",
        "global_quality",
        "evaluation",
        "finalize",
    ]
    unchanged = ScopedRevisionResult(
        action_id="unchanged",
        status="applied",
        target_kind="markdown_section",
        target_stage="theory",
        scope="local_section_only",
        changed=False,
        recommended_resume_node="practice",
    )
    changed = unchanged.model_copy(
        update={
            "action_id": "changed",
            "changed": True,
        }
    )

    unchanged_plan = executor.build_resume_plan(8, execution_plan, [unchanged])
    moved_plan = executor.build_resume_plan(8, execution_plan, [unchanged, changed])
    previous_steps = [
        FlowExecutionStep(node_id=node_id, node_name=node_id, status="success", duration_ms=1.0)
        for node_id in execution_plan[:8]
    ]
    trimmed_steps = executor.trim_previous_steps_for_resume(
        previous_steps,
        moved_plan.resume_from_index,
        execution_plan,
    )

    assert unchanged_plan.resume_from_index == 8
    assert unchanged_plan.ignored_action_ids == ["unchanged"]
    assert moved_plan.resume_from_index == 5
    assert moved_plan.invalidated_nodes == ["practice", "global_quality", "evaluation"]
    assert [step.node_id for step in trimmed_steps] == [
        "context",
        "task_planning",
        "title_annotation",
        "skeleton",
        "theory",
    ]


def test_approved_preview_results_drive_resume_without_reapplying_old_changes() -> None:
    request = MethodologistChangeRequest(
        target_stage="practice",
        target_selector="Глава 3",
        scope="local_section_only",
        instruction="Уточни практическую задачу.",
    )
    context = {
        "markdown": "## Глава 3. Практический блок\n\nСтарый текст.",
        "methodology_review_actions": [
            {
                "action": "changes_requested",
                "timestamp": "2026-04-27T00:00:00",
                "details": {"change_request": request.model_dump(mode="json")},
            }
        ],
    }
    executor = ScopedRevisionExecutor(FakeLLM())

    first_results = executor.apply_pending_change_requests(context)
    second_results = executor.apply_pending_change_requests(context)

    assert len(first_results) == 1
    assert second_results == []
    action_id = first_results[0].action_id
    context["methodology_review_actions"].append(
        {
            "action": "diff_approved",
            "details": {"approved_action_ids": [action_id]},
        }
    )
    context["methodology_review_actions"].append({"action": "approved"})

    accepted_results = executor.approved_preview_results_for_resume(context)
    start_index = executor.build_resume_plan(
        5,
        ["context", "skeleton", "theory", "practice", "global_quality"],
        accepted_results,
    ).resume_from_index

    assert [item.action_id for item in accepted_results] == [action_id]
    assert start_index == 4

    context["methodology_review_actions"].append({"action": "approved"})
    assert executor.approved_preview_results_for_resume(context) == []


def test_approved_preview_results_are_cumulative_inside_one_review_cycle() -> None:
    first = MethodologistChangeRequest(
        target_stage="theory",
        target_selector="Глава 2",
        scope="local_section_only",
        instruction="Уточни теорию.",
    )
    second = MethodologistChangeRequest(
        target_stage="final",
        target_selector="README",
        scope="local_section_only",
        instruction="Сократи финальный повтор.",
    )
    first_action = {
        "action": "changes_requested",
        "timestamp": "2026-04-27T00:00:00",
        "details": {"change_request": first.model_dump(mode="json")},
    }
    second_action = {
        "action": "changes_requested",
        "timestamp": "2026-04-27T00:03:00",
        "details": {"change_request": second.model_dump(mode="json")},
    }
    first_id = ScopedRevisionExecutor.action_id_for_action(first_action, 0, first)
    second_id = ScopedRevisionExecutor.action_id_for_action(second_action, 2, second)
    context = {
        "methodology_review_actions": [
            first_action,
            {"action": "diff_approved", "details": {"approved_action_ids": [first_id]}},
            second_action,
            {"action": "diff_approved", "details": {"approved_action_ids": [second_id]}},
            {"action": "approved"},
        ],
        "methodology_revision_results": [
            {
                "action_id": first_id,
                "status": "applied",
                "target_kind": "markdown_section",
                "target_stage": "theory",
                "scope": "local_section_only",
                "changed": True,
                "recommended_resume_node": "practice",
            },
            {
                "action_id": second_id,
                "status": "applied",
                "target_kind": "markdown_section",
                "target_stage": "final",
                "scope": "local_section_only",
                "changed": True,
                "recommended_resume_node": "evaluation",
            },
        ],
    }
    executor = ScopedRevisionExecutor(FakeLLM())

    accepted_results = executor.approved_preview_results_for_resume(context)
    start_index = executor.build_resume_plan(
        8,
        ["context", "task_planning", "title_annotation", "skeleton", "theory", "practice", "global_quality", "evaluation"],
        accepted_results,
    ).resume_from_index

    assert {item.action_id for item in accepted_results} == {first_id, second_id}
    assert start_index == 5
