"""Тесты для RubricScorer."""

from content_gen.models.criteria_models import CheckMethod, CriteriaItem, CriteriaReport, StrictnessLevel
from content_gen.models.readme_document import ReadmeDocument
from content_gen.utils.rubric_export import criteria_to_json
from content_gen.validators.rubric.scorer import RubricScorer


class TestRubricScorer:
    """Тесты для основного класса RubricScorer."""

    def test_init(self, mock_llm_client):
        """Тест инициализации RubricScorer."""
        scorer = RubricScorer(language="ru", llm_client=mock_llm_client)
        assert scorer.lang == "ru"
        assert scorer.llm == mock_llm_client
        assert scorer.rx_h1 is not None
        assert scorer.rx_h2 is not None
        assert scorer.rx_h3 is not None

    def test_score_empty_markdown(self, mock_llm_client):
        """Тест оценки пустого markdown."""
        scorer = RubricScorer(language="ru", llm_client=mock_llm_client)
        report = scorer.score("")

        assert isinstance(report, CriteriaReport)
        assert len(report.items) > 0
        # Большинство критериев должны провалиться
        failed_items = [item for item in report.items if item.score == 0]
        assert len(failed_items) > 0

    def test_score_valid_markdown(self, mock_llm_client, sample_markdown):
        """Тест оценки валидного markdown."""
        scorer = RubricScorer(language="ru", llm_client=mock_llm_client)
        report = scorer.score(sample_markdown)

        assert isinstance(report, CriteriaReport)
        assert len(report.items) > 0
        # Проверяем структуру отчета
        assert hasattr(report, 'total')
        assert hasattr(report, 'max_score')
        assert hasattr(report, 'items')

    def test_score_parses_structure(self, mock_llm_client, sample_markdown):
        """Тест парсинга структуры markdown."""
        scorer = RubricScorer(language="ru", llm_client=mock_llm_client)

        # Проверяем, что регулярные выражения работают
        h1_matches = scorer.rx_h1.findall(sample_markdown)
        h2_matches = scorer.rx_h2.findall(sample_markdown)
        h3_matches = scorer.rx_h3.findall(sample_markdown)

        assert len(h1_matches) > 0
        assert len(h2_matches) > 0
        assert len(h3_matches) > 0

    def test_score_calculates_total(self, mock_llm_client, sample_markdown):
        """Тест расчета общего балла."""
        scorer = RubricScorer(language="ru", llm_client=mock_llm_client)
        report = scorer.score(sample_markdown)

        # Проверяем, что total рассчитан
        assert report.total >= 0
        assert report.max_score > 0
        assert report.total <= report.max_score

    def test_score_document_uses_typed_readme_boundary(self, mock_llm_client, monkeypatch):
        """Тест typed entrypoint для оценки README."""
        scorer = RubricScorer(language="ru", llm_client=mock_llm_client)
        captured = {"sections": []}

        def item(item_id: str) -> CriteriaItem:
            return CriteriaItem(
                id=item_id,
                title=item_id,
                description=item_id,
                check_method=CheckMethod.SCRIPT,
                score=1,
                comments=[],
            )

        def section1(document):
            captured["sections"].append(("1", document.title))
            return [item("1.1")]

        def section2(document, learning_outcomes=None):
            captured["sections"].append(("2", learning_outcomes))
            return [item("2.1")]

        def section3(document):
            captured["sections"].append(("3", document.title))
            return [item("3.1")]

        def section4(document):
            captured["sections"].append(("4", document.title))
            return [item("4.1")]

        monkeypatch.setattr(scorer.section1_checker, "check_document", section1)
        monkeypatch.setattr(scorer.section2_checker, "check_document", section2)
        monkeypatch.setattr(scorer.section3_checker, "check_document", section3)
        monkeypatch.setattr(scorer.section4_checker, "check_document", section4)
        document = ReadmeDocument.from_markdown("# Проект\n\n## Глава 2. Теория\n\nТекст.")
        monkeypatch.setattr(
            ReadmeDocument,
            "to_markdown",
            lambda self: (_ for _ in ()).throw(AssertionError("typed scorer rendered markdown")),
        )

        report = scorer.score_document(document, learning_outcomes=["LO"], use_cache=False)

        assert isinstance(report, CriteriaReport)
        assert report.max_score == 4
        assert ("1", "Проект") in captured["sections"]
        assert ("2", ["LO"]) in captured["sections"]
        assert ("3", "Проект") in captured["sections"]
        assert ("4", "Проект") in captured["sections"]

    def test_build_report_turns_soft_failures_into_warnings(self):
        """Soft rubric failures are diagnostic warnings, not blocking score losses."""
        report = RubricScorer._build_report([
            CriteriaItem(
                id="1.1",
                title="Структура README",
                description="Обязательная структура",
                check_method=CheckMethod.SCRIPT,
                score=0,
                comments=["Нет Главы 2"],
                strictness=StrictnessLevel.HARD,
            ),
            CriteriaItem(
                id="2.4.2",
                title="Проверка смысловой точности названий подразделов",
                description="Каждый подраздел имеет тематическое название",
                check_method=CheckMethod.AI_AGENT,
                score=0,
                comments=["Некоторые названия требуют ручной проверки"],
                strictness=StrictnessLevel.SOFT,
            ),
            CriteriaItem(
                id="3.2",
                title="Проверка единого нарративного фокуса",
                description="Весь проект сохраняет единый контекст",
                check_method=CheckMethod.HYBRID,
                score=0,
                comments=["Внешний пример может доминировать над основным кейсом"],
            ),
        ])

        hard, soft, semantic = report.items
        assert hard.score == 0
        assert soft.score == 1
        assert semantic.score == 1
        assert soft.strictness == StrictnessLevel.SOFT
        assert semantic.strictness == StrictnessLevel.SOFT
        assert soft.comments[0].startswith("Предупреждение:")
        assert semantic.details["blocking"] is False
        assert report.total == 2

        exported = criteria_to_json(report)
        exported_by_id = {item["id"]: item for item in exported["items"]}
        assert exported_by_id["1.1"]["status"] == "failed"
        assert exported_by_id["2.4.2"]["status"] == "warning"
        assert exported_by_id["3.2"]["status"] == "warning"

