from pathlib import Path

from content_audit.code_similarity import build_code_similarity_index
from content_audit.domain import ContentUnit


def _unit(root: Path, unit_id: str) -> ContentUnit:
    return ContentUnit(unit_id=unit_id, name=unit_id, root_path=root, relative_path=unit_id)


def test_code_similarity_index_detects_copied_code(workspace_tmp_path: Path) -> None:
    first = workspace_tmp_path / "first"
    second = workspace_tmp_path / "second"
    first.mkdir()
    second.mkdir()
    code = """
def normalize_value(value):
    value = value.strip().lower()
    return value.replace(' ', '-')

def build_slug(parts):
    return '-'.join(normalize_value(part) for part in parts)
"""
    (first / "main.py").write_text(code, encoding="utf-8")
    (second / "solution.py").write_text(code.replace("build_slug", "make_slug"), encoding="utf-8")

    index = build_code_similarity_index([_unit(first, "first"), _unit(second, "second")], threshold=0.5)

    assert index["first"][0].other_unit_id == "second"
    assert index["first"][0].similarity >= 0.5


def test_code_similarity_index_marks_attributed_units(workspace_tmp_path: Path) -> None:
    first = workspace_tmp_path / "first"
    second = workspace_tmp_path / "second"
    first.mkdir()
    second.mkdir()
    code = "def calculate_total(items):\n    return sum(item.price for item in items)\n"
    (first / "main.py").write_text("# source: https://example.com/snippet\n" + code, encoding="utf-8")
    (second / "main.py").write_text(code, encoding="utf-8")

    index = build_code_similarity_index([_unit(first, "first"), _unit(second, "second")], threshold=0.5)

    assert index["first"][0].attributed is True
