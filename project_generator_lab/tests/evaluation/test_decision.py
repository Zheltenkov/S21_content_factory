"""Causal grouping and decision invariants, independent of the supplied projects or model calls."""
import json
import random

import pytest
from project_generator_lab.evaluation import engine
from project_generator_lab.evaluation.models import Evidence, FileRecord, Snapshot, Unit
from project_generator_lab.evaluation.policy import presentation, summary
from project_generator_lab.evaluation.repository import Identity


def report_for(paths=()):
    files=[FileRecord(path=path,present=False,required=True,reference_units=["task:1:steps"]) for path in paths]
    text="\n".join(f"Запусти python {path}." for path in paths)+"\nНезависимое правило требует проверить точность округления."
    units=[Unit(id="file:README.md",title="Инструкция",kind="readme",text="Исходные требования полностью заданы в тестовом сценарии."),
           Unit(id="task:1:steps",title="Условия",kind="tasks",text=text),
           *[Unit(id=f"inventory:{f.path}",title=f.path,kind="inventory",text=f.model_dump_json()) for f in files]]
    snapshot=Snapshot(digest="synthetic",content_digest="synthetic",title="Независимый сценарий",units=units,files=files,
        outcomes=[],task_numbers=[1],optional_tasks=[],format="individual",has_story=False,has_theory=False,curriculum_available=True)
    report=engine.initial(Identity("00000000-0000-4000-8000-000000000003","test"),snapshot,"fake",True)
    for verdict in report.verdicts:
        verdict.state,verdict.mark,verdict.method,verdict.confirmed_blocker="assessed",1,"model",False
        verdict.reason="Проверено в синтетическом сценарии."
        verdict.evidence=[Evidence(unit=units[0].id,quote=units[0].text)]
    report.state="complete"
    return report


def absent(report,number,path,*,critical=False):
    verdict=report.verdicts[number-1]
    verdict.mark,verdict.confirmed_blocker=-1,critical
    verdict.evidence=[Evidence(unit=f"inventory:{path}",quote=next(u.text for u in report.snapshot.units if u.id==f"inventory:{path}")),
                      Evidence(unit="task:1:steps",quote=f"Запусти python {path}.")]
    verdict.reason="Обязательная команда ссылается на отсутствующий инструмент."


def test_six_effects_of_one_missing_dependency_are_one_finding_and_one_decision():
    report=report_for(["assets/tools/check_records.py"])
    absent(report,31,"assets/tools/check_records.py",critical=True)
    before=summary(report)
    for n in [9,12,16,32,34]:
        absent(report,n,"assets/tools/check_records.py")
    original=report.model_dump_json()
    after=summary(report)
    assert before["finding_count"]==after["finding_count"]==1
    assert before["recommendation"]==after["recommendation"]=="rework"
    assert after["findings"][0]["criteria"]==[9,12,16,31,32,34]
    assert after["problems"]==6 and after["score"] is None
    assert report.model_dump_json()==original


def test_two_missing_files_even_in_one_paragraph_remain_two_causes():
    report=report_for(["tools/audit.py","scripts/audit.py"])
    absent(report,12,"tools/audit.py")
    absent(report,16,"scripts/audit.py")
    assert summary(report)["finding_count"]==2


def test_a_different_failure_in_same_task_is_not_merged_with_the_missing_file():
    report=report_for(["tools/check.py"])
    absent(report,12,"tools/check.py")
    other=report.verdicts[15]
    other.mark=-1
    other.evidence=[Evidence(unit="task:1:steps",quote="Независимое правило требует проверить точность округления.")]
    assert summary(report)["finding_count"]==2


def test_mixed_code_findings_do_not_hide_an_independent_broken_local_link():
    report=report_for(["tools/check.py"])
    absent(report,31,"tools/check.py",critical=True)
    absent(report,32,"tools/check.py")
    verdict=report.verdicts[31]
    verdict.method="code"
    line="Открой [пример](file:///work/source.csv)."
    report.snapshot.units[1].text+="\n"+line
    verdict.evidence.append(Evidence(unit="task:1:steps",quote=line))
    result=summary(report)
    assert result["finding_count"]==2
    assert any(f["basis"]=="criterion" and f["criteria"]==[32] for f in result["findings"])


def test_same_quote_or_same_filename_is_not_sufficient_to_claim_a_common_cause():
    report=report_for()
    for n in [12,16]:
        report.verdicts[n-1].mark=-1
    assert summary(report)["finding_count"]==2
    assert summary(report)["recommendation"]=="targeted_revision"


@pytest.mark.parametrize("state",["no_data","unverified","error","pending"])
def test_removing_a_failed_assessment_cannot_create_a_ready_project_or_a_higher_score(state):
    report=report_for()
    report.verdicts[11].mark=-1
    assert summary(report)["recommendation"]=="targeted_revision"
    report.verdicts[11].state,report.verdicts[11].mark=state,None
    result=summary(report)
    assert result["recommendation"]=="preliminary" and result["incomplete"]
    assert result["score"] is None and result["assessed"]==39


def test_invalid_citation_is_a_visible_review_question_and_never_a_defect():
    report=report_for()
    verdict=report.verdicts[27]
    verdict.mark,verdict.evidence_valid=-1,False
    result=summary(report)
    assert result["finding_count"]==0 and len(result["review_queue"])==1
    assert result["recommendation"]=="preliminary"


def test_assessed_record_without_a_mark_is_unknown_in_api_and_coverage():
    report=report_for()
    report.verdicts[15].mark=None
    assert presentation(report).verdicts[15].state=="unverified"
    assert summary(report)["unverified"]==1


def test_full_positive_assessment_is_ready_but_does_not_invent_a_calibrated_score():
    result=summary(report_for())
    assert result["recommendation"]=="ready_recommended" and result["fulfilled"]==40
    assert result["coverage_percent"]==100 and result["score"] is None


def test_unknown_execution_is_not_presented_as_a_partially_broken_project():
    report=report_for()
    verdict=report.verdicts[28]
    verdict.mark,verdict.method=0,"model"
    result=summary(report)
    assert result["partial"]==0 and result["finding_count"]==0
    assert result["recommendation"]=="preliminary"


def test_code_preflight_with_no_defect_is_unknown_not_a_partial_defect():
    report=report_for()
    for n in [31,32]:
        report.verdicts[n-1].mark,report.verdicts[n-1].method=0,"code"
    assert summary(report)["finding_count"]==0
    assert summary(report)["unverified"]==2


def test_optional_missing_input_is_still_a_real_partial_remark():
    report=report_for(["inputs/bonus.json"])
    file=report.snapshot.files[0]
    file.required=False
    next(u for u in report.snapshot.units if u.id=="inventory:inputs/bonus.json").text=file.model_dump_json()
    verdict=report.verdicts[30]
    verdict.mark,verdict.method=0,"code"
    verdict.evidence=[Evidence(unit="inventory:inputs/bonus.json",quote=next(u.text for u in report.snapshot.units if u.id=="inventory:inputs/bonus.json"))]
    assert summary(report)["finding_counts"]=={"partial":1}


def test_duplicate_or_missing_criterion_record_prevents_ready_and_does_not_inflate_coverage():
    report=report_for()
    report.verdicts[1]=report.verdicts[0].model_copy(deep=True)
    result=summary(report)
    assert result["assessed"]==38 and result["unverified"]==2
    assert result["recommendation"]=="preliminary"


def test_inapplicable_everywhere_is_not_a_verified_project():
    report=report_for()
    for verdict in report.verdicts[:40]:
        verdict.state,verdict.mark="not_applicable",None
    assert summary(report)["recommendation"]=="insufficient"


def test_experts_thresholds_and_order_do_not_override_confirmed_causes():
    report=report_for(["support/validate.py"])
    absent(report,12,"support/validate.py")
    absent(report,34,"support/validate.py")
    original=summary(report)
    report.profile.ready_threshold,report.profile.revision_threshold=1,0
    for verdict in report.verdicts[40:]:
        verdict.mark,verdict.confirmed_blocker=-1,True
    random.Random(17).shuffle(report.verdicts)
    assert summary(report)["findings"]==original["findings"]
    assert summary(report)["recommendation"]==original["recommendation"]
    raw=report.model_dump_json()
    assert all(v.mark is None for v in presentation(report).verdicts[40:])
    assert report.model_dump_json()==raw
    assert json.loads(raw)["schema_version"]==7
