from types import SimpleNamespace

from content_gen.generation_runtime import GenerationRuntimeContainer
import content_gen.phase_executors as phase_executors
from content_gen.context_phase_executor import ContextPhaseExecutor
from content_gen.models.readme_document import ReadmeDocument
from content_gen.node_executor_bundle import GenerationNodeExecutorBundle
from content_gen.practice_phase_executor import PracticePhaseExecutor
from content_gen.phase_executors import (
    EvaluationPhaseExecutor,
    QualityPhaseExecutor,
    TranslationPhaseExecutor,
)
from content_gen.structure_phase_executor import StructurePhaseExecutor
from content_gen.theory_phase_executor import TheoryPhaseExecutor


class FakeLLM:
    def complete(self, *args, **kwargs):
        return ""


def test_node_executor_bundle_builds_concrete_executors() -> None:
    runtime = GenerationRuntimeContainer(FakeLLM())
    node_executors = GenerationNodeExecutorBundle.from_runtime(runtime)

    assert isinstance(node_executors.runtime, GenerationRuntimeContainer)
    assert isinstance(node_executors.context, ContextPhaseExecutor)
    assert isinstance(node_executors.structure, StructurePhaseExecutor)
    assert isinstance(node_executors.theory, TheoryPhaseExecutor)
    assert isinstance(node_executors.practice, PracticePhaseExecutor)
    assert isinstance(node_executors.quality, QualityPhaseExecutor)
    assert isinstance(node_executors.evaluation, EvaluationPhaseExecutor)
    assert isinstance(node_executors.translation, TranslationPhaseExecutor)


def test_quality_phase_executor_executes_canonical_quality_logic() -> None:
    class ContentEditor:
        def ensure_global_coherence_document(self, document, _seed):
            return document

    class Toc:
        def build_document(self, _document, language):
            assert language == "ru"
            return SimpleNamespace(toc_md="- [Заключение](#заключение)")

        def inject_document(self, document, toc_md, language):
            return document.with_upserted_section_by_title_fragment(
                "Содержание",
                f"## Содержание\n\n{toc_md}",
                fallback_level=2,
            )

    class Style:
        def lint_document(self, _document, _language):
            return []

    runtime = SimpleNamespace(
        content_editor=ContentEditor(),
        story_map_contract=SimpleNamespace(completion="Собери итоговый макет."),
        toc=Toc(),
        style=Style(),
    )
    seed = SimpleNamespace(language="ru", title_seed="Проект", project_description="")

    result = QualityPhaseExecutor(runtime).execute(seed, "# Проект\n")

    assert "## Заключение" in result.markdown
    assert "Собери итоговый макет" in result.markdown
    assert "- [Заключение](#заключение)" in result.markdown


def test_quality_phase_executor_replaces_skeleton_conclusion_placeholder() -> None:
    class ContentEditor:
        def ensure_global_coherence_document(self, document, _seed):
            return document

    class Toc:
        def build_document(self, _document, language):
            assert language == "ru"
            return SimpleNamespace(toc_md="- [Заключение](#заключение)")

        def inject_document(self, document, _toc_md, language):
            assert language == "ru"
            return document

    class Style:
        def lint_document(self, _document, _language):
            return []

    runtime = SimpleNamespace(
        content_editor=ContentEditor(),
        story_map_contract={"completion": "Собери итоговый макет."},
        toc=Toc(),
        style=Style(),
    )
    seed = SimpleNamespace(language="ru", title_seed="Проект", project_description="")
    document = ReadmeDocument.from_markdown(
        "# Проект\n\n"
        "## Заключение\n\n"
        "(финальное завершение текущего проекта без анонса следующего)\n"
    )

    result = QualityPhaseExecutor(runtime).execute(seed, document.to_markdown(), readme_document=document)

    assert "(финальное завершение текущего проекта без анонса следующего)" not in result.markdown
    assert "Собери итоговый макет" in result.markdown
    assert "p2p-ревью" in result.markdown


def test_quality_phase_executor_returns_typed_document() -> None:
    class ContentEditor:
        def ensure_global_coherence_document(self, document, _seed):
            return document

    class Toc:
        def build_document(self, _document, language):
            assert language == "ru"
            return SimpleNamespace(toc_md="- [Заключение](#заключение)")

        def inject_document(self, document, toc_md, language):
            return document.with_upserted_section_by_title_fragment(
                "Содержание",
                f"## Содержание\n\n{toc_md}",
                fallback_level=2,
            )

    class Style:
        def lint_document(self, _document, _language):
            return []

    runtime = SimpleNamespace(
        content_editor=ContentEditor(),
        story_map_contract=SimpleNamespace(completion="Собери итоговый макет."),
        toc=Toc(),
        style=Style(),
    )
    seed = SimpleNamespace(language="ru", title_seed="Проект", project_description="")
    document = ReadmeDocument.from_markdown("# Проект\n\nАннотация.")

    result = QualityPhaseExecutor(runtime).execute(seed, document.to_markdown(), readme_document=document)

    assert result.readme_document.title == "Проект"
    assert result.readme_document.section_by_title_fragment("Заключение") is not None
    assert "Собери итоговый макет" in result.markdown


def test_quality_phase_executor_restores_conclusion_after_style_rewrite() -> None:
    class ContentEditor:
        def ensure_global_coherence_document(self, document, _seed):
            return document

    class Toc:
        def build_document(self, _document, language):
            assert language == "ru"
            return SimpleNamespace(toc_md="- [Заключение](#заключение)")

        def inject_document(self, document, toc_md, language):
            return document.with_upserted_section_by_title_fragment(
                "Содержание",
                f"## Содержание\n\n{toc_md}",
                fallback_level=2,
            )

    class Style:
        def lint_document(self, _document, _language):
            return ["style issue"]

        def rewrite_document(self, _document, _language):
            return ReadmeDocument.from_markdown("# Проект\n\n## Глава 1\n\nТекст.")

    runtime = SimpleNamespace(
        content_editor=ContentEditor(),
        story_map_contract=SimpleNamespace(completion="Собери финальный артефакт."),
        toc=Toc(),
        style=Style(),
    )
    seed = SimpleNamespace(language="ru", title_seed="Проект", project_description="")

    result = QualityPhaseExecutor(runtime).execute(seed, "# Проект\n")

    assert "## Заключение" in result.markdown
    assert "Собери финальный артефакт" in result.markdown


def test_quality_phase_executor_prefers_typed_quality_contracts() -> None:
    calls = []

    class ContentEditor:
        def ensure_global_coherence_document(self, document, _seed):
            calls.append("editor_document")
            return document

        def ensure_global_coherence(self, *_args):
            raise AssertionError("legacy editor path should not be used")

    class Toc:
        def build_document(self, document, language):
            calls.append(("toc_build_document", document.title, language))
            return SimpleNamespace(toc_md="- [Глава 1. Введение](#глава-1-введение)")

        def inject_document(self, document, toc_md, language):
            calls.append(("toc_inject_document", language))
            return document.with_upserted_section_by_title_fragment(
                "Содержание",
                f"## Содержание\n\n{toc_md}",
                fallback_level=2,
            )

        def build(self, *_args, **_kwargs):
            raise AssertionError("legacy TOC path should not be used")

        def inject(self, *_args, **_kwargs):
            raise AssertionError("legacy TOC path should not be used")

    class Style:
        def lint_document(self, document, language):
            calls.append(("style_lint_document", document.title, language))
            return []

        def lint(self, *_args):
            raise AssertionError("legacy style lint path should not be used")

    runtime = SimpleNamespace(
        content_editor=ContentEditor(),
        story_map_contract=SimpleNamespace(completion="Собери итоговый макет."),
        toc=Toc(),
        style=Style(),
    )
    seed = SimpleNamespace(language="ru", title_seed="Проект", project_description="")
    document = ReadmeDocument.from_markdown("# Проект\n\n## Глава 1. Введение\n\nТекст.")

    result = QualityPhaseExecutor(runtime).execute(seed, document.to_markdown(), readme_document=document)

    assert "editor_document" in calls
    assert ("toc_build_document", "Проект", "ru") in calls
    assert ("style_lint_document", "Проект", "ru") in calls
    assert result.readme_document.section_by_title_fragment("Содержание") is not None


def test_translation_phase_executor_executes_canonical_translation_logic() -> None:
    class Translator:
        def translate(self, markdown, target_language, _seed):
            return f"{target_language}:{markdown}"

    runtime = SimpleNamespace(translator=Translator())
    seed = SimpleNamespace(language="ru")

    translated = TranslationPhaseExecutor(runtime).execute(seed, "# md", "en")
    untranslated = TranslationPhaseExecutor(runtime).execute(seed, "# md", "ru")

    assert translated.markdown == "# md"
    assert translated.translated_markdown == "en:# md"
    assert untranslated.markdown == "# md"
    assert untranslated.translated_markdown == "# md"


def test_translation_phase_executor_returns_typed_document() -> None:
    class Translator:
        def translate(self, markdown, target_language, _seed):
            return f"# {target_language.upper()}\n\n{markdown}"

    runtime = SimpleNamespace(translator=Translator())
    seed = SimpleNamespace(language="ru")
    document = ReadmeDocument.from_markdown("# README\n\nBody.")

    result = TranslationPhaseExecutor(runtime).execute(seed, document.to_markdown(), "en", readme_document=document)

    assert result.readme_document is document
    assert result.translated_readme_document.title == "EN"
    assert result.markdown == document.to_markdown()
    assert result.translated_markdown.startswith("# EN")


def test_evaluation_phase_executor_executes_canonical_evaluation_logic(monkeypatch) -> None:
    class Validator:
        def __init__(self, message):
            self.message = message

        def validate_document(self, *_args, **_kwargs):
            return [SimpleNamespace(message=self.message, severity="soft")]

    class Rubric:
        def __init__(self, language, llm_client):
            self.language = language
            self.llm_client = llm_client

        def score_document(self, readme_document, learning_outcomes):
            return {"title": readme_document.title, "learning_outcomes": learning_outcomes}

    monkeypatch.setattr(phase_executors, "RubricScorer", Rubric)
    monkeypatch.setattr(phase_executors, "criteria_to_json", lambda report: {"report": report})

    runtime = SimpleNamespace(
        intro_validator=Validator("intro"),
        theory_validator=Validator("theory"),
        practice_validator=Validator("practice"),
        llm=FakeLLM(),
        rubric=None,
    )
    seed = SimpleNamespace(language="ru", tasks_count=1, learning_outcomes=["LO"])

    result = EvaluationPhaseExecutor(runtime).execute(seed, "# md")

    assert result.rubric_json == {"report": {"title": "md", "learning_outcomes": ["LO"]}}
    assert [issue["message"] for issue in result.issues] == ["intro", "theory", "practice"]
    assert isinstance(runtime.rubric, Rubric)


def test_evaluation_phase_executor_returns_typed_document(monkeypatch) -> None:
    class Validator:
        def validate_document(self, *_args, **_kwargs):
            return []

    class Rubric:
        def __init__(self, language, llm_client):
            self.language = language
            self.llm_client = llm_client

        def score_document(self, readme_document, learning_outcomes):
            return {"title": readme_document.title, "learning_outcomes": learning_outcomes}

    monkeypatch.setattr(phase_executors, "RubricScorer", Rubric)
    monkeypatch.setattr(phase_executors, "criteria_to_json", lambda report: {"report": report})

    runtime = SimpleNamespace(
        intro_validator=Validator(),
        theory_validator=Validator(),
        practice_validator=Validator(),
        llm=FakeLLM(),
        rubric=None,
    )
    seed = SimpleNamespace(language="ru", tasks_count=1, learning_outcomes=["LO"])
    document = ReadmeDocument.from_markdown("# README\n\nBody.")

    result = EvaluationPhaseExecutor(runtime).execute(seed, document.to_markdown(), readme_document=document)

    assert result.readme_document is document
    assert result.rubric_json["report"]["title"] == "README"
    assert result.rubric_json["report"]["learning_outcomes"] == ["LO"]
    assert result.issues == []


def test_practice_executor_selects_serious_critic_issues_only() -> None:
    issues = [
        SimpleNamespace(kind="p2p_check", severity="critical"),
        SimpleNamespace(kind="style", severity="critical"),
        SimpleNamespace(kind="theory_alignment", severity="minor"),
        SimpleNamespace(kind="story_alignment", severity="hard"),
    ]

    selected = PracticePhaseExecutor._critic_issues_for_regeneration(issues)

    assert selected == [issues[0], issues[3]]


def test_practice_executor_extracts_instruction_and_theory_summary() -> None:
    class Intro:
        def _split_intro_instruction(self, markdown):
            assert markdown == "# README"
            return "intro", "instruction"

    runtime = SimpleNamespace(
        intro=Intro(),
        theory_parts=[SimpleNamespace(title="Риск", body="**Риск** — это событие, влияющее на срок проекта.")],
    )
    seed = SimpleNamespace(language="ru")

    instruction, theory_summary = PracticePhaseExecutor(runtime).extract_instruction_and_theory_summary("# README", seed)

    assert instruction == "instruction"
    assert "Риск" in theory_summary


def test_practice_executor_appends_practice_as_typed_document_when_chapter_missing() -> None:
    task = SimpleNamespace(
        title="Собрать артефакт",
        situation="Есть задача",
        input_data="Бриф",
        goal="Собрать таблицу",
        constraints_or_risk="Не добавлять лишних данных",
        group_roles=[],
        expected_artifact="Таблица",
        artifact_location="materials/task.csv",
        p2p_criteria=["Файл открыт"],
        approach_bullets=["Заполни строки"],
    )
    document = ReadmeDocument.from_markdown("# README\n\nАннотация.")
    seed = SimpleNamespace(language="ru")

    updated, changed = PracticePhaseExecutor.render_practice_document(document, [task], [task], True, seed)
    markdown = updated.to_markdown()

    assert changed is True
    assert "## Глава 3. Практический блок" in markdown
    assert "### Задание 1. Собрать артефакт" in markdown
    assert "### Бонусное задание 1*" in markdown


def test_practice_executor_renders_practice_as_typed_document() -> None:
    task = SimpleNamespace(
        title="Собрать артефакт",
        situation="Есть задача",
        input_data="Бриф",
        goal="Собрать таблицу",
        constraints_or_risk="Не добавлять лишних данных",
        group_roles=[],
        expected_artifact="Таблица",
        artifact_location="materials/task.csv",
        p2p_criteria=["Файл открыт"],
        approach_bullets=["Заполни строки"],
    )
    document = ReadmeDocument.from_markdown(
        "# README\n\n"
        "## Глава 3. Практический блок\n\n"
        "Черновик\n\n"
        "## Бонус\n\n"
        "Черновик"
    )
    seed = SimpleNamespace(language="ru")

    updated, changed = PracticePhaseExecutor.render_practice_document(document, [task], [task], True, seed)

    assert changed is True
    assert updated.section_by_title_fragment("Задание 1").title == "Задание 1. Собрать артефакт"
    assert updated.section_by_title_fragment("Бонусное задание").title == "Бонусное задание 1*. Собрать артефакт"
