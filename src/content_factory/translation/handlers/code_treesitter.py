"""Комментарии в коде — через настоящие грамматики языков.

Заменяет шесть ручных сканеров, которые писались под каждый язык отдельно. За время работы над
ними автоматический разбор безопасности нашёл шесть расхождений подряд, и список выглядел так:
``${x:- # текст}`` в bash, регулярные выражения и шаблонные строки в JavaScript, разделитель
разрядов ``1'000'000`` в C++, сырые строки C++ и Go, ``\\u002f`` в Java, диалекты экранирования
SQL. Каждое чинилось отказом от сомнительного случая, и каждый заход находил следующее.

Грамматика закрывает весь класс разом: разбирает не догадка, а сам язык.

Надёжность теперь настоящая, а не круговая. Прежняя проверка сличала результат тем же сканером,
который нашёл комментарии, поэтому систематическая ошибка оставалась невидимой. Здесь результат
разбирается заново и сравниваются **все листья дерева, кроме комментариев**: совпали — файл
принят, нет — возвращается исходный. Ошибка грамматики на файле (``has_error``) тоже означает
отказ: гадать о сломанном коде незачем.

Работаем в байтах: tree-sitter отдаёт смещения в UTF-8, и пересчёт в символы — лишний источник
ошибок. На строках документации Python это уже один раз стоило разбора.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from content_factory.translation.errors import DocumentFormatError
from content_factory.translation.units import TextUnit

MAX_UNITS = 5_000
# Длина файла, выше которой за разбор не беремся: грамматика строит дерево в памяти.
MAX_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class _Comment:
    start: int
    end: int
    text: str


class TreeSitterCommentHandler:
    """Переводит комментарии языка, разбирая его настоящей грамматикой."""

    offered_on_page = False

    def __init__(
        self,
        language: str,
        extensions: frozenset[str],
        refuse_if_contains: tuple[bytes, ...] = (),
    ) -> None:
        self.language = language
        self.extensions = extensions
        # Редкий случай, когда грамматика расходится с компилятором по замыслу языка.
        self.refuse_if_contains = refuse_if_contains

    def extract(self, content: bytes) -> list[TextUnit]:
        return [
            TextUnit(unit_id=f"{index + 1:04d}", text=comment.text, hint="комментарий")
            for index, comment in enumerate(self._comments(content))
        ]

    def apply(self, content: bytes, translations: dict[str, str]) -> bytes:
        comments = self._comments(content)
        pieces: list[bytes] = []
        cursor = 0
        changed = False
        for index, comment in enumerate(comments):
            translated = translations.get(f"{index + 1:04d}")
            if translated is None:
                continue
            # Перенос строки оборвал бы строчный комментарий и сделал остаток кодом.
            safe = " ".join(translated.split())
            pieces.append(content[cursor : comment.start])
            pieces.append(safe.encode("utf-8"))
            cursor = comment.end
            changed = True
        if not changed:
            return content
        pieces.append(content[cursor:])
        result = b"".join(pieces)

        if self._code_shape(result) != self._code_shape(content):
            # Перевод изменил не только комментарии — возвращаем файл нетронутым.
            return content
        return result

    def _parse(self, content: bytes) -> Any:
        if len(content) > MAX_BYTES:
            raise DocumentFormatError(
                f"Файл кода больше {MAX_BYTES} байт — такой не разбираем"
            )
        try:
            from tree_sitter_language_pack import get_parser
        except ImportError as exc:  # pragma: no cover - зависимость есть в requirements
            raise DocumentFormatError("Не установлены грамматики языков (tree-sitter)") from exc
        return get_parser(self.language).parse(content)

    def _comments(self, content: bytes) -> list[_Comment]:
        if any(mark in content for mark in self.refuse_if_contains):
            return []
        tree = self._parse(content)
        if tree.root_node.has_error:
            return []  # файл не разбирается: трогать его нельзя

        found: list[_Comment] = []
        for node in _walk(tree.root_node):
            if "comment" not in node.type:
                continue
            raw = content[node.start_byte : node.end_byte]
            if node.start_byte == 0 and raw.startswith(b"#!"):
                continue  # строка запуска — не пояснение
            opening, closing = _markers(raw)
            body = raw[len(opening) : len(raw) - len(closing)]
            stripped = body.strip()
            if not stripped:
                continue
            if len(found) >= MAX_UNITS:
                break
            lead = len(body) - len(body.lstrip())
            tail = len(body) - len(body.rstrip())
            found.append(
                _Comment(
                    start=node.start_byte + len(opening) + lead,
                    end=node.end_byte - len(closing) - tail,
                    text=stripped.decode("utf-8", errors="replace"),
                )
            )
        return found

    def _code_shape(self, content: bytes) -> list[tuple[str, bytes]] | None:
        """Все листья дерева, кроме комментариев. ``None`` — файл не разбирается."""
        tree = self._parse(content)
        if tree.root_node.has_error:
            return None
        shape: list[tuple[str, bytes]] = []
        for node in _walk(tree.root_node):
            if "comment" in node.type:
                continue
            if node.child_count == 0:
                shape.append((node.type, content[node.start_byte : node.end_byte]))
        return shape


def _walk(node: Any) -> Iterator[Any]:
    """Узлы дерева сверху вниз; внутрь комментария не заходим."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        if "comment" in current.type:
            continue
        stack.extend(reversed(current.children))


def _markers(raw: bytes) -> tuple[bytes, bytes]:
    """Открывающая и закрывающая части пояснения — всё в байтах, без пересчёта в символы."""
    if raw.startswith(b"/*"):
        return b"/*", b"*/" if raw.endswith(b"*/") else b""
    for opening in (b"//", b"--", b"#"):
        if raw.startswith(opening):
            return opening, b""
    return b"", b""


def shell_handler() -> TreeSitterCommentHandler:
    return TreeSitterCommentHandler("bash", frozenset({".sh", ".bash"}))


def javascript_handler() -> TreeSitterCommentHandler:
    return TreeSitterCommentHandler("javascript", frozenset({".js", ".mjs", ".cjs"}))


def typescript_handler() -> TreeSitterCommentHandler:
    return TreeSitterCommentHandler("typescript", frozenset({".ts"}))


def c_handler() -> TreeSitterCommentHandler:
    return TreeSitterCommentHandler("c", frozenset({".c", ".h"}))


def cpp_handler() -> TreeSitterCommentHandler:
    return TreeSitterCommentHandler("cpp", frozenset({".cpp", ".cc", ".hpp"}))


def go_handler() -> TreeSitterCommentHandler:
    return TreeSitterCommentHandler("go", frozenset({".go"}))


def java_handler() -> TreeSitterCommentHandler:
    """Java — единственный язык здесь, где грамматика расходится с компилятором.

    Компилятор разворачивает экранирование вида u-код ДО разбора, поэтому строка из одного
    такого экранирования для него лишняя кавычка, а для грамматики — обычная строка. Файл
    с таким экранированием может значить не то, что видно, и мы за него не беремся.
    В современном коде это редкость: строки пишут прямо в UTF-8.
    """
    return TreeSitterCommentHandler(
        "java", frozenset({".java"}), refuse_if_contains=(bytes([92, 117]),)
    )


def sql_handler() -> TreeSitterCommentHandler:
    return TreeSitterCommentHandler("sql", frozenset({".sql"}))
