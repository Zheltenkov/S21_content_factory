from content_audit.artifacts import build_artifact_text_index
from content_audit.checklist_grounding import assess_checklist_grounding
from content_audit.checklist_matching import ChecklistQuestion


class _FakeTsharkResult:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


def test_grounding_flags_self_join_order_missing_from_readme() -> None:
    readme = """
## Exercise 10

Find pairs of people who live at the same address.

| person_name1 | person_name2 | common_address |
"""
    questions = [
        ChecklistQuestion(
            name="Exercise 10 Find persons from one city",
            description_text="""
Checks for the file day02_ex10.sql.
SELECT p1.name, p2.name, p1.address AS common_address
FROM person p1
INNER JOIN person p2 ON p1.id > p2.id
AND p1.address = p2.address
ORDER BY 1, 2, 3
""",
        )
    ]

    issues = assess_checklist_grounding(questions, readme)

    assert len(issues) == 1
    assert issues[0].issue_type == "ungrounded_self_join_order"
    assert issues[0].evidence == "p1.id > p2.id"


def test_grounding_flags_use_case_filename_variant_with_markdown_escaped_readme() -> None:
    readme = r"""
## Exercise 00

The answers must be in the file ex00\_<product prefix>\_UC.docx.
"""
    questions = [
        ChecklistQuestion(
            name="Exercise 00 — Description of Use Cases",
            description_text="The answers are in the file `ex00_<product prefix>_use case.docx`.",
        )
    ]

    issues = assess_checklist_grounding(questions, readme)

    assert len(issues) == 1
    assert issues[0].issue_type == "expected_file_name_mismatch"
    assert "use case.docx" in issues[0].evidence
    assert "UC.docx" in issues[0].evidence


def test_grounding_does_not_flag_self_join_order_when_readme_mentions_it() -> None:
    readme = """
## Exercise 10

Find pairs of people who live at the same address. Use `p1.id > p2.id`
to avoid duplicate pairs.

| person_name1 | person_name2 | common_address |
"""
    questions = [
        ChecklistQuestion(
            name="Exercise 10 Find persons from one city",
            description_text="INNER JOIN person p2 ON p1.id > p2.id AND p1.address = p2.address",
        )
    ]

    assert assess_checklist_grounding(questions, readme) == []


def test_grounding_flags_duplicate_pizzeria_names_result() -> None:
    readme = """
## Exercise 06

Please create a function `fnc_person_visits_and_eats_on_date` that will
find the names of pizzerias that a person visited and where he could buy
pizza for less than the given price.
"""
    questions = [
        ChecklistQuestion(
            name="Exercise 06 — Function like a function-wrapper",
            description_text="""
The result of SQL:
"Pizza Hut"
"Pizza Hut"
"Pizza Hut"
"Pizza Hut"
""",
        )
    ]

    issues = assess_checklist_grounding(questions, readme)

    assert len(issues) == 1
    assert issues[0].issue_type == "suspicious_duplicate_name_result"
    assert issues[0].evidence == "Pizza Hut × 4"


def test_grounding_does_not_flag_duplicate_rows_when_readme_asks_for_pizzas() -> None:
    readme = """
## Exercise 06

Return names of pizzas and pizzerias where the user can buy them.
"""
    questions = [
        ChecklistQuestion(
            name="Exercise 06",
            description_text='"Pizza Hut" "Pizza Hut" "Pizza Hut"',
        )
    ]

    assert assess_checklist_grounding(questions, readme) == []


def test_grounding_flags_expected_file_name_mismatch() -> None:
    readme = """
## Task 1

Prepare the use case document `xxx_usecase.dox`.
"""
    questions = [
        ChecklistQuestion(
            name="Task 1",
            description_text="Reviewer checks that `xxx_UC.dox` exists and contains the use case.",
        )
    ]

    issues = assess_checklist_grounding(questions, readme)

    assert len(issues) == 1
    assert issues[0].issue_type == "expected_file_name_mismatch"
    assert issues[0].evidence == "xxx_UC.dox vs xxx_usecase.dox"


def test_grounding_reads_cyrillic_placeholder_file_refs() -> None:
    readme = """
## Exercise 00

Распиши свои ответы в файле ex00\\_<префикс продукта>\\_UC.docx.
"""
    questions = [
        ChecklistQuestion(
            name="Exercise 00",
            description_text="The answers are in the file `ex00_<product prefix>_use case.docx`.",
        )
    ]

    issues = assess_checklist_grounding(questions, readme)

    assert len(issues) == 1
    assert issues[0].issue_type == "expected_file_name_mismatch"
    assert issues[0].evidence == "ex00_<product prefix>_use case.docx vs ex00_<префикс продукта>_UC.docx"



def test_grounding_does_not_flag_attached_resource() -> None:
    readme = """
## Task 1

Analyze the network capture and answer the questions.
"""
    questions = [
        ChecklistQuestion(
            name="Task 1",
            description_text="The archive contains `capture.pcapng`; check that it can be opened.",
        )
    ]

    issues = assess_checklist_grounding(questions, readme, available_files=["materials/capture.pcapng"])

    assert issues == []


def test_grounding_flags_ungrounded_command() -> None:
    readme = """
## Task 1

Analyze the network capture and list the observed hosts.
"""
    questions = [
        ChecklistQuestion(
            name="Task 1",
            description_text="The expected pcapng contains command output from `whoami`.",
        )
    ]

    issues = assess_checklist_grounding(questions, readme, available_files=["materials/capture.pcapng"])

    assert any(issue.issue_type == "ungrounded_command" and issue.evidence == "whoami" for issue in issues)


def test_grounding_flags_missing_expected_marker_inside_attached_artifact(workspace_tmp_path) -> None:
    (workspace_tmp_path / "capture.pcapng").write_bytes(b"GET /index.html\\r\\nHost: example.local\\r\\n")
    artifact_index = build_artifact_text_index(workspace_tmp_path)
    readme = """
## Task 2

Analyze the attached pcapng dump.
"""
    questions = [
        ChecklistQuestion(
            name="Task 2. Reverse shell evidence",
            description_text="The expected pcapng contains command output from `whoami`.",
        )
    ]

    issues = assess_checklist_grounding(
        questions,
        readme,
        available_files=["capture.pcapng"],
        artifact_text_index=artifact_index,
    )

    assert any(
        issue.issue_type == "artifact_missing_expected_text" and "whoami" in issue.evidence for issue in issues
    )


def test_grounding_accepts_expected_marker_inside_attached_artifact(workspace_tmp_path) -> None:
    (workspace_tmp_path / "capture.pcapng").write_bytes(b"whoami\\r\\nstudent\\r\\n")
    artifact_index = build_artifact_text_index(workspace_tmp_path)
    questions = [
        ChecklistQuestion(
            name="Task 2",
            description_text="The expected pcapng contains command output from `whoami`.",
        )
    ]

    issues = assess_checklist_grounding(
        questions,
        "## Task 2\nAnalyze the attached pcapng dump.",
        available_files=["capture.pcapng"],
        artifact_text_index=artifact_index,
    )

    assert all(issue.issue_type != "artifact_missing_expected_text" for issue in issues)


def test_grounding_accepts_command_output_extracted_by_tshark(workspace_tmp_path, monkeypatch) -> None:
    (workspace_tmp_path / "capture.pcapng").write_bytes(b"\x00\x01\x02\x03")

    def fake_run(args, **kwargs):
        del kwargs
        command_line = " ".join(args)
        if "-e tcp.stream" in command_line:
            return _FakeTsharkResult("0\n")
        if "follow,tcp,ascii,0" in command_line:
            return _FakeTsharkResult("whoami\r\nstudent\r\n")
        return _FakeTsharkResult("")

    monkeypatch.setattr("content_audit.artifacts.shutil.which", lambda name: "tshark" if name == "tshark" else None)
    monkeypatch.setattr("content_audit.artifacts.subprocess.run", fake_run)

    artifact_index = build_artifact_text_index(workspace_tmp_path)
    questions = [
        ChecklistQuestion(
            name="Task 2",
            description_text="The expected pcapng contains command output from `whoami`.",
        )
    ]

    issues = assess_checklist_grounding(
        questions,
        "## Task 2\nAnalyze the attached pcapng dump.",
        available_files=["capture.pcapng"],
        artifact_text_index=artifact_index,
    )

    assert all(issue.issue_type != "artifact_missing_expected_text" for issue in issues)


def test_grounding_flags_command_mention_without_command_output(workspace_tmp_path) -> None:
    (workspace_tmp_path / "capture.pcapng").write_bytes(b"result of: id uname -a whoami result of: whoami ls -la")
    artifact_index = build_artifact_text_index(workspace_tmp_path)
    questions = [
        ChecklistQuestion(
            name="Task 2",
            description_text="The expected pcapng contains command output from `whoami`.",
        )
    ]

    issues = assess_checklist_grounding(
        questions,
        "## Task 2\nAnalyze the attached pcapng dump.",
        available_files=["capture.pcapng"],
        artifact_text_index=artifact_index,
    )

    assert any(
        issue.issue_type == "artifact_missing_expected_text" and "вывод whoami" in issue.evidence
        for issue in issues
    )


def test_grounding_flags_ungrounded_sql_condition() -> None:
    readme = """
## Exercise 03

Return all active customers ordered by name.
"""
    questions = [
        ChecklistQuestion(
            name="Exercise 03",
            description_text="SELECT * FROM customers WHERE country = 'RU' AND status = 'active' ORDER BY name",
        )
    ]

    issues = assess_checklist_grounding(questions, readme)
    issue_types = {issue.issue_type for issue in issues}

    assert "ungrounded_sql_condition" in issue_types
    assert any(issue.evidence == "country = 'RU'" for issue in issues)


def test_grounding_does_not_treat_file_template_as_sql_condition() -> None:
    readme = """
## Exercise 01

Prepare a use case document for the selected product.
"""
    questions = [
        ChecklistQuestion(
            name="Exercise 01",
            description_text="The file `ex00_<product prefix>_use case.docx` is present and formatted correctly.",
        )
    ]

    issues = assess_checklist_grounding(questions, readme)

    assert all(issue.issue_type != "ungrounded_sql_condition" for issue in issues)


def test_grounding_keeps_semantically_described_sql_condition() -> None:
    readme = """
## Exercise 10

Find pairs of people who live at the same address.
"""
    questions = [
        ChecklistQuestion(
            name="Exercise 10",
            description_text="SELECT * FROM person p1 JOIN person p2 ON p1.address = p2.address",
        )
    ]

    assert assess_checklist_grounding(questions, readme) == []


def test_grounding_flags_expected_output_semantic_mismatch() -> None:
    readme = """
## Exercise 06

Return names of pizzas that are available for less than the requested price.
"""
    questions = [
        ChecklistQuestion(
            name="Exercise 06",
            description_text='Expected output: "Pizza Hut"',
        )
    ]

    issues = assess_checklist_grounding(questions, readme)

    assert len(issues) == 1
    assert issues[0].issue_type == "expected_output_semantic_mismatch"
