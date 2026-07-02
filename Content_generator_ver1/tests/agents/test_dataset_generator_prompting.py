from content_gen.agents.base.llm_client import LLMClientProtocol
from content_gen.agents.dataset_generator import DatasetGeneratorAgent
from content_gen.artifact_chain import EvidenceSpec
from content_gen.models.schemas import PracticeTask, ProjectSeed


class RecordingLLM(LLMClientProtocol):
    def __init__(self):
        self.last_user = ""

    def complete(self, system: str, user: str, response_format=None, **kwargs) -> str:
        self.last_user = user
        return "# test"


def test_generate_file_content_includes_project_context():
    llm = RecordingLLM()
    agent = DatasetGeneratorAgent(llm)
    seed = ProjectSeed(
        language="ru",
        project_type="individual",
        title_seed="Публичные выступления",
        project_description="Проект про подготовку выступления, работу с волнением и критикой.",
        learning_outcomes=["Подготовить сообщение для выступления"],
        skills=["SERMON", "сторителлинг"],
        thematic_block="Блок 5. Soft skills",
        sjm="Ты готовишь выступление перед командой и отвечаешь на вопросы после него.",
    )

    content = agent._generate_file_content(
        filename="message_description.md",
        file_type="md",
        description="Описание основного сообщения",
        task_context="Задача 2: Разработка эффективного сообщения",
        seed=seed,
    )

    assert content == b"# test"
    assert "Публичные выступления" in llm.last_user
    assert "работу с волнением" in llm.last_user
    assert "Soft skills" in llm.last_user


def test_should_not_generate_solution_like_materials():
    agent = DatasetGeneratorAgent(RecordingLLM())

    should_generate = agent._should_generate_file(
        input_data="Готовая матрица решений — см. файл `materials/decision_matrix.md`",
        task_context="Задача 2: Сопоставить варианты решения по критериям",
        filename="decision_matrix.md",
    )

    assert should_generate is False


def test_generate_files_attaches_evidence_spec_metadata():
    llm = RecordingLLM()
    agent = DatasetGeneratorAgent(llm)
    seed = ProjectSeed(
        language="ru",
        project_type="individual",
        title_seed="Рабочий анализ",
        project_description="Проект про анализ рабочих наблюдений.",
        learning_outcomes=["Сформулировать выводы по наблюдениям"],
        skills=["анализ"],
    )
    spec = EvidenceSpec(
        path="materials/task_01_source_notes.md",
        evidence_type="raw_case_evidence",
        contains=["сырые заметки"],
        excludes=["готовый отчет"],
        student_must_derive=["выводы и структуру артефакта"],
        source_task_index=1,
    )
    task = PracticeTask(
        title="Анализ наблюдений",
        input_data="Сырые заметки — см. файл `materials/task_01_source_notes.md`.",
        goal="Проанализировать наблюдения.",
        expected_artifact="Таблица наблюдений",
    )

    files = agent.generate_files([task], seed, evidence_specs=[spec])

    assert files[0]["path"] == "materials/task_01_source_notes.md"
    assert files[0]["evidence_spec"]["evidence_type"] == "raw_case_evidence"
    assert "EVIDENCE SPEC" in llm.last_user
    assert "Студент должен вывести сам" in llm.last_user
