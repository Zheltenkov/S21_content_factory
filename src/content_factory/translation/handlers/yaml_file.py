"""YAML: перевод строковых значений без пересборки файла.

Библиотека тут только читает. Если дать PyYAML собрать файл заново, пропадут комментарии и
блочные скаляры ``>``, которыми написаны чек-листы, а порядок ключей сохранится лишь по
случайности. Поэтому берутся координаты скаляров (``start_mark``/``end_mark``) и текст
подменяется прямо в исходнике, с конца к началу, чтобы не сбить смещения.

Что не переводим:

* ключи отображений — иначе файл перестанут читать те, кто его разбирает;
* всё, что не строка: числа, даты, ``true``/``null``;
* коды и опознаватели — скаляр без пробелов длиной до 20 знаков. В живых чек-листах рядом с
  прозой лежат ``EMPTY_WORK``, ``language: en``, ``kind: "1"``, и перевод любого из них ломает
  обработку файла.

Блочный скаляр ``>`` сворачивает строки абзаца в одну логическую, поэтому единицей берётся абзац
и возвращается одной строкой — смысл сохраняется, а разбивка по ширине меняется. У литерального
``|`` переносы значимы, там единица — строка.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import yaml

from content_factory.translation.errors import DocumentFormatError
from content_factory.translation.units import TextUnit

# Длина, ниже которой скаляр без пробелов считается кодом, а не текстом.
_CODE_MAX_LENGTH = 20
_STR_TAG = "tag:yaml.org,2002:str"


class YamlHandler:
    """Переводит строковые значения YAML, сохраняя стиль, порядок и комментарии."""

    extensions = frozenset({".yml", ".yaml"})

    def extract(self, content: bytes) -> list[TextUnit]:
        text = content.decode("utf-8")
        return [
            TextUnit(unit_id=unit_id, text=value, hint=hint)
            for unit_id, _start, _end, value, hint in _spans(text)
        ]

    def apply(self, content: bytes, translations: dict[str, str]) -> bytes:
        text = content.decode("utf-8")
        pieces = list(_spans(text))
        for unit_id, start, end, _value, _hint in reversed(pieces):
            translated = translations.get(unit_id)
            if translated is None:
                continue
            text = text[:start] + _render(text, start, end, translated) + text[end:]
        return text.encode("utf-8")


def _spans(text: str) -> list[tuple[str, int, int, str, str]]:
    """Переводимые куски: ``(ключ, начало, конец, текст, подсказка)`` в устойчивом порядке."""
    try:
        root = yaml.compose(text)
    except yaml.YAMLError as exc:
        raise DocumentFormatError(f"Не удалось разобрать YAML: {exc}") from exc
    if root is None:
        return []

    found: list[tuple[str, int, int, str, str]] = []
    counter = [0]

    def add(start: int, end: int, value: str, hint: str) -> None:
        counter[0] += 1
        found.append((f"{counter[0]:04d}", start, end, value, hint))

    def visit(node: yaml.Node) -> None:
        if isinstance(node, yaml.MappingNode):
            for _key, value in node.value:
                visit(value)  # ключи не переводим
        elif isinstance(node, yaml.SequenceNode):
            for item in node.value:
                visit(item)
        elif isinstance(node, yaml.ScalarNode):
            _visit_scalar(node, text, add)

    visit(root)
    return found


def _visit_scalar(node: yaml.ScalarNode, text: str, add: Callable[[int, int, str, str], None]) -> None:
    if node.tag != _STR_TAG:
        return
    start, end = node.start_mark.index, node.end_mark.index
    if node.style in (">", "|"):
        for piece_start, piece_end, piece in _block_pieces(text, start, end, node.style):
            if _is_translatable(piece):
                add(piece_start, piece_end, piece, "блок текста")
        return
    value = node.value
    if _is_translatable(value):
        add(start, end, value, "значение")


def _block_pieces(text: str, start: int, end: int, style: str) -> list[tuple[int, int, str]]:
    """Куски блочного скаляра: абзацы для ``>`` и отдельные строки для ``|``."""
    body_start = text.index("\n", start) + 1 if "\n" in text[start:end] else end
    lines: list[tuple[int, int, str]] = []
    cursor = body_start
    for raw_line in text[body_start:end].split("\n"):
        line_start = cursor
        cursor += len(raw_line) + 1
        if raw_line.strip():
            indent = len(raw_line) - len(raw_line.lstrip())
            lines.append((line_start + indent, line_start + len(raw_line), raw_line.strip()))

    if style == "|" or not lines:
        return lines

    # Свёрнутый блок: смежные непустые строки складываются в один абзац.
    paragraphs: list[tuple[int, int, str]] = []
    group_start, group_end, parts = lines[0][0], lines[0][1], [lines[0][2]]
    for piece_start, piece_end, piece in lines[1:]:
        if text[group_end:piece_start].count("\n") <= 1:
            group_end, parts = piece_end, [*parts, piece]
            continue
        paragraphs.append((group_start, group_end, " ".join(parts)))
        group_start, group_end, parts = piece_start, piece_end, [piece]
    paragraphs.append((group_start, group_end, " ".join(parts)))
    return paragraphs


def _render(text: str, start: int, end: int, translated: str) -> str:
    """Готовит перевод к вставке: кавычки — как были, отступ блока — как был."""
    original = text[start:end]
    if original.startswith('"') and original.endswith('"') and len(original) >= 2:
        escaped = translated.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if original.startswith("'") and original.endswith("'") and len(original) >= 2:
        return "'" + translated.replace("'", "''") + "'"
    indent = _line_indent(text, start)
    return translated.replace("\n", "\n" + " " * indent)


def _line_indent(text: str, position: int) -> int:
    """Отступ строки, в которой начинается кусок — чтобы многострочный перевод не развалил блок."""
    line_start = text.rfind("\n", 0, position) + 1
    return position - line_start


def _is_translatable(value: str) -> bool:
    """Текст для человека, а не код: пустое и короткое без пробелов пропускаем."""
    stripped = value.strip()
    if not stripped:
        return False
    if not re.search(r"\s", stripped) and len(stripped) <= _CODE_MAX_LENGTH:
        return False
    return bool(re.search(r"[^\W\d_]", stripped, re.UNICODE))
