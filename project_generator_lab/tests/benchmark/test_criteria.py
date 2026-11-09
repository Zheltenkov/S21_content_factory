"""The file of criteria: what it holds, which of it the judge is asked, and what is said of a file that reads wrong."""

import hashlib

import pytest
import yaml
from project_generator_lab.benchmark import criteria

# The criteria as they stood when the version was given. A sign, a scope or a routing field changed without the
# version raised makes two reports that cannot be compared look comparable: raise `version` in criteria.yaml, then
# put the new pair here.
PINNED = ("0.2", "1d09fd9c343df26e")


def fingerprint(found: criteria.Criteria) -> str:
    lines = [f"{c.id}|{c.source}|{c.section}|{c.text}|{c.by}|{','.join(c.needs)}|{c.group}|{','.join(c.stages)}|{c.scope}|{c.note}|{c.evidence}|{c.uncounted}"
             for c in found.criteria]
    lines += [f"{name}|{group.title}|{','.join(group.shows)}" for name, group in found.groups.items()]
    lines += [f"{name}|{source.title}|{source.short}|{source.formula}|{sorted(source.scale.items())}" for name, source in found.sources.items()]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]


def test_the_file_holds_the_three_sources_whole():
    found = criteria.load()
    by_source = {name: [c.id for c in found.criteria if c.source == name] for name in found.sources}
    # Forty signs of the diagnostic check-list in their own numbers, the two rubrics of the passport row for row.
    assert by_source == {"rules": [f"R{n:02d}" for n in range(1, 41)], "passport_project": [f"P{n:02d}" for n in range(1, 28)],
                         "passport_checklist": [f"C{n:02d}" for n in range(1, 14)]}
    assert set(found.scale) == {"0", "1", "2", "na", "no_data"} and list(found.groups) == ["logic", "tasks", "values", "checking", "package"]
    signs = {c.id: c for c in found.criteria}
    assert signs["R13"].text == "Сторителлинг и хуки помогают работе, а не маскируют разрывы логики." and "сюжет необязателен" in signs["R13"].note
    assert signs["R39"].scope.startswith("Оценивается только вторая часть") and signs["R21"].by == "run" and signs["R20"].by == "expert"
    assert [source.short for source in found.sources.values()] == ["Правила (R)", "Паспорт, проект (P)", "Паспорт, чек-лист (C)"]
    # Each source marks in its own words; only the rules give a formula, and the passport is counted by the same one.
    assert found.sources["rules"].formula == "сумма баллов / (2 × число применимых пунктов) × 100"
    # The sheets of the passport divide by a number typed in — 32 and 24 — which is not twice the number of their rows: the result is counted as by the rules.
    assert found.sources["passport_project"].formula.startswith("в источнике: сумма баллов / 32 × 100 — знаменатель записан числом и не равен 2 × 27 строк; здесь считается как у правил")
    assert found.sources["passport_checklist"].formula.startswith("в источнике: сумма баллов / 24 × 100 — знаменатель записан числом и не равен 2 × 13 строк; здесь считается как у правил")
    assert {c.section for c in found.criteria if c.id in ("R09", "R13")} == {"В. Структура связность и сложность"}  # as the source has it, without a comma
    assert found.sources["rules"].scale == {"0": "признак не выполнен или обнаружено существенное противоречие", "1": "выполнено частично, требуется локальная доработка",
                                            "2": "выполнено в достаточной степени для текущей версии"}
    assert found.sources["passport_project"].scale == found.sources["passport_checklist"].scale == {
        "0": "совсем не соответствует критерию", "1": "соответствует критерию, но нужно доработать", "2": "идеально соответствует критерию"}
    # The signs that say something is not there: a mark that says it is there has to show it.
    assert [c.id for c in found.criteria if c.evidence == "absence"] == [
        "R04", "R06", "R18", "R23", "R25", "R26", "R27", "R28", "R29", "R30", "R31", "R32", "R35", "R36", "R37", "R38", "R39", "P15", "P16", "P18", "C04", "C07", "C08", "C09", "C10"]
    assert signs["R34"].group == "tasks"  # the instruction to run may be in the materials: asked where they are shown
    assert signs["R18"].group == "checking"  # a demand the task does not make is seen where the check-list is shown
    assert signs["R38"].text == "Нет заглушек, внутренних промптов и следов незавершённой генерации." and signs["R05"].evidence == "presence"


def test_a_change_of_the_criteria_takes_a_new_version():
    found = criteria.load()
    assert (found.version, fingerprint(found)) == PINNED, "критерии изменились: поднимите version в criteria.yaml и запишите новую пару в PINNED"


def test_two_criteria_of_the_passport_are_marked_and_kept_out_of_the_total():
    found = criteria.load()
    apart = {c.id: c.uncounted for c in found.criteria if c.uncounted}
    # Decided 06.10.2026: the sample of the style is the run of 28.09, which has neither points of choice nor written reports on purpose.
    assert list(apart) == ["P23", "P26"] and all(why.startswith("решение 06.10.2026: образец — версия интенсива 28.09, где ") for why in apart.values())
    assert "результат задания задан точно" in apart["P23"] and "письменные отчёты и журналы не требуются" in apart["P26"]
    # They are asked of the judge like the rest: kept out of the total, not out of the question.
    by_group, left = criteria.asked(found, "content")
    assert {"P23", "P26"} <= {c.id for c in by_group["values"]} and not {"P23", "P26"} & {one.criterion.id for one in left}


def test_the_judge_is_asked_only_what_a_text_can_show():
    found = criteria.load()
    by_group, left = criteria.asked(found, "content")
    assert {name: len(group) for name, group in by_group.items()} == {"logic": 13, "tasks": 13, "checking": 18, "package": 14, "values": 8}
    # What takes a run, the subject expert, or something the case does not have, is named with the reason.
    assert [(one.criterion.id, one.why) for one in left] == [
        ("R01", "needs: outcomes"), ("R02", "needs: outcomes"), ("R03", "needs: outcomes"), ("R07", "needs: hours"), ("R08", "needs: hours"),
        ("R20", "expert"), ("R21", "run"), ("R24", "expert"), ("R33", "run"), ("R40", "needs: approved_files"), ("P17", "needs: track"),
        ("C01", "needs: standard_texts"), ("C02", "needs: standard_texts"), ("C11", "needs: track")]
    asked = [c.id for group in by_group.values() for c in group]
    assert len(asked) == len(set(asked)) == 66 and len(asked) + len(left) == len(found.criteria)
    # With the inputs of a case known, their criteria are asked; a run and an expert are never replaced by a model.
    everything = frozenset({"outcomes", "hours", "approved_files", "standard_texts", "track"})
    by_group, left = criteria.asked(found, "content", everything)
    assert [one.criterion.id for one in left] == ["R20", "R21", "R24", "R33"] and sum(len(group) for group in by_group.values()) == 76
    by_group, left = criteria.asked(found, "content", frozenset({"outcomes"}))
    assert [c.id for c in by_group["logic"]][:3] == ["R01", "R02", "R03"] and "R07" in [one.criterion.id for one in left]
    # A concept is asked only what can be confirmed on a concept; the rubrics of the passport are about finished text.
    by_group, left = criteria.asked(found, "concept", everything)
    asked = [c.id for group in by_group.values() for c in group]
    assert all(ident.startswith("R") for ident in asked) and "R06" not in asked and "R02" in asked and not left


def written(tmp_path, change):
    data = yaml.safe_load(criteria.FILE.read_text(encoding="utf-8"))
    change(data)
    path = tmp_path / "criteria.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.parametrize("change, said", [
    (lambda d: d["criteria"][1].update(id="R01"), "номер критерия «R01» повторяется или записан не как буква и две цифры"),
    (lambda d: d["criteria"][0].update(id="R1"), "номер критерия «R1» повторяется или записан не как буква и две цифры"),
    (lambda d: d["criteria"][0].update(id="r01"), "номер критерия «r01» повторяется"),
    (lambda d: d["criteria"][2].update(source="rule"), "у критерия R03 источник «rule», которого нет в sources"),
    (lambda d: d["criteria"][2].update(group="logics"), "у критерия R03 группа «logics», которой нет в groups"),
    (lambda d: d["criteria"][2].update(by="model"), "не подходит по форме (criteria.2.by: literal_error)"),
    (lambda d: d["criteria"][2].update(needs=["outcome"]), "не подходит по форме (criteria.2.needs.0: literal_error)"),
    (lambda d: d["criteria"][2].update(weight=2), "не подходит по форме (criteria.2.weight: extra_forbidden)"),
    (lambda d: d["criteria"][2].update(text=""), "не подходит по форме (criteria.2.text: string_too_short)"),
    (lambda d: d["criteria"][2].update(stages=[]), "не подходит по форме (criteria.2.stages: too_short)"),
    (lambda d: d["scale"].pop("no_data"), "шкала должна состоять из 0, 1, 2, na, no_data"),
    (lambda d: d["scale"].update(maybe="может быть"), "шкала должна состоять из 0, 1, 2, na, no_data"),
    (lambda d: d["groups"]["logic"].update(shows=["everything"]), "не подходит по форме (groups.logic.shows.0: literal_error)"),
    (lambda d: d["sources"]["rules"]["scale"].pop("2"), "у источника «rules» шкала должна состоять из 0, 1, 2"),
    (lambda d: d["sources"]["passport_project"]["scale"].update(na="неприменимо"), "у источника «passport_project» шкала должна состоять из 0, 1, 2"),
    (lambda d: d["sources"]["rules"].pop("scale"), "не подходит по форме (sources.rules.scale: missing)"),
    (lambda d: d["sources"]["rules"].pop("short"), "не подходит по форме (sources.rules.short: missing)"),
    (lambda d: d["criteria"][2].update(evidence="maybe"), "не подходит по форме (criteria.2.evidence: literal_error)"),
    (lambda d: d.pop("version"), "не подходит по форме (version: missing)"),
    (lambda d: d.update(criteria=[]), "не подходит по форме (criteria: too_short)"),
])
def test_a_file_that_reads_wrong_is_refused_by_what_is_wrong(tmp_path, change, said):
    with pytest.raises(ValueError) as refused:
        criteria.load(written(tmp_path, change))
    assert str(refused.value).startswith("criteria.yaml: ") and said in str(refused.value)


def test_a_file_that_is_not_there_or_is_not_yaml_is_refused_too(tmp_path):
    with pytest.raises(ValueError, match=r"^nowhere\.yaml: не читается \(FileNotFoundError\)$"):
        criteria.load(tmp_path / "nowhere.yaml")
    (tmp_path / "criteria.yaml").write_text("version: [1, 2", encoding="utf-8")
    with pytest.raises(ValueError, match=r"^criteria\.yaml: не читается \(\w+\)$"):
        criteria.load(tmp_path / "criteria.yaml")
    (tmp_path / "criteria.yaml").write_bytes("версия: 1".encode("cp1251"))
    with pytest.raises(ValueError, match=r"^criteria\.yaml: не читается \(UnicodeDecodeError\)$"):
        criteria.load(tmp_path / "criteria.yaml")
    (tmp_path / "criteria.yaml").write_text("- просто список\n", encoding="utf-8")
    with pytest.raises(ValueError, match="не подходит по форме"):
        criteria.load(tmp_path / "criteria.yaml")
