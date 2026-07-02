import csv
import json

from content_factory.audit.adjudication import (
    build_adjudication_sample,
    infer_corpus_totals,
    score_adjudication_sheet,
    split_issue_parts,
)


def test_split_issue_parts_uses_last_segment_as_subtype() -> None:
    quote, defect, fix, subtype = split_issue_parts("quote with | pipe | defect | fix | wording")

    assert quote == "quote with"
    assert defect == "pipe"
    assert fix == "defect | fix"
    assert subtype == "wording"


def test_build_adjudication_sample_stratifies_by_subtype(workspace_tmp_path) -> None:
    fp_file = workspace_tmp_path / "false_positive_detailed_all.csv"
    _write_fp_file(
        fp_file,
        [
            ("f1", "spelling_wording_checker", "Quote 1 | Defect | Fix | wording"),
            ("f2", "spelling_wording_checker", "Quote 2 | Defect | Fix | wording"),
            ("f3", "spelling_wording_checker", "Quote 3 | Defect | Fix | typo"),
            ("f4", "other_checker", "Quote 4 | Defect | Fix | typo"),
        ],
    )

    sample = build_adjudication_sample(fp_file, checker="spelling_wording_checker", margin=0.2, seed=7)

    assert len(sample.candidates) == 3
    assert {item.subtype for item in sample.candidates} == {"wording", "typo"}
    assert [(item.subtype, item.population, item.sample_size, item.mode) for item in sample.plan] == [
        ("wording", 2, 2, "census"),
        ("typo", 1, 1, "census"),
    ]


def test_score_adjudication_sheet_recalculates_corpus_precision(workspace_tmp_path) -> None:
    fp_file = workspace_tmp_path / "false_positive_detailed_all.csv"
    _write_fp_file(
        fp_file,
        [
            ("f1", "spelling_wording_checker", "Quote 1 | Defect | Fix | wording"),
            ("f2", "spelling_wording_checker", "Quote 2 | Defect | Fix | wording"),
            ("f3", "spelling_wording_checker", "Quote 3 | Defect | Fix | typo"),
            ("f4", "other_checker", "Quote 4 | Defect | Fix | other"),
        ],
    )
    (workspace_tmp_path / "aggregate_summary.json").write_text(
        json.dumps({"true_positive": 4, "false_positive": 10, "predicted_total": 14}),
        encoding="utf-8",
    )
    sheet = workspace_tmp_path / "adjudication_sheet.csv"
    with sheet.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["finding_id", "subtype_hidden", "verdict[real|style|wrong]"])
        writer.writerow(["f1", "wording", "real"])
        writer.writerow(["f2", "wording", "style"])
        writer.writerow(["f3", "typo", "real"])

    score = score_adjudication_sheet(
        sheet,
        fp_file,
        checker="spelling_wording_checker",
        corpus_totals=infer_corpus_totals(fp_file),
    )

    assert score.population_total == 3
    assert score.adjudicated_total == 3
    assert score.estimated_true_false_positive == 1.0
    assert score.corrected_false_positive == 8.0
    assert round(score.original_micro_precision, 4) == round(4 / 14, 4)
    assert round(score.corrected_micro_precision, 4) == round(4 / 12, 4)
    assert not score.warnings


def _write_fp_file(path, rows) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "проект",
                "критерий_код",
                "найденная строка/диапазон",
                "найденная ошибка",
                "finding_id",
                "checker",
            ],
        )
        writer.writeheader()
        for finding_id, checker, issue in rows:
            writer.writerow(
                {
                    "проект": "project",
                    "критерий_код": "readability",
                    "найденная строка/диапазон": "README.md:10",
                    "найденная ошибка": issue,
                    "finding_id": finding_id,
                    "checker": checker,
                }
            )
