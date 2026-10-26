"""Комментарии в коде: перевод текста, который на работу программы не влияет.

Модель не получает код на правку. Ни строчки. Она видит список комментариев и возвращает их
перевод; файл меняет код.

Надёжность держится на проверке после подмены: результат заново разбирается на токены и
сличается с исходником. Совпало всё, кроме комментариев, — файл принят. Не совпало — возвращается
исходный файл. Поэтому разбор может ошибиться и пропустить комментарий, но не может испортить
код: он либо верен, либо отказ.

Сличение идёт по токенам, а не по координатам. Сравнение «вырежем по тем же местам и сверим»
ничего бы не доказывало: если разбор принял за комментарий кусок кода, вырезано будет одинаково
в обоих, и подмена пройдёт незамеченной.

Python разбирает ``tokenize`` из стандартной библиотеки — он знает и строки, и f-строки, и
вложенные кавычки, поэтому ``"# это не комментарий"`` комментарием не станет. Строка документации
это не комментарий, а первая строка-выражение, поэтому ищется отдельно.
"""

from __future__ import annotations

import ast
import io
import tokenize

from content_factory.translation.errors import DocumentFormatError
from content_factory.translation.units import TextUnit

# Потолок: столько пояснений в одном файле не бывает осмысленным.
MAX_UNITS = 5_000


class PythonCommentHandler:
    """Переводит комментарии и строки документации Python."""

    extensions = frozenset({".py"})

    def extract(self, content: bytes) -> list[TextUnit]:
        text = _decode(content)
        return [
            TextUnit(unit_id=unit_id, text=value, hint=hint)
            for unit_id, _start, _end, value, hint in _spans(text)
        ]

    def apply(self, content: bytes, translations: dict[str, str]) -> bytes:
        text = _decode(content)
        pieces: list[str] = []
        cursor = 0
        changed = False
        for unit_id, start, end, _value, hint in _spans(text):
            translated = translations.get(unit_id)
            if translated is None:
                continue
            pieces.append(text[cursor:start])
            pieces.append(_render(text[start:end], translated, hint))
            cursor = end
            changed = True
        if not changed:
            return content
        pieces.append(text[cursor:])
        result = "".join(pieces)

        if not _same_code(text, result):
            # Разбор ошибся: лучше непереведённый файл, чем сломанный.
            return content
        return result.encode("utf-8")


def _decode(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentFormatError("Файл кода не в UTF-8") from exc


def _spans(text: str) -> list[tuple[str, int, int, str, str]]:
    """Комментарии и строки документации: ``(ключ, начало, конец, текст, вид)``."""
    found: list[tuple[str, int, int, str, str]] = []
    offsets = _line_offsets(text)
    counter = 0

    def add(start: int, end: int, value: str, hint: str) -> None:
        nonlocal counter
        if not value.strip():
            return
        counter += 1
        if counter > MAX_UNITS:
            raise DocumentFormatError(f"В файле больше {MAX_UNITS} пояснений")
        found.append((f"{counter:04d}", start, end, value, hint))

    for token in _tokens(text):
        if token.type != tokenize.COMMENT:
            continue
        start = offsets[token.start[0] - 1] + token.start[1]
        end = offsets[token.end[0] - 1] + token.end[1]
        body = text[start:end]
        if body.startswith("#!") and start == 0:
            continue  # строка запуска — это не пояснение
        inner = body[1:]
        lead = len(inner) - len(inner.lstrip())
        add(start + 1 + lead, end, inner.strip(), "комментарий")

    for start, end, value in _docstrings(text, offsets):
        add(start, end, value, "строка документации")

    found.sort(key=lambda item: item[1])
    return [(f"{index + 1:04d}", *rest[1:]) for index, rest in enumerate(found)]


def _tokens(text: str) -> list[tokenize.TokenInfo]:
    try:
        return list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        raise DocumentFormatError(f"Не удалось разобрать файл Python: {exc}") from exc


def _docstrings(text: str, offsets: list[int]) -> list[tuple[int, int, str]]:
    """Строки документации модуля, классов и функций — по дереву, а не по токенам."""
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise DocumentFormatError(f"Не удалось разобрать файл Python: {exc}") from exc

    found: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = getattr(node, "body", [])
        if not body or not isinstance(body[0], ast.Expr):
            continue
        literal = body[0].value
        if not isinstance(literal, ast.Constant) or not isinstance(literal.value, str):
            continue
        if literal.end_lineno is None or literal.end_col_offset is None:
            continue
        # Столбцы в дереве Python считаются в байтах UTF-8, а не в символах: строка
        # документации с кириллицей иначе вырезается не по тем границам.
        start = _char_position(text, offsets, literal.lineno, literal.col_offset)
        end = _char_position(text, offsets, literal.end_lineno, literal.end_col_offset)
        raw = text[start:end]
        inner_start, inner_end = _inside_quotes(raw)
        if inner_start is None or inner_end is None:
            continue
        found.append((start + inner_start, start + inner_end, raw[inner_start:inner_end]))
    return found


def _char_position(text: str, offsets: list[int], lineno: int, byte_column: int) -> int:
    """Переводит координату дерева (строка, столбец в байтах) в позицию символа."""
    line_start = offsets[lineno - 1]
    line = text[line_start : offsets[lineno]] if lineno < len(offsets) else text[line_start:]
    prefix = line.encode("utf-8")[:byte_column].decode("utf-8", errors="ignore")
    return line_start + len(prefix)


def _inside_quotes(raw: str) -> tuple[int | None, int | None]:
    """Границы содержимого строкового литерала без кавычек и префиксов."""
    for quote in ('"""', "'''", '"', "'"):
        opening = raw.find(quote)
        if opening < 0:
            continue
        start = opening + len(quote)
        if raw.endswith(quote) and len(raw) - len(quote) >= start:
            return start, len(raw) - len(quote)
    return None, None


def _render(original: str, translated: str, hint: str) -> str:
    """Перевод на месте куска: отступ и хвостовые пробелы сохраняются."""
    head = original[: len(original) - len(original.lstrip())]
    tail = original[len(original.rstrip()) :]
    body = translated.replace("\\", "\\\\") if hint == "строка документации" else translated
    return f"{head}{body}{tail}"


def _same_code(before: str, after: str) -> bool:
    """Совпадает ли всё, кроме комментариев и строк документации."""
    try:
        return _code_shape(before) == _code_shape(after)
    except DocumentFormatError:
        return False


def _code_shape(text: str) -> list[tuple[int, str]]:
    shape: list[tuple[int, str]] = []
    docstring_ranges = [(start, end) for start, end, _value in _docstrings(text, _line_offsets(text))]
    offsets = _line_offsets(text)
    for token in _tokens(text):
        if token.type in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.ENCODING):
            continue
        start = offsets[token.start[0] - 1] + token.start[1]
        if token.type == tokenize.STRING and any(a <= start < b or start < a < token.end[1] + start for a, b in docstring_ranges):
            shape.append((token.type, "<строка документации>"))
            continue
        shape.append((token.type, token.string))
    return shape


def _line_offsets(text: str) -> list[int]:
    """Смещение начала каждой строки — токены приходят в координатах «строка, столбец»."""
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets
