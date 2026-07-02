"""Тесты валидатора LaTeX-формул."""

from content_gen.utils.latex_validator import build_latex_agent_hint, collect_latex_issues


class TestLatexValidator:
    def test_valid_block_formula(self):
        md = "Текст\n\n$$ E = mc^2 $$\n\nПродолжение."
        assert collect_latex_issues(md) == []

    def test_unmatched_double_dollar(self):
        md = "Начало $$ a + b"
        issues = collect_latex_issues(md)
        assert issues
        assert any("нечётное" in issue.lower() for issue in issues)

    def test_empty_formula_body(self):
        md = "Текст\n\n$$$$\n\n"
        issues = collect_latex_issues(md)
        assert issues
        assert any("пуста" in issue.lower() for issue in issues)

    def test_placeholder_detected(self):
        md = "FORMULA_BLOCK_3 остаётся."
        issues = collect_latex_issues(md)
        assert issues
        assert any("плейсхолдер" in issue.lower() for issue in issues)

    def test_agent_hint_for_placeholders(self):
        issues = ["Обнаружены плейсхолдеры"]
        hint = build_latex_agent_hint(issues)
        assert "маркер" in hint.lower()

    def test_agent_hint_default(self):
        hint = build_latex_agent_hint([])
        assert "формулы" in hint.lower()

