import pytest

from content_gen.utils import mermaid_export
from content_gen.utils.markdown_renderer import _mermaid_theme_json


MERMAID_MD = """Перед диаграммой.

```mermaid
flowchart TD
    A[Start] --> B[Done]
```

После диаграммы.
"""


def test_convert_mermaid_blocks_keeps_source_by_default(monkeypatch) -> None:
    monkeypatch.delenv("MERMAID_EXPORT_MODE", raising=False)

    updated, assets = mermaid_export.convert_mermaid_blocks(MERMAID_MD)

    assert updated == MERMAID_MD
    assert assets == []


def test_convert_mermaid_blocks_preserves_source_when_renderer_fails(monkeypatch) -> None:
    monkeypatch.setenv("MERMAID_EXPORT_MODE", "kroki")

    def fail_render(_code: str, *, mode: str = "auto") -> bytes:
        raise TimeoutError("renderer timeout")

    monkeypatch.setattr(mermaid_export, "_render_mermaid", fail_render)

    updated, assets = mermaid_export.convert_mermaid_blocks(MERMAID_MD)

    assert updated == MERMAID_MD
    assert assets == []


def test_convert_mermaid_blocks_preflights_missing_local_cli(monkeypatch) -> None:
    monkeypatch.setenv("MERMAID_EXPORT_MODE", "local")
    monkeypatch.delenv("MERMAID_CLI_PATH", raising=False)
    monkeypatch.setattr(mermaid_export.shutil, "which", lambda _name: None)

    def fail_render(_code: str, *, mode: str = "auto") -> bytes:
        raise AssertionError("local renderer must not be called when mmdc is absent")

    monkeypatch.setattr(mermaid_export, "_render_mermaid", fail_render)

    updated, assets = mermaid_export.convert_mermaid_blocks(MERMAID_MD)

    assert updated == MERMAID_MD
    assert assets == []


def test_convert_mermaid_blocks_can_export_png_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("MERMAID_EXPORT_MODE", "kroki")
    captured = {}

    def render(_code: str, *, mode: str = "auto") -> bytes:
        captured["mode"] = mode
        return b"png-bytes"

    monkeypatch.setattr(mermaid_export, "_render_mermaid", render)

    updated, assets = mermaid_export.convert_mermaid_blocks(MERMAID_MD)

    assert "![Диаграмма 1](images/diagram_1.png)" in updated
    assert "```mermaid" not in updated
    assert assets == [{"name": "diagram_1.png", "data": b"png-bytes"}]
    assert captured["mode"] == "kroki"


def test_export_theme_matches_light_product_style() -> None:
    themed = mermaid_export._ensure_theme('%%{init:{"theme":"dark"}}%%\nflowchart TD\nA --> B')

    assert '"theme":"base"' in themed
    assert '"primaryColor":"#ffffff"' in themed
    assert '"lineColor":"#334238"' in themed
    assert '"fontSize":"18px"' in themed
    assert '"wrappingWidth":230' in themed
    assert '"theme":"dark"' not in themed
    assert "#0a0e27" not in themed
    assert themed.count("flowchart TD") == 1


def test_markdown_mermaid_theme_matches_light_product_style() -> None:
    theme = _mermaid_theme_json()

    assert '"theme":"base"' in theme
    assert '"primaryColor":"#ffffff"' in theme
    assert '"lineColor":"#334238"' in theme
    assert '"fontSize":"18px"' in theme
    assert '"wrappingWidth":230' in theme
    assert '"theme":"dark"' not in theme


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("off", "none"),
        ("remote", "kroki"),
        ("best_effort", "auto"),
        ("unknown", "none"),
    ],
)
def test_normalize_export_mode(raw: str, expected: str) -> None:
    assert mermaid_export._normalize_export_mode(raw) == expected
