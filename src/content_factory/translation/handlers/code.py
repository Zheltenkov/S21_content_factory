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
import re
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


class ShellCommentHandler:
    """Переводит комментарии shell-скриптов.

    Решётка начинает комментарий не везде: внутри кавычек это обычный знак, в ``${#array}`` —
    длина, в строке запуска — не пояснение, а внутри heredoc вообще текст, который уйдёт в
    программу. Поэтому разбор идёт посимвольно с памятью о состоянии.
    """

    extensions = frozenset({".sh", ".bash"})
    # Конструкции, которые разбор не моделирует. Встретив их перед решёткой в той же строке,
    # он не берётся судить, пояснение это или код, и пропускает строку целиком.
    UNMODELLED = ("`", "$(")
    # Знаки, которыми можно выйти из пояснения в код. В переводе прозы им взяться неоткуда,
    # поэтому их появление означает либо ошибку разбора, либо попытку что-то подсунуть.
    SHELL_SYNTAX = tuple("`$;|&(){}<>'\"\\")

    def extract(self, content: bytes) -> list[TextUnit]:
        text = _decode(content)
        return [
            TextUnit(unit_id=f"{index + 1:04d}", text=value, hint="комментарий")
            for index, (_start, _end, value) in enumerate(_shell_comments(text))
        ]

    def apply(self, content: bytes, translations: dict[str, str]) -> bytes:
        text = _decode(content)
        spans = _shell_comments(text)
        pieces: list[str] = []
        cursor = 0
        changed = False
        for index, (start, end, _value) in enumerate(spans):
            translated = translations.get(f"{index + 1:04d}")
            if translated is None:
                continue
            pieces.append(text[cursor:start])
            # Перенос строки оборвал бы пояснение и сделал остаток командой.
            safe = " ".join(translated.split())
            original_body = text[start:end]
            if any(mark in safe for mark in self.SHELL_SYNTAX):
                # Управляющий знак в переводе — отказ независимо от того, был ли он в оригинале.
                # Исходное пояснение пишет тот, кто прислал архив: комментарий, составленный как
                # внушение модели, иначе служил бы сам себе разрешением.
                safe = original_body
            pieces.append(safe)
            cursor = end
            changed = True
        if not changed:
            return content
        pieces.append(text[cursor:])
        result = "".join(pieces)

        if _without_comments(result) != _without_comments(text):
            return content  # разбор сдвинулся: лучше непереведённый файл, чем сломанный
        return result.encode("utf-8")


def _without_comments(text: str) -> str:
    """Остаток файла, если вырезать пояснения — по свежему разбору, а не по прежним границам."""
    pieces: list[str] = []
    cursor = 0
    for start, end, _value in _shell_comments(text):
        pieces.append(text[cursor:start])
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _shell_comments(text: str) -> list[tuple[int, int, str]]:
    """Границы пояснений: ``(начало текста, конец строки, текст)``."""
    found: list[tuple[int, int, str]] = []
    index = 0
    length = len(text)
    quote: str | None = None
    heredoc: str | None = None
    at_line_start = True
    # Глубина ``${...}``: внутри подстановки решётка — часть значения, а не начало пояснения.
    # ``echo ${x:- # текст}`` без этого съедался вместе с закрывающей скобкой.
    expansion = 0

    while index < length:
        char = text[index]

        if heredoc is not None:
            line_end = text.find("\n", index)
            line_end = length if line_end < 0 else line_end
            if text[index:line_end].strip() == heredoc:
                heredoc = None
            index = line_end + 1
            at_line_start = True
            continue

        if quote is not None:
            if char == "\\" and quote == '"':
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue

        if char in "'\"":
            quote = char
            index += 1
            at_line_start = False
            continue

        if char == "\\":
            index += 2
            continue

        if text.startswith("<<", index):
            marker = _heredoc_marker(text, index)
            if marker is not None:
                heredoc, index = marker
                continue

        if text.startswith("${", index):
            expansion += 1
            index += 2
            at_line_start = False
            continue

        if char == "{" and expansion > 0:
            # Простая скобка внутри подстановки тоже считается, иначе ``${x:-{a} # текст}``
            # закроет подстановку раньше времени и решётка снова станет «пояснением».
            expansion += 1
            index += 1
            at_line_start = False
            continue

        if char == "}" and expansion > 0:
            expansion -= 1
            index += 1
            at_line_start = False
            continue

        if char == "#" and expansion == 0 and _starts_comment(text, index, at_line_start):
            line_end = text.find("\n", index)
            line_end = length if line_end < 0 else line_end
            body = text[index + 1 : line_end]
            lead = len(body) - len(body.lstrip())
            if body.strip() and len(found) < MAX_UNITS and _line_is_plain(text, index):
                found.append((index + 1 + lead, line_end, body.strip()))
            index = line_end
            continue

        at_line_start = char == "\n" or (at_line_start and char in " \t")
        index += 1

    if quote is not None or heredoc is not None or expansion:
        # Разбор дошёл до конца файла внутри кавычки, heredoc или подстановки — значит он сбился
        # раньше, и ни одной найденной границе доверять нельзя.
        return []
    return found


def _line_is_plain(text: str, hash_index: int) -> bool:
    """Нет ли перед решёткой в этой строке того, чего разбор не моделирует."""
    line_start = text.rfind("\n", 0, hash_index) + 1
    prefix = text[line_start:hash_index]
    if not prefix.strip():
        return True  # пояснение с начала строки однозначно
    return not any(mark in prefix for mark in ShellCommentHandler.UNMODELLED)


def _starts_comment(text: str, index: int, at_line_start: bool) -> bool:
    """Решётка начинает пояснение, если перед ней начало строки или пробел."""
    if index == 0:
        return not text.startswith("#!")  # строка запуска
    if at_line_start:
        return True
    return text[index - 1] in " \t"


def _heredoc_marker(text: str, index: int) -> tuple[str, int] | None:
    """Разбирает ``<<EOF`` и ``<<-'EOF'``, возвращая метку конца и позицию после неё."""
    cursor = index + 2
    if cursor < len(text) and text[cursor] == "-":
        cursor += 1
    while cursor < len(text) and text[cursor] in " \t":
        cursor += 1
    opening = cursor
    if cursor < len(text) and text[cursor] in "'\"":
        quote = text[cursor]
        closing = text.find(quote, cursor + 1)
        if closing < 0:
            return None
        return text[cursor + 1 : closing], closing + 1
    while cursor < len(text) and (text[cursor].isalnum() or text[cursor] == "_"):
        cursor += 1
    if cursor == opening:
        return None
    return text[opening:cursor], cursor


class CStyleCommentHandler:
    """Переводит комментарии C-подобных языков: ``//`` до конца строки и ``/* */``.

    Разбор моделирует строки в кавычках, при ``backtick_strings`` — обратные кавычки (шаблонные
    строки JavaScript, сырые строки Go), экранирование обратным слешем и оба вида комментариев.

    Чего он не моделирует и где поэтому отказывается судить:

    * регулярные выражения JavaScript. Косая черта начинает и деление, и регулярное выражение, а
      внутри него ``//`` вовсе не комментарий. Различить это без полного разбора языка нельзя,
      поэтому при ``regex_literals`` строка, где перед предполагаемым комментарием встречается
      косая черта, пропускается целиком;
    * подстановки ``${...}`` внутри шаблонных строк: в них может быть вложена ещё одна шаблонная
      строка. Поэтому шаблонная строка со вложенной подстановкой обрывает разбор файла.

    Выход за конец файла внутри строки или незакрытого блока означает, что разбор сбился, — файл
    не отдаёт ни одной единицы.
    """

    # Знаки, которыми перевод может выйти из пояснения обратно в код. Обратный слеш в конце
    # строчного пояснения продолжает его на следующую строку и выключает её.
    FORBIDDEN_IN_TRANSLATION = ("\\", "`", '"', "'", "*/", "/*", "??/")

    def __init__(
        self,
        extensions: frozenset[str] | None = None,
        *,
        line_token: str = "//",
        backtick_strings: bool = True,
        backtick_escapes: bool = True,
        backslash_escapes: bool = True,
        doubled_quote_escapes: bool = False,
        regex_literals: bool = True,
        unmodelled: tuple[str, ...] = (),
        unmodelled_patterns: tuple[str, ...] = (),
    ) -> None:
        self.extensions = extensions or frozenset({".js", ".ts", ".mjs", ".cjs"})
        self.line_token = line_token
        self.backtick_strings = backtick_strings
        self.backtick_escapes = backtick_escapes
        self.backslash_escapes = backslash_escapes
        self.doubled_quote_escapes = doubled_quote_escapes
        self.regex_literals = regex_literals
        # Конструкции, которых разбор не знает вовсе: встретив любую, он за файл не берётся.
        self.unmodelled = unmodelled
        self.unmodelled_patterns = tuple(re.compile(pattern) for pattern in unmodelled_patterns)

    def extract(self, content: bytes) -> list[TextUnit]:
        text = _decode(content)
        return [
            TextUnit(unit_id=f"{index + 1:04d}", text=value, hint="комментарий")
            for index, (_start, _end, value, _block) in enumerate(self._comments(text))
        ]

    def apply(self, content: bytes, translations: dict[str, str]) -> bytes:
        text = _decode(content)
        pieces: list[str] = []
        cursor = 0
        changed = False
        for index, (start, end, _value, _block) in enumerate(self._comments(text)):
            translated = translations.get(f"{index + 1:04d}")
            if translated is None:
                continue
            safe = " ".join(translated.split())
            if any(mark in safe for mark in self.FORBIDDEN_IN_TRANSLATION):
                # Такой перевод вышел бы из пояснения обратно в код.
                safe = text[start:end]
            pieces.append(text[cursor:start])
            pieces.append(safe)
            cursor = end
            changed = True
        if not changed:
            return content
        pieces.append(text[cursor:])
        result = "".join(pieces)

        if self._without_comments(result) != self._without_comments(text):
            return content
        return result.encode("utf-8")

    def _without_comments(self, text: str) -> str:
        pieces: list[str] = []
        cursor = 0
        for start, end, _value, _block in self._comments(text):
            pieces.append(text[cursor:start])
            cursor = end
        pieces.append(text[cursor:])
        return "".join(pieces)

    def _comments(self, text: str) -> list[tuple[int, int, str, bool]]:
        """Пояснения: ``(начало текста, конец текста, текст, это блок)``."""
        if any(mark in text for mark in self.unmodelled):
            return []  # в файле конструкция, которой разбор не знает
        if any(pattern.search(text) for pattern in self.unmodelled_patterns):
            return []

        found: list[tuple[int, int, str, bool]] = []
        index = 0
        length = len(text)
        quote: str | None = None

        while index < length:
            char = text[index]

            if quote is not None:
                escapes = self.backtick_escapes if quote == "`" else self.backslash_escapes
                if char == "\\":
                    if escapes:
                        index += 2
                        continue
                    if quote != "`":
                        # Слеш в строке языка, где он не экранирует: диалекты расходятся
                        # (в MySQL экранирует, в стандарте SQL нет), угадывать нельзя.
                        return []
                if quote == "`" and self.backtick_strings and text.startswith("${", index):
                    return []  # вложенная подстановка — разбор не берётся
                if char == quote:
                    if self.doubled_quote_escapes and text.startswith(quote * 2, index):
                        index += 2  # удвоенная кавычка внутри строки, а не её конец
                        continue
                    quote = None
                index += 1
                continue

            if char in "'\"" or (char == "`" and self.backtick_strings):
                quote = char
                index += 1
                continue

            if text.startswith("/*", index):
                closing = text.find("*/", index + 2)
                if closing < 0:
                    return []  # незакрытый блок: разбор сбился
                body = text[index + 2 : closing]
                if body.strip() and len(found) < MAX_UNITS:
                    lead = len(body) - len(body.lstrip())
                    tail = len(body) - len(body.rstrip())
                    found.append((index + 2 + lead, closing - tail, body.strip(), True))
                index = closing + 2
                continue

            if text.startswith(self.line_token, index):
                line_end = text.find("\n", index)
                line_end = length if line_end < 0 else line_end
                body = text[index + len(self.line_token) : line_end]
                # Слеш в конце продолжает пояснение на следующую строку: её границы разбор
                # не знает, поэтому такой кусок не трогаем.
                continued = body.rstrip().endswith(("\\", "??/"))
                if body.strip() and not continued and len(found) < MAX_UNITS and self._line_is_plain(text, index):
                    lead = len(body) - len(body.lstrip())
                    tail = len(body) - len(body.rstrip())
                    found.append((index + len(self.line_token) + lead, line_end - tail, body.strip(), False))
                index = line_end
                continue

            index += 1

        if quote is not None:
            return []
        return found

    def _line_is_plain(self, text: str, token_index: int) -> bool:
        """Нет ли перед пояснением того, чего разбор не моделирует."""
        if not self.regex_literals:
            return True
        line_start = text.rfind("\n", 0, token_index) + 1
        prefix = text[line_start:token_index]
        if not prefix.strip():
            return True  # пояснение с начала строки однозначно
        return "/" not in prefix


class SqlCommentHandler(CStyleCommentHandler):
    """Переводит комментарии SQL: ``--`` до конца строки и ``/* */``.

    В SQL обратный слеш строку не экранирует — кавычку внутри удваивают.
    """

    def __init__(self) -> None:
        super().__init__(
            frozenset({".sql"}),
            line_token="--",
            backtick_strings=True,
            backtick_escapes=False,
            backslash_escapes=False,
            doubled_quote_escapes=True,
            regex_literals=False,
        )


class GoCommentHandler(CStyleCommentHandler):
    """Переводит комментарии Go. В сырой строке в обратных кавычках слеш не экранирует."""

    def __init__(self) -> None:
        super().__init__(
            frozenset({".go"}),
            backtick_strings=True,
            backtick_escapes=False,
            regex_literals=False,
        )


class CFamilyCommentHandler(CStyleCommentHandler):
    """Переводит комментарии C, C++, заголовков и Java.

    Сырые строки C++ (``R"(...)"``) и текстовые блоки Java (``\"\"\"``) разбор не моделирует:
    внутри них кавычки и косые черты ничего не значат, и ошибиться тут значит испортить код.
    Встретив такую конструкцию, он за файл не берётся.
    """

    def __init__(self) -> None:
        super().__init__(
            frozenset({".c", ".cpp", ".cc", ".h", ".hpp", ".java"}),
            backtick_strings=False,
            regex_literals=False,
            # Java разворачивает \u-экранирование до разбора: / это «/», и файл может
            # значить не то, что видно глазами.
            unmodelled=('R"', '"""', "\\u"),
            # ``1'000'000`` — разделитель разрядов C++14, а не символьный литерал.
            unmodelled_patterns=(r"[0-9a-fA-F]'[0-9]",),
        )
