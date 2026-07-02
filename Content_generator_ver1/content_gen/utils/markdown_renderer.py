"""
content_gen/utils/markdown_renderer.py

Универсальный рендер Markdown-блоков:
- Mermaid (Markdown-блоки → UI рендерит SVG)
- Формулы (LaTeX)
- Таблицы (Markdown)
- Подписи блоков

Гибридный режим:
- HTML допускается (unsafe_allow_html=True в Streamlit)
- Mermaid остаётся в Markdown, UI обрабатывает отдельно
"""

import json

# ============================================================
#  ОБЩИЕ ВСПОМОГАТЕЛЬНЫЕ ШАБЛОНЫ
# ============================================================

_CENTER_WRAPPER_START = (
    "<div style='display:flex;justify-content:center;margin:20px 0;'>"
    "<div style='max-width:100%;'>"
)

_CENTER_WRAPPER_END = "</div></div>"

_CAPTION_TMPL = (
    "<p style='text-align:center;font-style:italic;margin-top:8px;'>{}</p>"
)

# ============================================================
#  MERMAID (Markdown → UI рендерит SVG)
# ============================================================

MERMAID_THEME: dict[str, object] = {
    "theme": "base",
    "flowchart": {
        "htmlLabels": True,
        "curve": "basis",
        "padding": 18,
        "nodeSpacing": 68,
        "rankSpacing": 82,
        "wrappingWidth": 230,
        "useMaxWidth": True,
    },
    "themeVariables": {
        "primaryColor": "#ffffff",
        "primaryTextColor": "#111820",
        "primaryBorderColor": "#9aa79d",
        "lineColor": "#334238",
        "secondaryColor": "#eef4ef",
        "tertiaryColor": "#f7faf6",
        "background": "#ffffff",
        "mainBkg": "#ffffff",
        "secondBkg": "#eef4ef",
        "textColor": "#111820",
        "border1": "#9aa79d",
        "border2": "#7f8d83",
        "arrowheadColor": "#334238",
        "edgeLabelBackground": "#ffffff",
        "actorBkg": "#ffffff",
        "actorBorder": "#9aa79d",
        "actorTextColor": "#111820",
        "actorLineColor": "#334238",
        "signalColor": "#334238",
        "signalTextColor": "#111820",
        "labelBoxBkgColor": "#ffffff",
        "labelBoxBorderColor": "#9aa79d",
        "noteBkgColor": "#f7faf6",
        "noteTextColor": "#111820",
        "activationBkgColor": "#eef4ef",
        "activationBorderColor": "#9aa79d",
        "fontSize": "18px",
        "fontFamily": "-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Arial, sans-serif",
    },
    "scale": 1.0,
}

def _mermaid_theme_json() -> str:
    """Генерирует компактную JSON-строку Mermaid init без лишних пробелов."""
    return json.dumps(MERMAID_THEME, separators=(",", ":"))

def _render_mermaid_block(
    code: str,
    label: str | None = None,
    description: str | None = None,
) -> str:
    """
    Общая реализация Mermaid-блока.
    - code: чистый mermaid-код (без ```mermaid)
    - label: заголовок над диаграммой (жирный)
    - description: подпись под диаграммой (курсив)
    """
    code = (code or "").strip()
    theme = _mermaid_theme_json()
    parts: list[str] = []

    # Обертка (центрирование + ограничение ширины)
    parts.append(_CENTER_WRAPPER_START)

    # Заголовок (если есть)
    if label:
        parts.append(f"<p style='text-align:center;font-weight:bold;margin-bottom:8px;'>{label}</p>")

    # Markdown-блок mermaid — UI сам превратит в SVG
    parts.append("\n```mermaid")
    parts.append(f"%%{{init:{theme}}}%%")
    parts.append(code)
    parts.append("```")

    # Подпись (если есть)
    if description:
        parts.append("\n" + _CAPTION_TMPL.format(description))

    parts.append(_CENTER_WRAPPER_END)

    # Склеиваем с одинарными переводами строк, без лишних пустых блоков
    return "\n".join(parts) + "\n"

def render_mermaid_md_block(code: str, label: str | None = None) -> str:
    """
    Короткий рендер для Mermaid в Markdown:
    - НЕ генерируем <img>, это делает UI (frontend)
    - только корректный mermaid-блок + при необходимости подпись снизу курсивом
    label здесь используется как подпись под диаграммой (краткое описание).
    """
    return _render_mermaid_block(code=code, label=None, description=label)

def render_mermaid(
    label: str | None,
    code: str,
    description: str | None = None,
) -> str:
    """
    Более подробный Mermaid-блок (для агентов):
    - label — заголовок над диаграммой (жирный), может быть None
    - code — mermaid-код
    - description — подпись под диаграммой (курсив)
    """
    return _render_mermaid_block(code=code, label=label, description=description)

# ============================================================
#  FORMULAS (LaTeX / MathJax)
# ============================================================

def render_latex_block(formula: str) -> str:
    """
    Центрированная LaTeX формула в виде блочного Markdown:
    $$ 
    formula 
    $$
    Обертка нужна только для внешнего отступа, центрирование делает MathJax/Markdown.
    """
    formula = (formula or "").strip()
    parts: list[str] = [
        _CENTER_WRAPPER_START,
        "",
        "$$",
        formula,
        "$$",
        "",
        _CENTER_WRAPPER_END,
    ]
    return "\n".join(parts) + "\n"

def render_formula(
    label: str,
    latex: str,
    parameters: list[dict[str, str]] | None = None,
    description: str | None = None,
) -> str:
    """
    Формула с заголовком и списком параметров (центрированная).
    Структура:
    - жирный заголовок
    - блочная формула $$ ... $$
    - список параметров (каждый: - $symbol$ — description)
    - дополнительное текстовое описание
    """
    latex = (latex or "").strip()
    block_parts: list[str] = []

    # Заголовок
    block_parts.append(f"**{label}**\n")

    # Чистый блочный LaTeX — без HTML-оберток,
    # чтобы фронт (MathJax) уверенно его поймал
    block_parts.append("$$")
    block_parts.append(latex)
    block_parts.append("$$\n")

    # Параметры
    if parameters:
        # Используем HTML-список, чтобы избежать странностей разметки
        block_parts.append("<ul>")
        for p in parameters:
            symbol = p.get("symbol", "").strip()
            desc = p.get("description", "").strip()
            if not symbol and not desc:
                continue
            # Рендерим символ как inline-LaTeX, чтобы не было «сырого» \bar{Y}_{boot}
            symbol_tex = symbol
            if symbol_tex and not symbol_tex.startswith("$") and not symbol_tex.endswith("$"):
                symbol_tex = f"${symbol_tex}$"
            block_parts.append(f"<li><strong>{symbol_tex}</strong> — {desc}</li>")
        block_parts.append("</ul>\n")

    # Описание
    if description:
        block_parts.append(description.strip())
        block_parts.append("")

    return "\n".join(block_parts).rstrip() + "\n"

# ============================================================
#  TABLES (Markdown)
# ============================================================

def render_table_md(headers: list[str], rows: list[list[str]]) -> str:
    """
    Обычная Markdown-таблица (без HTML).
    Streamlit конвертирует её в <table> автоматически.
    """
    if not headers:
        return ""

    headers = [str(h).strip() for h in headers]
    normalized_rows = [[str(c).strip() for c in row] for row in rows]

    head = " | ".join(headers)
    sep = " | ".join(["---"] * len(headers))
    body = "\n".join(" | ".join(row) for row in normalized_rows)

    return f"{head}\n{sep}\n{body}\n"

def render_table(label: str, md_table: str, description: str | None = None) -> str:
    """
    Таблица с заголовком и подписью, оформленная чистым Markdown.
    HTML-обертки не используются, чтобы не ломать Markdown-парсинг таблицы.
    """
    md_table = (md_table or "").strip()
    if not md_table:
        return ""

    parts: list[str] = [f"**{label}**", "", md_table]

    if description:
        parts.extend(["", f"*{description.strip()}*"])

    return "\n".join(parts).strip() + "\n\n"
