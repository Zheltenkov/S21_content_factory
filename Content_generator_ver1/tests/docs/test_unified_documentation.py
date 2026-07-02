import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOC_PATH = ROOT / "docs" / "CONTENT_GENERATOR_UNIFIED.html"


def _visible_text(html: str) -> str:
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip().lower()


def test_unified_html_documentation_exists_and_covers_product_flows() -> None:
    html = DOC_PATH.read_text(encoding="utf-8")
    text = _visible_text(html)

    assert "<!doctype html>" in html.lower()
    for phrase in (
        "техническая документация",
        "внутренняя ии-модель генерации",
        "цель ии-контура",
        "архитектурные принципы",
        "схема работы ии-контура",
        "входной контракт ии",
        "общее состояние проекта",
        "цепочка генерации учебного проекта",
        "роли ии",
        "вызов языковой модели",
        "подсказки модели",
        "структурированный ответ",
        "проверки и безопасные исправления",
        "методологический режим",
        "точечная правка готового readme",
        "генерация check-list.yml",
        "проверка readme по критериям",
        "перевод документов и видео",
        "наблюдаемость ии-контура",
        "ошибки и восстановление",
        "наборы проверок качества ии",
    ):
        assert phrase in text


def test_unified_html_documentation_uses_user_facing_language() -> None:
    text = f" {_visible_text(DOC_PATH.read_text(encoding='utf-8'))} "

    for forbidden in (
        " gate ",
        " curriculum ",
        " workflow ",
        " endpoint ",
        " runtime ",
        " pipeline ",
        " checkpoint ",
        " node ",
        " риск ",
        " риски ",
        " рисков ",
    ):
        assert forbidden not in text

    for expected in (
        "учебный план",
        "контрольная точка",
        "точечная правка",
        "проверка критериев",
        "архитектурные принципы",
    ):
        assert expected in text
