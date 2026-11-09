"""The files of a project are named by their paths from the root of its repository (content 0.7): the address the plan
gives, what the methodologist is shown of it, the check of what was written, and a file of the project opened by its
path — as the README links to it."""

import io
import time
import zipfile
from uuid import uuid4

import pytest
from project_generator_lab import bridge
from project_generator_lab.concept import runs
from project_generator_lab.content import checks, render, writer
from project_generator_lab.content.checks import paths, repository_root, run_checks
from project_generator_lab.content.handoff import card
from project_generator_lab.content.models import Material
from project_generator_lab.tests.content.fixtures import handoff_fixture
from project_generator_lab.tests.content.test_route import client, wait_idle  # noqa: F401
from project_generator_lab.tests.content.test_theory_plan import model, planned, reviewer, search  # noqa: F401
from project_generator_lab.tests.test_bridge import approved_run, folders  # noqa: F401 - fixture

ADDRESS = "https://git.example.org/masters/D10P5_LLMbridge.ID_157"


def current(profile="legend", presentation="chapter", repository=ADDRESS):
    """A whole draft begun by this version of the generator, with the address of its repository known."""
    draft = planned(profile, presentation)
    draft.content_version = writer.CONTENT_VERSION
    draft.handoff = draft.handoff.model_copy(update={"repository": repository})
    return draft


def said(draft):
    return [(c.severity, c.part, c.message) for c in paths(draft)]


@pytest.mark.parametrize("address, root", [
    ("https://git.example.org/masters/D1P1_Terminal.ID_1578290.git", "git.example.org/masters/d1p1_terminal.id_1578290"),
    ("https://git.example.org/masters/D6P3_Dataquery.ID_1/-/blob/master/README.md?ref_type=heads", "git.example.org/masters/d6p3_dataquery.id_1"),
    ("  HTTPS://Git.Example.org/masters/D7P4/  ", "git.example.org/masters/d7p4"),
    ("http://git.example.org/a/b?x=1#y", "git.example.org/a/b"),
    # The cell of a plan may end the address as a sentence ends; an address may carry a name; ssh names the same repository.
    ("https://git.example.org/masters/D7P4.", "git.example.org/masters/d7p4"),
    ("https://git.example.org/masters/D7P4.git,", "git.example.org/masters/d7p4"),
    ("https://reader@git.example.org/masters/D7P4", "git.example.org/masters/d7p4"),
    ("git@git.example.org:masters/D7P4.git", "git.example.org/masters/d7p4"),
    ("ssh://git@git.example.org/masters/sub/D7P4.git", "git.example.org/masters/sub/d7p4"),
    ("https://git.example.org/masters/BOX.GIT", "git.example.org/masters/box"), ("GIT@git.example.org:masters/XX/", "git.example.org/masters/xx"),
    ("", ""), ("D10P5_LLMbridge", ""), ("masters/D10P5_LLMbridge", ""), ("ftp://git.example.org/a/b", ""),
    # Not the address of a project: a host or a group alone would name every repository there; words are not a host.
    ("https://gitlab.com/", ""), ("https://git.example.org/masters", ""), ("https://git.example.org/masters/", ""),
    ("https://будет позже", ""), ("https://localhost/a/b", ""), ("git@git.example.org:masters", ""),
])
def test_the_repository_is_one_whatever_page_of_it_the_plan_names(address, root):
    assert repository_root(address) == root


def test_a_sound_draft_names_its_files_by_their_paths():
    draft = current()
    assert said(draft) == [] and not [c for c in run_checks(draft, final=True) if c.code == "paths"]
    # The links the README is assembled with are paths from the root of the repository, every one of them.
    targets = [target for _, target in checks.WRITTEN_LINK.findall(render.readme(current("legend", "both")))]
    own = [t for t in targets if not t.startswith(("#", "http://", "https://"))]
    assert own and all(t.startswith("materials/") for t in own)


def test_the_address_of_the_repository_in_a_text_is_named_with_the_part_it_stands_in():
    draft = current()
    rule = "Файлы проекта называйте путём от корня репозитория: materials/…, src/…."
    draft.tasks[0].steps = ["Открой https://git.example.org/masters/D10P5_LLMbridge.ID_157/-/blob/master/materials/team.csv и найди роли"]
    draft.tasks[1].check = "Пир клонирует HTTPS://GIT.EXAMPLE.ORG/masters/d10p5_llmbridge.id_157.git и запускает проверку."
    assert draft.frame is not None and draft.theory is not None and draft.summary is not None
    draft.frame.instruction = ["Шаблон лежит в https://gitlab.example.com/school/other/-/raw/main/template.md"]
    assert said(draft) == [
        ("warn", "frame", "Во введении файл назван адресом в GitLab (https://gitlab.example.com/school/other/-/raw/main/template.md). "
                          "Если это файл самого проекта: файлы проекта называйте путём от корня репозитория: materials/…, src/…."),
        ("warn", "task:1", "В задании 1 записан адрес репозитория проекта (git.example.org/masters/d10p5_llmbridge.id_157). " + rule),
        ("warn", "task:2", "В задании 2 записан адрес репозитория проекта (git.example.org/masters/d10p5_llmbridge.id_157). " + rule)]
    # Said once for a part, however many times the address stands in it; in the theory, the annotation and a material too.
    draft = current()
    assert draft.theory is not None and draft.summary is not None
    draft.theory.topics[2].notes = f"Смотри {ADDRESS} и ещё раз {ADDRESS}."
    draft.summary.annotation = f"Проект лежит в {ADDRESS}."
    draft.materials.append(Material(path="guide.md", kind="reference", purpose="Памятка", contains="Шаги", tasks=[1], content=f"Клонируй {ADDRESS}.git\n", status="approved"))
    draft.materials.append(Material(path="data.csv", kind="data", purpose="Данные", contains="Строки", tasks=[1], content=f"url\n{ADDRESS}\n", status="approved"))
    assert [(part, message.split(" записан")[0]) for _, part, message in said(draft)] == [
        ("summary", "В аннотации"), ("topic:3", "В разделе теории 3"), ("material:guide.md", "В материале «guide.md»"),
        ("material:data.csv", "В материале «data.csv»")]
    # Another repository, a page of the web, the name of the project alone are nobody's concern here — nor is a
    # neighbour whose name only begins as this one does, or a host whose name only ends so.
    draft = current()
    draft.tasks[0].steps = ["Прочитай https://git-scm.example.org/docs и https://git.example.org/masters/D1P1_Terminal — это другой проект; свой называется D10P5_LLMbridge",
                            f"Соседи: {ADDRESS}8, {ADDRESS}.v2, {ADDRESS}-old, https://mygit.example.org/masters/D10P5_LLMbridge.ID_157 "
                            "и https://docs.git.example.org/masters/D10P5_LLMbridge.ID_157x"]
    assert said(draft) == []
    # The same repository however it is written: by ssh, with «.git», at the end of a sentence, inside code.
    for written in ("git@git.example.org:masters/D10P5_LLMbridge.ID_157.git", f"{ADDRESS}.", f"({ADDRESS})", f"{ADDRESS}/-/issues/3",
                    f"```\ngit clone {ADDRESS}.git\n```", f"`{ADDRESS}`", f"«{ADDRESS}»,"):
        draft = current()
        draft.tasks[0].steps = [f"Сделай так: {written} и продолжай"]
        assert [part for _, part, _ in said(draft)] == ["task:1"], written
    # Without an address from the plan there is nothing to look for but the address of a file in GitLab.
    draft = current(repository="")
    draft.tasks[0].steps = [f"Открой {ADDRESS} и {ADDRESS}/-/tree/master/materials"]
    assert [(part, message.split(" (")[0]) for _, part, message in said(draft)] == [("task:1", "В задании 1 файл назван адресом в GitLab")]


def test_a_link_written_in_a_text_leads_to_a_file_the_project_has():
    draft = current("legend", "both")
    assert draft.theory is not None
    draft.tasks[0].context = ("Данные — в [таблице](materials/team.csv), теория — в [конспекте](materials/theory.md#1-тема-1), источник — "
                              "[Git](https://git-scm.example.org/docs), раздел — [выше](#теория), код — в [файле](src/a.py).")
    assert said(draft) == []
    draft.tasks[0].context = "Данные — в [таблице](materials/roles.csv) и в [другой](materials/Team.csv#L3)."
    draft.tasks[2].transition = "Отчёт сравни с [образцом](C:\\Users\\author\\sample.md), [вторым](/home/author/sample.md) и [третьим](<file:///tmp/sample.md>)."
    assert said(draft) == [
        ("warn", "task:1", "В задании 1 ссылка «таблице» ведёт на materials/roles.csv, а такого файла в материалах проекта нет."),
        ("warn", "task:1", "В задании 1 ссылка «другой» ведёт на materials/Team.csv, а такого файла в материалах проекта нет."),
        ("warn", "task:3", "В задании 3 ссылка «образцом» ведёт на путь одного компьютера (C:\\Users\\author\\sample.md). "
                           "Файлы проекта называйте путём от корня репозитория: materials/…, src/…."),
        ("warn", "task:3", "В задании 3 ссылка «вторым» ведёт на путь одного компьютера (/home/author/sample.md). "
                           "Файлы проекта называйте путём от корня репозитория: materials/…, src/…."),
        ("warn", "task:3", "В задании 3 ссылка «третьим» ведёт на путь одного компьютера (file:///tmp/sample.md). "
                           "Файлы проекта называйте путём от корня репозитория: materials/…, src/….")]
    # The file of the theory is a file of the project only where the plan of the theory puts one into materials/.
    chapter = current("legend", "chapter")
    chapter.tasks[0].context = "Теория — в [конспекте](materials/theory.md)."
    assert [part for _, part, _ in said(chapter)] == ["task:1"]
    # A path of the file system that is the subject of a task — not a link — is the task's own business. So is a link
    # that begins with «/»: in a repository that is a path from its root. And what stands in code is not a link.
    plain = current()
    plain.tasks[0].steps = ["Перейди в /home/student и выведи список файлов; в Windows это C:\\Users\\student",
                            "Открой [раздел](/api/users), [данные](/materials/team.csv) и [код](/src/a.py)",
                            "Пример разметки: `[образец](C:\\sample.md)` и ещё\n```\n[файл](materials/absent.csv)\n[дом](~/notes.md)\n```"]
    assert said(plain) == []
    plain.tasks[0].steps = ["Заметки лежат [дома](~/notes.md), [в сети](\\\\server\\share\\a.md), [во временной папке](/tmp/a.md) и [у автора](/Users/author/a.md)"]
    assert [message.split("«")[1].split("»")[0] for _, _, message in said(plain)] == ["дома", "в сети", "во временной папке", "у автора"]
    # A link is read where the text is shown as Markdown — in a material named in capitals too — and not in data or code.
    for path, read in (("GUIDE.MD", True), ("notes.txt", True), ("script.py", False), ("data.csv", False), ("config.yml", False)):
        plain = current()
        plain.materials.append(Material(path=path, kind="reference", purpose="Памятка", contains="Шаги", tasks=[1],
                                        content="x = items[0](materials/absent.csv)\n", status="approved"))
        assert [part for _, part, _ in said(plain)] == ([f"material:{path}"] if read else []), path


@pytest.mark.parametrize("part, plant", [
    ("summary", lambda d: setattr(d.summary, "annotation", "Образец — [у автора](C:\\sample.md).")),
    ("frame", lambda d: setattr(d.frame, "introduction", "Образец — [у автора](~/sample.md).")),
    ("frame", lambda d: setattr(d.frame, "conclusion", "Данные — в [таблице](materials/absent.csv).")),
    ("topic:1", lambda d: setattr(d.theory.topics[0], "text", "Подробнее — в [заметках](file:///tmp/notes.md).")),
    ("topic:3", lambda d: setattr(d.theory.topics[2], "notes", "Данные — в [таблице](materials/absent.csv).")),
])
def test_a_link_is_read_in_every_text_that_is_shown_as_markdown(part, plant):
    draft = current("legend", "both")
    plant(draft)
    assert [found for _, found, _ in said(draft)] == [part]


PLANTED = f"смотри {ADDRESS} здесь"


@pytest.mark.parametrize("part, plant", [
    ("summary", lambda d: setattr(d.summary, "title", "Проект " + PLANTED)),
    ("summary", lambda d: setattr(d.summary, "annotation", PLANTED)),
    ("frame", lambda d: setattr(d.frame, "introduction", PLANTED)),
    ("frame", lambda d: setattr(d.frame, "instruction", ["Первое.", PLANTED])),
    ("frame", lambda d: setattr(d.frame, "conclusion", PLANTED)),
    *[("task:2", lambda d, field=field: setattr(d.tasks[1], field, PLANTED)) for field in ("title", "goal", "context", "result", "check", "transition")],
    *[("task:2", lambda d, field=field: setattr(d.tasks[1], field, ["Первое.", PLANTED])) for field in ("steps", "expected", "constraints", "peer_checks", "examples")],
    *[("topic:2", lambda d, field=field: setattr(d.theory.topics[1], field, PLANTED)) for field in ("title", "text", "notes", "question")],
    ("material:team.csv", lambda d: setattr(d.materials[0], "content", f"url\n{ADDRESS}\n")),
])
def test_the_address_is_looked_for_in_every_text_the_project_shows(part, plant):
    draft = current()
    assert draft.materials[0].path == "team.csv"
    plant(draft)
    assert [found for _, found, _ in said(draft)] == [part]


def test_a_text_of_brackets_alone_does_not_hold_the_check():
    """A model caught in a loop fills a field with one character; the checks are counted on every look at the draft,
    in the loop that serves everyone (independent review 07.10.2026: 63 s for 60 000 «[»)."""
    draft = current()
    for filling in ("[", "[a](", "](", "https://a", "http://x/-/", "`", "```\n", "~~~", "[x](materials/", "git.example.org/masters/"):
        hostile = (filling * 60_000)[:60_000]
        draft.materials.append(Material(path=f"m{len(draft.materials)}.md", kind="reference", purpose="Памятка", contains="Шаги", tasks=[1],
                                        content=hostile, status="approved"))
    started = time.monotonic()
    paths(draft)
    assert time.monotonic() - started < 1.0


def test_a_draft_begun_before_is_not_checked_for_its_paths():
    for version in ("0.3", "0.5", "0.6"):
        draft = current()
        draft.content_version = version
        draft.tasks[0].steps = [f"Открой {ADDRESS}/-/blob/master/materials/team.csv"]
        assert paths(draft) == [] and not [c for c in run_checks(draft, final=True) if c.code == "paths"], version
    newer = current()
    newer.tasks[0].steps = [f"Открой {ADDRESS}"]
    for version in ("0.7", "0.10", "1.0"):
        newer.content_version = version
        assert [c.code for c in run_checks(newer) if c.code == "paths"] == ["paths"], version
    assert [newer.model_copy(update={"content_version": v}).since("0.7") for v in ("0.6", "0.7", "0.7.1", "0.10", "1.0", "0.6.9")] == [False, True, True, True, True, False]


def test_the_writers_are_told_the_rule_and_not_the_address():
    frozen = writer.snapshot()
    assert "Файл проекта называй путём от корня репозитория: `materials/team.csv`, `src/report.md`." in frozen["system"]
    assert "Адрес репозитория, адрес файла в GitLab и путь на чьём-то компьютере" in frozen["system"]
    draft = current()
    # Nothing a writer or a critic is sent names the address: a model shown it would write it.
    sent = [writer.project(draft), writer.project(draft, reviewer=True), writer.style_payload(draft, ""), writer.plan_payload(draft, ""),
            writer.summary_payload(draft), writer.task_payload(draft, 1), writer.theory_payload(draft), writer.frame_payload(draft),
            writer.material_payload(draft, "team.csv"), writer.critic_payload(draft)]
    assert draft.handoff.repository == ADDRESS and "git.example.org" not in writer.dumps({"all": sent})


def test_the_card_of_what_the_generator_received_names_the_repository_when_the_plan_gave_one():
    rows = {row["label"]: row for row in card(handoff_fixture().model_copy(update={"repository": ADDRESS}))}
    assert rows["Репозиторий проекта"] == {"label": "Репозиторий проекта", "value": ADDRESS, "ok": True,
                                         "items": ["Файлы проекта в тексте называются путём от корня репозитория: materials/…, src/…"]}
    assert list(rows).index("Репозиторий проекта") == list(rows).index("Сквозной сюжет серии") + 1
    # A draft that received no address shows the card it always showed — one begun before the styles too.
    assert "Репозиторий проекта" not in {row["label"] for row in card(handoff_fixture())}
    assert "Репозиторий проекта" not in {row["label"] for row in card(handoff_fixture(), styled=False)}
    assert "Репозиторий проекта" in {row["label"] for row in card(handoff_fixture().model_copy(update={"repository": ADDRESS}), styled=False)}
    # What the cell of the plan held is shown as it is; a value that is not the address of a project is marked — the
    # check of the texts cannot look for it.
    for value in ("D1P1_Terminal", "будет позже", "https://gitlab.com/", "ссылка"):
        row = {row["label"]: row for row in card(handoff_fixture().model_copy(update={"repository": value}))}["Репозиторий проекта"]
        items = row["items"]
        assert isinstance(items, list) and (row["value"], row["ok"], len(items)) == (value, False, 2) and "не адрес репозитория" in items[0], value


def test_the_address_of_the_form_reaches_the_content_and_not_the_concept(folders):  # noqa: F811
    run = approved_run()
    assert bridge.handoff(run.id).repository == ""
    run.request.brief.repository = f"{ADDRESS}.git"
    runs.save_run(run)
    assert bridge.handoff(run.id).repository == f"{ADDRESS}.git"
    assert "repository" not in run.request.brief.for_concept()


def test_a_file_opened_by_its_path_is_the_file_of_the_archive_to_the_byte():
    for profile, presentation in (("legend", "both"), ("quest", "materials"), ("legend", "chapter")):
        draft = current(profile, presentation)
        draft.materials.append(Material(path="notes.md", kind="reference", purpose="Памятка", contains="Шаги", tasks=[1], content="без перевода строки", status="approved"))
        draft.materials.append(Material(path="sub/dir/log.txt", kind="reference", purpose="Журнал", contains="Строки", tasks=[1], content="a\r\nb\n\n", status="approved"))
        archive = zipfile.ZipFile(io.BytesIO(render.bundle(draft)))
        files = [name for name in archive.namelist() if not name.endswith(("/", ".gitkeep"))]
        assert {"README.md", "check-list.yml", "materials/team.csv", "materials/notes.md", "materials/sub/dir/log.txt"} <= set(files)
        assert ("materials/theory.md" in files) == (presentation != "chapter")
        for name in files:
            served = render.project_file(draft, name)
            assert served is not None and served.encode("utf-8") == archive.read(name), name
        assert render.project_file(draft, "materials/notes.md") == "без перевода строки\n"
        for odd in ("materials/notes.md/", "materials//team.csv", "materials/./team.csv", "materials/TEAM.CSV", "materials/sub/dir/../dir/log.txt",
                    "materials\\team.csv", " README.md", "README.md ", "src/.gitkeep", "materials", "materials/" + "a" * 5000):
            assert render.project_file(draft, odd) is None, odd


def test_a_file_of_the_project_opens_by_its_path(client, model, search):  # noqa: F811
    run_id = str(uuid4())
    model.presentation = "both"
    client.post(f"/api/content/{run_id}/start")
    wait_idle(client, run_id)
    from project_generator_lab.tests.content.test_route import walk

    walk(client, run_id, stop="approve_text")
    file = lambda path: client.get(f"/api/content/{run_id}/file", params={"path": path})  # noqa: E731
    readme = client.get(f"/api/content/{run_id}/preview").text
    assert file("README.md").text == readme and file("README.md").headers["content-type"] == "text/plain; charset=utf-8"
    assert file("check-list.yml").text == client.get(f"/api/content/{run_id}/check-list-preview").text
    # Every file the README links to by a path is there under that path.
    linked = sorted({target.split("#")[0] for _, target in checks.WRITTEN_LINK.findall(readme) if target.startswith("materials/")})
    assert "materials/theory.md" in linked and len(linked) > 1
    for path in linked:
        assert file(path).status_code == 200 and file(path).text.endswith("\n"), path
    assert file("materials/theory.md").text.startswith("# Теория к проекту «")
    # What the project does not have is said so: a file of src/ is the learner's to make.
    for missing in ("src/task1.md", "materials/nothing.md", "materials/", "materials/../README.md", "../README.md", "/etc/passwd", "", "readme.md"):
        refused = file(missing)
        assert refused.status_code == 404 and "в проекте нет" in refused.json()["detail"], missing
    assert client.get(f"/api/content/{uuid4()}/file", params={"path": "README.md"}).status_code == 404
