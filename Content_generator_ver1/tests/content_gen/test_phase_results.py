from content_gen.models.phase_results import (
    ContextPhaseResult,
    EvaluationPhaseResult,
    PracticePhaseResult,
    QualityPhaseResult,
    SkeletonPhaseResult,
    StructurePhaseResult,
    TheoryPhaseResult,
    TitleAnnotationPhaseResult,
    TranslationPhaseResult,
)
from content_gen.models.readme_document import ReadmeDocument


def test_context_and_structure_phase_results_expose_typed_fields() -> None:
    context_result = ContextPhaseResult(
        seed="seed",
        context_meta="meta",
        context_analysis="analysis",
        context_bundle="bundle",
        similar_projects=["prev"],
        warnings=["warn"],
    )
    title_result = TitleAnnotationPhaseResult(title="Title", annotation={"text": "Annotation"})
    structure_result = StructurePhaseResult(
        markdown="# Title",
        preflight_result={"passed": True},
        intro_section={"intro": True},
        blueprint={"blueprint": True},
    )
    skeleton_result = SkeletonPhaseResult(
        markdown="# Title",
        preflight_result={"passed": True},
        intro_section={"intro": True},
        blueprint={"blueprint": True},
        title="Title",
        annotation={"text": "Annotation"},
    )

    assert context_result.seed == "seed"
    assert context_result.similar_projects == ["prev"]
    assert title_result.title == "Title"
    assert structure_result.markdown == "# Title"
    assert skeleton_result.annotation == {"text": "Annotation"}


def test_theory_phase_result_exposes_typed_fields() -> None:
    document = ReadmeDocument.from_markdown("# README\n\n## Глава 2. Теория\n\nText.")
    result = TheoryPhaseResult(
        markdown=document.to_markdown(),
        readme_document=document,
        theory_parts=[],
        issues=[],
        warnings=["warn"],
    )

    assert result.markdown == document.to_markdown()
    assert result.readme_document is document
    assert result.theory_parts == []
    assert result.issues == []
    assert result.warnings == ["warn"]


def test_practice_phase_result_exposes_typed_fields() -> None:
    document = ReadmeDocument.from_markdown("# README\n\n## Глава 3. Практика\n\nTask.")
    result = PracticePhaseResult(
        markdown=document.to_markdown(),
        readme_document=document,
        practice_tasks=[],
        issues=["issue"],
        warnings=[],
        artifact_chain_plan={"chain": True},
        evidence_specs=["evidence"],
        dataset_files=[{"path": "data.csv"}],
        practice_critic_issues=[{"message": "critic"}],
    )

    assert result.markdown == document.to_markdown()
    assert result.readme_document is document
    assert result.practice_tasks == []
    assert result.issues == ["issue"]
    assert result.warnings == []
    assert result.artifact_chain_plan == {"chain": True}
    assert result.evidence_specs == ["evidence"]
    assert result.dataset_files == [{"path": "data.csv"}]
    assert result.practice_critic_issues == [{"message": "critic"}]


def test_quality_phase_result_carries_markdown_and_document() -> None:
    document = ReadmeDocument.from_markdown("# README\n\n## Заключение\n\nDone.")
    result = QualityPhaseResult(markdown=document.to_markdown(), readme_document=document)

    assert result.markdown == document.to_markdown()
    assert result.readme_document.section_by_title_fragment("Заключение").body == "Done."


def test_evaluation_phase_result_exposes_typed_fields() -> None:
    document = ReadmeDocument.from_markdown("# README\n\nBody.")
    result = EvaluationPhaseResult(
        rubric_json={"score": 1},
        issues=["issue"],
        readme_document=document,
    )

    assert result.rubric_json == {"score": 1}
    assert result.issues == ["issue"]
    assert result.readme_document is document


def test_translation_phase_result_exposes_typed_fields() -> None:
    document = ReadmeDocument.from_markdown("# README\n\nBody.")
    translated_document = ReadmeDocument.from_markdown("# README EN\n\nBody.")
    result = TranslationPhaseResult(
        markdown="# README\n\nBody.",
        translated_markdown="# README EN\n\nBody.",
        readme_document=document,
        translated_readme_document=translated_document,
    )

    assert result.markdown == "# README\n\nBody."
    assert result.translated_markdown == "# README EN\n\nBody."
    assert result.readme_document is document
    assert result.translated_readme_document is translated_document
