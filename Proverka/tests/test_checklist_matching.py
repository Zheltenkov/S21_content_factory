from content_audit.checklist_matching import (
    assess_checklist_description_quality,
    checklist_name_matches_readme,
    checklist_name_match_strength,
    extract_checklist_questions,
    extract_checklist_question_names,
    match_checklist_to_readme,
)
from content_audit.text_utils import normalize_for_match


def test_extract_checklist_question_names_reads_yaml_shape() -> None:
    payload = {
        "sections": [
            {"questions": [{"name": "Part_1.CAT"}, {"name": "Part_2.GREP"}]},
            {"questions": [{"title": "without-name"}]},
        ]
    }

    assert extract_checklist_question_names(payload) == ["Part_1.CAT", "Part_2.GREP"]


def test_extract_checklist_questions_keeps_description_text() -> None:
    payload = {
        "sections": [
            {
                "questions": [
                    {
                        "name": "Part_1.CAT",
                        "description": "Must check src/cat.c and compare expected stdout with the example.",
                    }
                ]
            }
        ]
    }

    questions = extract_checklist_questions(payload)

    assert questions[0].name == "Part_1.CAT"
    assert "src/cat.c" in questions[0].description_text


def test_checklist_name_matches_readme_by_number_and_keyword() -> None:
    normalized_readme = normalize_for_match("## 1-qism. cat utilitasi bilan ishlash")

    assert checklist_name_matches_readme("Part_1.CAT", normalized_readme)
    assert checklist_name_match_strength("Part_1.CAT", normalized_readme) == "strong"


def test_checklist_name_marks_number_only_match_as_weak() -> None:
    normalized_readme = normalize_for_match("## Part 4. Log generator")

    assert checklist_name_match_strength("Part_4.File generator", normalized_readme) == "weak"


def test_match_checklist_to_readme_returns_explainable_result() -> None:
    result = match_checklist_to_readme(
        ["Part_1.CAT", "Part_2.GREP"],
        "## Part 1. Работа с cat\n\n## Part 2. Работа с grep\n",
    )

    assert result.total == 2
    assert result.matched == 2
    assert result.strong_matched == 2
    assert result.weak_matched == 0
    assert result.ratio == 1.0
    assert result.unmatched_names == ()


def test_match_checklist_to_readme_tracks_unmatched_items() -> None:
    result = match_checklist_to_readme(["Part_1.CAT", "Part_2.GREP"], "## Part 1. Работа с cat\n")

    assert result.total == 2
    assert result.matched == 1
    assert result.strong_matched == 1
    assert result.ratio == 0.5
    assert result.unmatched_names == ("Part_2.GREP",)


def test_assess_checklist_description_quality_counts_complete_items() -> None:
    questions = extract_checklist_questions(
        {
            "sections": [
                {
                    "questions": [
                        {
                            "name": "Part_1.CAT",
                            "description": "Must check src/cat.c, expected stdout and error handling. Example input is provided.",
                        },
                        {"name": "Part_2.GREP", "description": "Check grep."},
                    ]
                }
            ]
        }
    )

    result = assess_checklist_description_quality(questions)

    assert result.total == 2
    assert result.complete == 1
    assert result.ratio == 0.5
    assert result.incomplete_names == ("Part_2.GREP",)
