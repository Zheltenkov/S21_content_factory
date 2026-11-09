"""The folder of a case: what is read from it, and what is said of a folder that is not one."""

import pytest
from project_generator_lab.benchmark import cases
from project_generator_lab.benchmark.cases import Meta
from project_generator_lab.tests.benchmark.project import repository as written
from project_generator_lab.tests.benchmark.worded import META, kept


def test_a_case_is_read_from_its_folder(tmp_path):
    folder = tmp_path / "lib"
    written(folder / "etalon")
    (folder / "inputs" / "plan").mkdir(parents=True)
    (folder / "inputs" / "plan" / "row.csv").write_text("Название проекта\nКаталог\n", encoding="utf-8")
    (folder / "inputs" / "brief.md").write_text("Бриф", encoding="utf-8")
    (folder / "meta.yaml").write_text("profile: SQL, бассейн\nlevel: базовый\nsplit: held_out\nnote: первый случай\n", encoding="utf-8")
    (folder / "answers.yaml").write_text("Какой уровень у участников?: базовый\nСколько заданий?: 3\n", encoding="utf-8")
    case = cases.load(folder)
    assert (case.name, case.folder, case.etalon) == ("lib", folder, folder / "etalon")
    assert case.meta == Meta(profile="SQL, бассейн", level="базовый", split="held_out", note="первый случай")
    assert case.inputs == ["brief.md", "plan/row.csv"]
    assert case.answers == {"Какой уровень у участников?": "базовый", "Сколько заданий?": "3"}  # an answer is text, whatever YAML made of it


def test_a_repository_is_a_case_of_one_etalon(tmp_path):
    # Projects unpacked from an archive are measured as they lie: nothing is known of them but the project itself.
    written(tmp_path / "SQLB1" / "SQLB1")
    case = cases.load(tmp_path / "SQLB1")
    assert (case.name, case.etalon, case.meta, case.inputs, case.answers) == ("SQLB1", tmp_path / "SQLB1", Meta(), [], {})
    # Inputs without a project made of them are a case too — one with nothing to measure yet.
    (tmp_path / "new" / "inputs").mkdir(parents=True)
    assert cases.load(tmp_path / "new").etalon is None


@pytest.mark.parametrize("name, text, said", [
    ("meta.yaml", "split: maybe\n", "x/meta.yaml: не подходят поля: split. Известные: profile, level, split, note, readme, checklist, skip, broken, series, rank"),
    ("meta.yaml", "profil: SQL\nlevel: [1]\n", "x/meta.yaml: не подходят поля: level, profil. Известные: profile, level, split, note, readme, checklist, skip, broken, series, rank"),
    ("meta.yaml", "- список\n", "x/meta.yaml: ожидается «ключ: значение»"),
    ("meta.yaml", "profile: [\n", "x/meta.yaml: не читается как YAML (ParserError)"),
    ("answers.yaml", "просто текст\n", "x/answers.yaml: ожидается «ключ: значение»"),
    ("answers.yaml", "a: " + "б" * 100_000 + "\n", "x/answers.yaml: больше 200 КБ"),
    ("meta.yaml", "note: " + "[" * 3000 + "]" * 3000 + "\n", "x/meta.yaml: не читается как YAML (RecursionError)"),
])
def test_what_is_wrong_with_the_files_of_a_case_names_the_file(tmp_path, name, text, said):
    written(tmp_path / "x" / "etalon")
    (tmp_path / "x" / name).write_text(text, encoding="utf-8")
    with pytest.raises(ValueError) as refused:
        cases.load(tmp_path / "x")
    assert str(refused.value) == said
    # One case that does not read does not stop the others, and is not lost: it is passed over by name.
    written(tmp_path / "y")
    found, passed = cases.find(tmp_path)
    assert [case.name for case in found] == ["y"] and passed == [said]


def test_the_cases_of_a_folder_are_found_and_what_is_not_a_case_is_named(tmp_path):
    written(tmp_path / "B2" / "B2")
    written(tmp_path / "B1")
    (tmp_path / "exam" / "one").mkdir(parents=True)  # twenty exercises in subfolders, as the exam of the archive: not one project
    written(tmp_path / "exam" / "one" / "L1")
    written(tmp_path / "exam" / "two")
    (tmp_path / ".git").mkdir()
    (tmp_path / "notes.txt").write_text("не каталог", encoding="utf-8")
    (tmp_path / "empty.yaml").mkdir()
    (tmp_path / "empty.yaml" / "meta.yaml").write_text("", encoding="utf-8")  # an empty file is a case nothing is known of
    found, passed = cases.find(tmp_path)
    assert [case.name for case in found] == ["B1", "B2", "empty.yaml"] and found[2].meta == Meta()
    assert passed == ["«exam» — не случай: нет meta.yaml, etalon/, inputs/ и README"]
    # A folder that is a case is that case, not a folder of cases.
    assert [case.name for case in cases.find(tmp_path / "B2")[0]] == ["B2"]
    with pytest.raises(ValueError, match="каталога «.*нет такого» нет"):
        cases.find(tmp_path / "нет такого")


def test_what_is_known_of_a_case_is_found_beside_its_readme_too(tmp_path):
    """Independent check, 05.10.2026: fifteen projects lie as «X/X/README.md»; a meta.yaml put beside the README was
    passed over, and a case kept back for the release was measured in an ordinary run."""
    written(tmp_path / "K" / "K")
    (tmp_path / "K" / "K" / "meta.yaml").write_text("split: held_out\n", encoding="utf-8")
    assert cases.load(tmp_path / "K").meta.split == "held_out"
    # Two files that may say different things are not chosen between: the case is refused until one is left.
    (tmp_path / "K" / "meta.yaml").write_text("split: tuning\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"^K: meta.yaml лежит в нескольких местах \(meta.yaml, K/meta.yaml\) — оставьте один$"):
        cases.load(tmp_path / "K")
    # Inside etalon/ it is the case's too, and so it is under a name in other letters.
    written(tmp_path / "E" / "etalon")
    (tmp_path / "E" / "inputs").mkdir()
    (tmp_path / "E" / "etalon" / "Meta.YAML").write_text("split: held_out\n", encoding="utf-8")
    assert cases.load(tmp_path / "E").meta.split == "held_out"
    written(tmp_path / "M" / "M")
    (tmp_path / "M" / "M" / "meta.yml").write_text("split: held_out\n", encoding="utf-8")
    with pytest.raises(ValueError, match="^M: файл meta.yml не читается — нужен meta.yaml$"):
        cases.load(tmp_path / "M")
    assert cases.find(tmp_path)[1] == ["K: meta.yaml лежит в нескольких местах (meta.yaml, K/meta.yaml) — оставьте один", "M: файл meta.yml не читается — нужен meta.yaml"]


def test_no_more_cases_are_read_than_the_limit(tmp_path, monkeypatch):
    for name in ("a", "b", "c"):
        written(tmp_path / name)
    monkeypatch.setattr(cases, "MAX_CASES", 2)
    found, passed = cases.find(tmp_path)
    assert [case.name for case in found] == ["a", "b"] and passed == ["каталогов 3, прочитаны первые 2"]


def test_a_case_is_its_own_folder_and_the_limit_is_met_exactly(tmp_path, monkeypatch):
    written(tmp_path / "B2" / "inner")
    found, passed = cases.find(tmp_path / "B2")
    assert [(case.name, case.folder) for case in found] == [("B2", tmp_path / "B2")] and passed == []  # the case, not the folder inside it
    written(tmp_path / "many" / "a")
    written(tmp_path / "many" / "b")
    monkeypatch.setattr(cases, "MAX_CASES", 2)
    assert cases.find(tmp_path / "many")[1] == []
    (tmp_path / "B2" / "meta.yaml").write_text("note: " + "я" * 20 + "\n", encoding="utf-8")
    monkeypatch.setattr(cases, "MAX_YAML", (tmp_path / "B2" / "meta.yaml").stat().st_size)
    assert cases.load(tmp_path / "B2").meta.note == "я" * 20


def test_a_working_folder_is_a_case_by_the_word_of_its_meta(tmp_path):
    # No «README.md» in it: without the meta.yaml the folder is not a case, with it — a case whose project is the folder.
    kept(tmp_path / "root" / "day")
    found, passed = cases.find(tmp_path / "root")
    assert found == [] and passed == ["«day» — не случай: нет meta.yaml, etalon/, inputs/ и README"]
    kept(tmp_path / "root" / "day", meta=META + 'skip:\n  - "datasets"\nbroken: ["materials/terminal_rus.md"]\n')
    [case], passed = cases.find(tmp_path / "root")
    assert passed == [] and case.etalon == tmp_path / "root" / "day"
    assert (case.meta.readme, case.meta.checklist, case.meta.skip, case.meta.broken) == ("README.md.docx", "D1_check-list_RUS.yml", ["datasets"], ["materials/terminal_rus.md"])
    # With an etalon/ folder the names are from that folder.
    kept(tmp_path / "boxed" / "etalon")
    (tmp_path / "boxed" / "meta.yaml").write_text(META, encoding="utf-8")
    assert cases.load(tmp_path / "boxed").etalon == tmp_path / "boxed" / "etalon"
    # A meta.yaml that names no README leaves a folder without one a case with nothing to measure.
    kept(tmp_path / "silent", meta="note: файлы не названы\n")
    assert cases.load(tmp_path / "silent").etalon is None


@pytest.mark.parametrize("text, fields", [
    ("readme: [a, b]\n", "readme"), ("skip: datasets\n", "skip"), ('skip: [""]\n', "skip.0"), ("broken: {a: 1}\n", "broken"),
    ("skip: [" + ", ".join("a" for _ in range(101)) + "]\n", "skip"), ("readme: " + "я" * 501 + "\n", "readme"),
])
def test_the_names_of_a_meta_are_words_and_lists_of_words(tmp_path, text, fields):
    kept(tmp_path / "x", meta=text)
    with pytest.raises(ValueError) as refused:
        cases.load(tmp_path / "x")
    assert str(refused.value) == f"x/meta.yaml: не подходят поля: {fields}. Известные: profile, level, split, note, readme, checklist, skip, broken, series, rank"
    kept(tmp_path / "y", meta="skip: [" + ", ".join("a" for _ in range(100)) + "]\nreadme: " + "я" * 500 + "\n")
    assert len(cases.load(tmp_path / "y").meta.skip) == 100
