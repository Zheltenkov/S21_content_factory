"""Перевод архива целиком: распаковка, опись, смета, упаковка.

Архив приходит снаружи, поэтому половина проверок здесь — про отказ. Проверки устроены так,
чтобы ловить не «вернулась ошибка», а **что именно** отвергнуто: путь наружу, ссылка, перебор
по числу файлов и по объёму. Разница важна: отказ по не той причине выглядит так же, как
правильный, и прячет настоящую дыру.
"""

from __future__ import annotations

import stat
import zipfile
from io import BytesIO

import pytest

from content_factory.translation.archive import (
    MAX_FILES,
    MAX_UNPACKED_BYTES,
    ArchiveMember,
    estimate_for,
    read_members,
    repack,
    safe_archive_path,
    take_inventory,
)
from content_factory.translation.errors import DocumentFormatError


def _zip(files: dict[str, bytes | str], *, symlinks: tuple[str, ...] = ()) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            data = payload.encode("utf-8") if isinstance(payload, str) else payload
            info = zipfile.ZipInfo("placeholder")
            # Имя ставится в обход конструктора: под Windows тот сам меняет ``\`` на ``/``,
            # и проверка обратной косой проверяла бы этот помощник, а не модуль.
            info.filename = name
            if name in symlinks:
                info.create_system = 3  # unix
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, data)
    return buffer.getvalue()


# ------------------------------------------------------------------ распаковка: что отвергаем

@pytest.mark.parametrize(
    ("name", "why"),
    [
        ("../снаружи.md", "путь наружу"),
        ("docs/../../снаружи.md", "путь наружу через середину"),
        ("/etc/passwd", "абсолютный путь"),
        ("C:/Windows/system.ini", "путь с буквой диска"),
        ("docs//внутри.md", "пустая часть пути"),
    ],
)
def test_a_path_that_leaves_the_archive_is_refused(name: str, why: str) -> None:
    with pytest.raises(DocumentFormatError) as info:
        read_members(_zip({name: "текст"}))

    assert name in str(info.value), f"сообщение должно называть файл ({why})"


def test_a_backslash_in_a_name_is_refused() -> None:
    """На Windows обратная косая — разделитель, и ``..\\`` там выводит за корень.

    Проверяется прямым вызовом, а не архивом: ``zipfile`` меняет косую на прямую и при записи,
    и при чтении, поэтому через архив проверялась бы эта нормализация. Проверка нужна сама по
    себе — без неё защита зависела бы от неё, а ``..\\x`` на части по ``/`` не распадается.
    """
    with pytest.raises(DocumentFormatError) as info:
        safe_archive_path(".." + chr(92) + "снаружи.md")

    assert "косую" in str(info.value)


def test_a_symlink_member_is_refused() -> None:
    """Ссылка указывает куда угодно: распаковав её, мы прочли бы файл вне архива."""
    content = _zip({"docs/ссылка": "/etc/passwd"}, symlinks=("docs/ссылка",))

    with pytest.raises(DocumentFormatError) as info:
        read_members(content)

    assert "ссылк" in str(info.value).lower()


def test_too_many_files_is_refused() -> None:
    content = _zip({f"f{index}.md": "текст" for index in range(MAX_FILES + 1)})

    with pytest.raises(DocumentFormatError) as info:
        read_members(content)

    assert str(MAX_FILES) in str(info.value)


def test_an_archive_that_unpacks_too_large_is_refused() -> None:
    """Сжатый размер ничего не говорит о распакованном: мегабайт нулей — это килобайт."""
    content = _zip({"bomb.txt": b"\0" * (MAX_UNPACKED_BYTES + 1)})

    with pytest.raises(DocumentFormatError) as info:
        read_members(content)

    assert "разворачивается" in str(info.value)


def test_a_file_that_is_not_an_archive_is_refused() -> None:
    with pytest.raises(DocumentFormatError):
        read_members(b"not a zip at all")


# -------------------------------------------------------------------- распаковка: что проходит

def test_members_keep_their_paths_and_bytes() -> None:
    content = _zip({"README.md": "Привет", "src/main.py": "# код\n"})

    members = read_members(content)

    assert [(m.path, m.content) for m in members] == [
        ("README.md", "Привет".encode()),
        ("src/main.py", "# код\n".encode()),
    ]


def test_directory_entries_are_not_members() -> None:
    content = _zip({"docs/": b"", "docs/README.md": "Привет"})

    assert [m.path for m in read_members(content)] == ["docs/README.md"]


# ------------------------------------------------------------------------------------- опись

def test_the_inventory_names_a_handler_and_counts_the_work() -> None:
    members = [ArchiveMember("README.md", "Привет мир".encode())]

    entry = take_inventory(members)[0]

    assert entry.path == "README.md"
    assert entry.handler == "md"
    assert entry.units == 1
    assert entry.chars == len("Привет мир")
    assert entry.selected is True
    assert entry.skip_reason is None


def test_a_format_without_a_handler_is_skipped_with_a_reason() -> None:
    entry = take_inventory([ArchiveMember("logo.png", b"\x89PNG\r\n")])[0]

    assert entry.handler is None
    assert entry.selected is False
    assert entry.skip_reason == "формат не переводится"


def test_an_english_file_is_not_selected_by_default() -> None:
    """Перевод идёт с русского, поэтому английский файл по умолчанию не отмечен."""
    members = [ArchiveMember("README.md", b"Intro to Machine Learning, lesson one")]

    entry = take_inventory(members)[0]

    assert entry.language == "other"
    assert entry.selected is False
    assert entry.skip_reason == "уже не на русском"


def test_a_file_with_nothing_to_translate_is_skipped() -> None:
    entry = take_inventory([ArchiveMember("empty.md", b"   \n\n")])[0]

    assert entry.units == 0
    assert entry.selected is False
    assert entry.skip_reason == "нечего переводить"


def test_a_broken_file_surfaces_before_the_run() -> None:
    """Опись считает единицы вхолостую — битый файл всплывает до оплаты, а не посреди прогона."""
    entry = take_inventory([ArchiveMember("notes.docx", b"not a docx")])[0]

    assert entry.selected is False
    assert entry.skip_reason is not None
    assert "повреж" in entry.skip_reason or "архив" in entry.skip_reason


def test_code_is_inventoried_by_its_comments() -> None:
    members = [ArchiveMember("ci/test.sh", "#!/bin/bash\n# Сборка проекта\nmake all\n".encode())]

    entry = take_inventory(members)[0]

    assert entry.handler == "sh"
    assert entry.units == 1
    assert entry.chars == len("Сборка проекта")


# ------------------------------------------------------------------------------------- смета

def test_the_estimate_grows_with_the_work_and_names_the_model() -> None:
    small = estimate_for(take_inventory([ArchiveMember("a.md", "Привет".encode())]))
    big = estimate_for(take_inventory([ArchiveMember("a.md", ("Привет мир. " * 500).encode())]))

    assert small.usd > 0
    assert big.usd > small.usd
    assert big.seconds >= small.seconds
    assert big.model, "смета должна называть модель, по прайсу которой посчитана"


def test_an_unselected_file_costs_nothing() -> None:
    entries = take_inventory([ArchiveMember("logo.png", b"\x89PNG")])

    estimate = estimate_for(entries)

    assert estimate.chars == 0
    assert estimate.usd == 0.0
    assert estimate.files == 0


# ------------------------------------------------------------------------------------ упаковка

def test_untouched_files_are_carried_over_byte_for_byte() -> None:
    members = [
        ArchiveMember("README.md", "Привет".encode()),
        ArchiveMember("data/logo.png", b"\x89PNG\r\n\x1a\n"),
    ]

    result = repack(members, {"README.md": b"Hello"}, report="# Отчёт\n")

    with zipfile.ZipFile(BytesIO(result)) as archive:
        assert archive.read("data/logo.png") == b"\x89PNG\r\n\x1a\n"
        assert archive.read("README.md") == b"Hello"


def test_the_result_keeps_the_same_names_and_structure() -> None:
    members = [ArchiveMember("docs/глава/README.md", "Привет".encode())]

    result = repack(members, {}, report="# Отчёт\n")

    with zipfile.ZipFile(BytesIO(result)) as archive:
        assert "docs/глава/README.md" in archive.namelist()


def test_the_report_travels_with_the_result() -> None:
    result = repack([ArchiveMember("a.md", b"x")], {}, report="# Отчёт\nвсё переведено\n")

    with zipfile.ZipFile(BytesIO(result)) as archive:
        assert "ПЕРЕВОД-ОТЧЁТ.md" in archive.namelist()
        assert "всё переведено" in archive.read("ПЕРЕВОД-ОТЧЁТ.md").decode("utf-8")


def test_a_replacement_for_a_path_that_is_not_in_the_archive_is_an_error() -> None:
    """Иначе перевод тихо потерялся бы: файла с таким именем в результате просто нет."""
    with pytest.raises(KeyError):
        repack([ArchiveMember("a.md", b"x")], {"b.md": b"y"}, report="")
