"""A README kept as a Word document, read as the Markdown the reader of a repository reads.

Each test builds a document with one feature in it and says what Markdown it has to become. The last ones show the
document to the reader of a repository: a line of a code sample must not become a heading on the way."""

import re
import zipfile

import pytest
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from project_generator_lab.benchmark import word
from project_generator_lab.benchmark.repository import parse_readme
from project_generator_lab.tests.benchmark.worded import AGAIN, BULLET, Built, day

W = nsdecls("w")


def read(built: Built, tmp_path) -> str:
    return word.markdown(built.save(tmp_path / "README.md.docx"))


def test_headings_are_known_by_their_styles_and_paragraphs_stand_apart(tmp_path):
    built = Built()
    built.heading("Заглавие  дня", 0)  # the style «Title»; the spaces of a heading are one space
    built.heading("Раздел", 2)
    built.text("Первый абзац.")
    built.text("")  # an empty paragraph — the blank line of a document — adds nothing
    built.text("   ")
    built.text("Второй абзац.")
    built.heading("Глубже некуда", 9)  # Markdown has six levels
    built.text("Крупно и жирно", style="Normal").runs[0].bold = True  # typed bold, not a heading: a bold line
    assert read(built, tmp_path) == "# Заглавие дня\n\n## Раздел\n\nПервый абзац.\n\nВторой абзац.\n\n###### Глубже некуда\n\n**Крупно и жирно**\n"
    assert read(Built(), tmp_path) == ""  # a document without a word in it


def test_bold_and_monospaced_runs_get_their_marks_with_the_spaces_outside(tmp_path):
    built = Built()
    # Word cuts a phrase into runs where it pleases: two bold runs side by side are one bold phrase.
    built.text(("Резуль", "b"), ("тат: ", "b"), "создан ", ("src/answer.py", "m"), ".")
    built.text((" Проверка:", "b"), " код ", ("0", "m"), (" ", "b"), "и ", ("  ", "m"), "всё.")  # marks around nothing are not written
    built.text(("git status", "bm"), " и ", ("печать `x`", "m"))  # bold code; code with a backtick in it
    built.text(("Результат:", "b"), " файл.\n", ("Проверка:", "b"), " запуск.\tДальше")  # a break of a line; a tab
    built.text(("Первая\nвторая", "b"))  # a mark does not run across a break
    assert read(built, tmp_path).split("\n\n") == [
        "**Результат:** создан `src/answer.py`.", " **Проверка:** код `0` и   всё.", "**`git status`** и `` печать `x` ``",
        "**Результат:** файл.\n**Проверка:** запуск.\tДальше", "**Первая**\n**вторая**\n"]


def test_bold_switched_off_is_not_bold_and_only_a_named_font_is_code(tmp_path):
    built = Built()
    paragraph = built.text("обычный ")
    built.raw(paragraph, f'<w:r {W}><w:rPr><w:b w:val="0"/><w:rFonts w:ascii="Arial"/></w:rPr><w:t>не жирный</w:t></w:r>')
    built.raw(paragraph, f'<w:r {W}><w:rPr><w:b w:val="false"/><w:rFonts w:cs="Courier New"/></w:rPr><w:t>код</w:t></w:r>')
    assert read(built, tmp_path) == "обычный не жирный`код`\n"


def test_items_of_lists_are_marked_and_numbered_list_by_list(tmp_path):
    built = Built()
    built.text("Шаги:")
    built.item("Открой терминал.")
    built.item("Найди ", ("файл", "m"), ".\nОн в корне.")  # the second line of an item stays inside the item
    built.item("вложенный шаг", level=1)
    built.item("ещё один", level=1)
    built.item("Закрой терминал.")
    built.item("после третьего шага вложенный счёт идёт заново", level=1)
    built.text("Что сдаёшь:")
    built.item("src/a.py", of=BULLET)
    built.item("src/b.py", of=BULLET, level=1)
    built.item("Новый список считает с единицы.", of=AGAIN)
    built.item("Список без описания нумерации — тоже список.", of=77)
    built.item("Абзац, у которого нумерация снята.", of=0)
    built.text("Список по стилю абзаца.", style="List Bullet")
    built.item("")  # an item without a word is not an item
    assert read(built, tmp_path) == (
        "Шаги:\n\n1. Открой терминал.\n2. Найди `файл`.\n   Он в корне.\n    1. вложенный шаг\n    2. ещё один\n3. Закрой терминал.\n"
        "    1. после третьего шага вложенный счёт идёт заново\n\nЧто сдаёшь:\n\n- src/a.py\n    - src/b.py\n1. Новый список считает с единицы.\n"
        "- Список без описания нумерации — тоже список.\n\nАбзац, у которого нумерация снята.\n\n- Список по стилю абзаца.\n")


def test_a_table_is_rows_with_its_first_row_for_the_header(tmp_path):
    built = Built()
    table = built.table([["Что сдаёшь", "Как проверяется"], ["src/count.sh", "cat журнал | wc -l"], ["один", ""]])
    table.rows[2].cells[1].text = "первая строка"
    table.rows[2].cells[1].add_paragraph("вторая\tстрока").runs[0].bold = True  # a cell of two paragraphs is one line
    table.rows[1]._tr.remove(table.rows[1].cells[1]._tc)  # a row a cell short
    built.text("после таблицы")
    assert read(built, tmp_path) == ("| Что сдаёшь | Как проверяется |\n|---|---|\n| src/count.sh |  |\n| один | первая строка<br>**вторая строка** |\n\nпосле таблицы\n")
    bar = Built()
    bar.table([["команда"], ["cat a | wc"]])
    assert read(bar, tmp_path) == "| команда |\n|---|\n| cat a \\| wc |\n"


def test_a_line_that_markdown_would_read_as_structure_stays_a_line_of_text(tmp_path):
    built = Built()
    built.heading("Задание 1. Комментарии", 2)
    for line in ("# комментарий в коде", "  ## и с отступом", "```python", "~~~", "| не таблица |", "#хэштег — и так не заголовок", "C# — язык", "1) пункт", "#",
                 "####### семь решёток — уже не заголовок"):
        built.text(line)
    built.item("шаг\n# вторая строка шага")
    text = read(built, tmp_path)
    assert text == ("## Задание 1. Комментарии\n\n\\# комментарий в коде\n\n  \\## и с отступом\n\n\\```python\n\n\\~~~\n\n\\| не таблица |\n\n"
                    "#хэштег — и так не заголовок\n\nC# — язык\n\n1) пункт\n\n\\#\n\n####### семь решёток — уже не заголовок\n\n"
                    "1. шаг\n   \\# вторая строка шага\n")
    readme = parse_readme(text)
    # Nothing of the sample became a heading, a table or an unclosed block of code: the task holds every line.
    assert [task.label for task in readme.tasks] == ["Задание 1"] and not readme.sections and not readme.unclosed
    assert not readme.tasks[0].table and "комментарий в коде" in readme.tasks[0].text and "вторая строка шага" in readme.tasks[0].text


def test_pictures_are_counted_and_a_ruled_line_is_not_a_picture(tmp_path):
    built = Built()
    built.text("До картинки.")
    built.picture()
    rule = built.text("")
    built.raw(rule, f'<w:r {W}><w:pict><v:rect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office" o:hr="t"/></w:pict></w:r>')
    old = built.text("Старая картинка: ")
    built.raw(old, f'<w:r {W}><w:pict><v:shape xmlns:v="urn:schemas-microsoft-com:vml"><v:imagedata/></v:shape></w:pict></w:r>')
    text = read(built, tmp_path)
    assert text == "До картинки.\n\n![](рисунок)\n\nСтарая картинка: ![](рисунок)\n" and parse_readme(text).images == 2


def test_links_and_insertions_are_read_and_what_was_removed_is_not(tmp_path):
    built = Built()
    paragraph = built.text("Смотри ")
    built.raw(paragraph, f'<w:hyperlink {W}><w:r><w:t>справочник</w:t></w:r></w:hyperlink>')
    built.raw(paragraph, f'<w:ins {W}><w:r><w:t xml:space="preserve"> и вставку</w:t></w:r></w:ins>')
    built.raw(paragraph, f'<w:del {W}><w:r><w:delText>удалённое</w:delText><w:t>и это</w:t></w:r></w:del>')
    built.raw(paragraph, f'<w:moveFrom {W}><w:r><w:t>перенесённое отсюда</w:t></w:r></w:moveFrom>')
    built.raw(paragraph, f'<w:r {W}><w:t>.</w:t><w:br w:type="page"/><w:noBreakHyphen/><w:cr/><w:t>конец</w:t></w:r>')
    assert read(built, tmp_path) == "Смотри справочник и вставку.-\nконец\n"


def linked(built, paragraph, address, inside='<w:r><w:t>справочник</w:t></w:r>'):
    """A link to the outside put into the paragraph, as Word keeps it: the words here, the address among the relationships."""
    ident = built.document.part.relate_to(address, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    built.raw(paragraph, f'<w:hyperlink {W} {nsdecls("r")} r:id="{ident}">{inside}</w:hyperlink>')


@pytest.mark.parametrize("address, read_as", [
    ("https://example.org/poll", "[справочник](https://example.org/poll)"), ("HTTP://example.org", "[справочник](HTTP://example.org)"),
    ("mailto:team@example.org", "[справочник](mailto:team@example.org)"), ("  https://example.org/a\n", "[справочник](https://example.org/a)"),
    ("https://example.org/a_(b)", "[справочник](https://example.org/a_(b%29)"),  # «)» in an address would end the link in Markdown
    # What is not an address in one word is not written into the line: the link is its words.
    ("javascript:alert(1)", "справочник"), ("file:///C:/secret.txt", "справочник"), ("https://example.org/a b", "справочник"), ("#chapter-i", "справочник"),
    ("https://", "справочник"), ("ftp://example.org/x", "справочник"),
    # What Word makes of the name of a file typed into the text is a link to nowhere: an address has a host with a dot.
    ("http://materials/rules_rus.md", "справочник"), ("http://datasets/", "справочник"), ("http://src", "справочник"), ("http://localhost:8000/x", "справочник"),
    ("https://example.org:8080/x?y=1#z", "[справочник](https://example.org:8080/x?y=1#z)"), ("https://example..org/x", "справочник"),
])
def test_a_link_to_the_outside_keeps_its_address(tmp_path, address, read_as):
    built = Built()
    linked(built, built.text("Смотри "), address)
    assert read(built, tmp_path) == f"Смотри {read_as}\n"


def test_the_words_of_a_link_keep_their_marks_and_a_link_without_words_is_nothing(tmp_path):
    built = Built()
    paragraph = built.text("Открой ")
    linked(built, paragraph, "https://example.org/a", '<w:r><w:rPr><w:b/></w:rPr><w:t>анкету</w:t></w:r><w:r><w:t xml:space="preserve"> курса</w:t></w:r>')
    linked(built, paragraph, "https://example.org/empty", "<w:r><w:t></w:t></w:r>")
    built.raw(paragraph, f'<w:hyperlink {W} w:anchor="chapter"><w:r><w:t xml:space="preserve"> и раздел</w:t></w:r></w:hyperlink>')  # a link inside the document
    built.raw(paragraph, f'<w:hyperlink {W} {nsdecls("r")} r:id="rId999"><w:r><w:t xml:space="preserve"> и никуда</w:t></w:r></w:hyperlink>')
    table = built.table([["Ссылка", ""]])
    linked(built, table.rows[0].cells[1].paragraphs[0], "https://example.org/t", "<w:r><w:t>таблица</w:t></w:r>")
    text = read(built, tmp_path)
    assert text.splitlines()[0] == "Открой [**анкету** курса](https://example.org/a) и раздел и никуда"
    assert "| Ссылка | [таблица](https://example.org/t) |" in text and "empty" not in text
    # The addresses are what the judge is told the number of.
    assert len(set(re.findall(r"https?://[^\s)>\]]+", text))) == 2


def test_a_block_word_wrapped_around_paragraphs_is_read_through(tmp_path):
    built = Built()
    built.text("до")
    built.document.element.body.insert(1, parse_xml(
        f'<w:sdt {W}><w:sdtContent><w:p><w:r><w:t>оглавление</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>ячейка</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        '</w:sdtContent></w:sdt>'))
    assert read(built, tmp_path) == "до\n\nоглавление\n\n| ячейка |\n|---|\n"


def test_what_is_not_a_word_document_is_refused_by_name(tmp_path, monkeypatch):
    text = tmp_path / "README.md.docx"
    text.write_text("# это Markdown под чужим именем", encoding="utf-8")
    with pytest.raises(ValueError, match=r"README\.md\.docx не читается как документ Word \(BadZipFile\)"):
        word.markdown(text)
    with zipfile.ZipFile(tmp_path / "empty.docx", "w") as packed:
        packed.writestr("note.txt", "архив без документа")
    with pytest.raises(ValueError, match=r"empty\.docx не читается как документ Word"):
        word.markdown(tmp_path / "empty.docx")
    path = day().save(tmp_path / "day.docx")
    monkeypatch.setattr(word, "MAX_UNPACKED", 1000)
    with pytest.raises(ValueError, match=r"day\.docx после распаковки больше"):
        word.markdown(path)
    monkeypatch.setattr(word, "MAX_WORD", 1000)
    with pytest.raises(ValueError, match=r"day\.docx больше"):
        word.markdown(path)


def test_the_readme_of_a_day_reads_into_its_sections_and_tasks(tmp_path):
    readme = parse_readme(read(day(), tmp_path))
    assert readme.title == "День 01. Журнал" and readme.annotation == "Сегодня ты найдёшь журнал станции и посчитаешь его строки."
    assert [(s.title, s.role) for s in readme.sections] == [("Материалы", "other"), ("Что сдаётся за день", "other")]
    assert [(t.label, t.title, t.bonus) for t in readme.tasks] == [("Задание 1", "Разведка", False), ("Задание 2", "Счёт", False), ("Задание 3", "Реставратор", True)]
    assert readme.tasks[0].text == ("1. Открой терминал.\n2. Найди файл `datasets/station.log`.\n\n"
                                    "**Результат:** путь к журналу записан в `src/path.txt`.\n**Зачем:** без журнала считать нечего.")
    # The line of the log that begins with «#» is a line of task 2, and its table keeps the bar of the command.
    assert "\\# первая строка журнала" in readme.tasks[1].text and "cat datasets/station.log \\| wc -l" in readme.tasks[1].text


@pytest.mark.parametrize("mark", ["\u2028", "\u2029", "\x85"])  # the others XML does not let into a document at all
def test_whatever_ends_a_line_for_the_reader_ends_it_here(tmp_path, mark):
    # The reader of a repository cuts a text into lines by every such mark: a «#» after one of them would be a heading.
    built = Built()
    built.heading("Задание 1. Журнал", 2)
    built.text(f"вывод:{mark}# Задание 9. Поддельное{mark}```")
    built.heading("Задание 2. Счёт", 2)
    text = read(built, tmp_path)
    assert text == "## Задание 1. Журнал\n\nвывод:\n\\# Задание 9. Поддельное\n\\```\n\n## Задание 2. Счёт\n"
    readme = parse_readme(text)
    assert [task.label for task in readme.tasks] == ["Задание 1", "Задание 2"] and not readme.unclosed


def test_a_break_typed_as_two_marks_is_one_line_break(tmp_path):
    built = Built()
    paragraph = built.text("")
    built.raw(paragraph, f'<w:r {W}><w:t xml:space="preserve">первая&#13;&#10;вторая&#13;третья</w:t></w:r>')
    assert read(built, tmp_path) == "первая\nвторая\nтретья\n"


def test_the_levels_and_kinds_of_lists_are_read_from_what_the_document_says(tmp_path):
    built = Built()
    built.item("глубже девятого уровня не бывает", of=BULLET, level=42)
    for value in ("²", "x", ""):  # a level that is not a number is the first level
        item = built.item(f"уровень «{value}»", of=BULLET)
        item._p.pPr.find(f"{{{W.split(chr(34))[1]}}}numPr")[0].set(f"{{{W.split(chr(34))[1]}}}val", value)
    numbering = built.document.part.numbering_part.element
    numbering.append(parse_xml(f'<w:abstractNum {W} w:abstractNumId="93"><w:lvl w:ilvl="0"><w:numFmt w:val="none"/></w:lvl><w:lvl w:ilvl="1"/></w:abstractNum>'))
    numbering.append(parse_xml(f'<w:num {W} w:numId="93"><w:abstractNumId w:val="93"/></w:num>'))
    numbering.append(parse_xml(f'<w:num {W} w:numId="94"/>'))  # a list that names no description of itself
    built.item("нумерация «нет» — метка", of=93)
    built.item("уровень без описания — метка", of=93, level=1)
    built.item("список без описания — метка", of=94)
    plain = built.text("абзац с пустой нумерацией")
    plain._p.get_or_add_pPr().append(parse_xml(f'<w:numPr {W}><w:ilvl w:val="0"/><w:numId/></w:numPr>'))
    built.item("первый", of=AGAIN)
    built.item("второй", of=AGAIN)
    assert read(built, tmp_path) == (f"{' ' * 32}- глубже девятого уровня не бывает\n- уровень «²»\n- уровень «x»\n- уровень «»\n- нумерация «нет» — метка\n"
                                     "    - уровень без описания — метка\n- список без описания — метка\n\nабзац с пустой нумерацией\n\n1. первый\n2. второй\n")


@pytest.mark.parametrize("fonts, code", [
    ('w:ascii="Consolas"', True), ('w:hAnsi="Menlo"', True), ('w:cs="Courier New"', True), ('w:ascii="JetBrains Mono"', True),
    ('w:ascii="Arial" w:hAnsi="Arial"', False), ('w:eastAsia="Consolas"', False), ("", False),
])
def test_a_run_is_code_by_the_font_it_names(tmp_path, fonts, code):
    built = Built()
    built.raw(built.text("файл "), f'<w:r {W}><w:rPr><w:rFonts {fonts}/></w:rPr><w:t>a.py</w:t></w:r>')
    assert read(built, tmp_path) == ("файл `a.py`\n" if code else "файл a.py\n")


def test_bold_is_bold_unless_it_is_switched_off_in_any_of_the_ways(tmp_path):
    built = Built()
    paragraph = built.text("")
    for value in ("0", "false", "off", "1", "true", "on"):
        built.raw(paragraph, f'<w:r {W}><w:rPr><w:b w:val="{value}"/></w:rPr><w:t xml:space="preserve">{value} </w:t></w:r>')
        built.raw(paragraph, f'<w:r {W}><w:t xml:space="preserve">· </w:t></w:r>')
    assert read(built, tmp_path) == "0 · false · off · **1** · **true** · **on** ·\n"


def test_what_word_keeps_twice_is_read_once_and_what_it_wraps_is_read_through(tmp_path):
    mc = 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
    box = f'<w:txbxContent {W}><w:p><w:r><w:t>В РАМКЕ</w:t></w:r></w:p></w:txbxContent>'
    twice = f'<mc:AlternateContent {mc} {W}><mc:Choice Requires="wps">{{}}</mc:Choice><mc:Fallback>{{}}</mc:Fallback></mc:AlternateContent>'
    built = Built()
    # The same runs for a new Word and for an old one, between the runs of a paragraph: the first of the two is read.
    built.raw(built.text("до "), twice.format(f'<w:r {W}><w:t>новое</w:t></w:r>', f'<w:r {W}><w:t>старое</w:t></w:r>'))
    # A text box drawn in a cell is not a paragraph of the cell, however many times Word wrote it down.
    table = built.table([["ячейка"], ["вторая"]])
    built.raw(table.rows[0].cells[0].paragraphs[0], f'<w:r {W}>' + twice.format(f'<w:drawing>{box}</w:drawing>', f'<w:pict>{box}</w:pict>') + '</w:r>')
    # A table inside a cell is lines of that cell; a row, a cell and a block wrapped by Word are a row, a cell and a block.
    inner = f'<w:tbl {W}><w:tr><w:tc><w:p><w:r><w:t>внутри</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    table.rows[1].cells[0]._tc.append(parse_xml(inner))
    table._tbl.append(parse_xml(f'<w:sdt {W}><w:sdtContent><w:tr><w:sdt><w:sdtContent><w:tc><w:p><w:r><w:t>в обёртке</w:t></w:r></w:p></w:tc></w:sdtContent></w:sdt></w:tr></w:sdtContent></w:sdt>'))
    built.document.element.body.append(parse_xml(f'<w:customXml {W}><w:p><w:r><w:t>блок в обёртке</w:t></w:r></w:p></w:customXml>'))
    # A picture kept twice is one picture.
    picture = '<a:blip xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"/>'
    built.raw(built.text("рисунок: "), f'<w:r {W}>' + twice.format(f'<w:drawing>{picture}</w:drawing>', '<w:pict><v:imagedata xmlns:v="urn:schemas-microsoft-com:vml"/></w:pict>') + '</w:r>')
    # When the first of the two says nothing, the second is read; a note between them is not one of the two.
    built.raw(built.text("пусто: "), twice.format("", f'<w:r {W}><w:t>только старое</w:t></w:r>').replace("<mc:Choice", "<!-- заметка --><mc:Choice"))
    built.raw(built.text("слово "), f'<w:smartTag {W}><w:r><w:t>в метке</w:t></w:r></w:smartTag>')
    assert read(built, tmp_path) == ("до новое\n\n| ячейка |\n|---|\n| вторая<br>внутри |\n| в обёртке |\n\nрисунок: ![](рисунок)\n\n"
                                     "пусто: только старое\n\nслово в метке\n\nблок в обёртке\n")


def rewritten(source, target, parts: dict[str, bytes | None]):
    """The document with some of its parts replaced, taken out (None) or added."""
    with zipfile.ZipFile(source) as packed, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
        names = packed.namelist()
        for name in [*names, *(name for name in parts if name not in names)]:
            data = parts[name] if name in parts else packed.read(name)
            if data is not None:
                out.writestr(name, data)
    return target


@pytest.mark.parametrize("parts", [
    {"word/document.xml": b"<<< not xml"},
    {"word/document.xml": b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>'},  # no body
    {"word/document.xml": b'<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>'},  # not a document
    {"word/document.xml": None}, {"[Content_Types].xml": None}, {"word/styles.xml": b"\x00\x01"},
    # A style without a name: the library refuses it with an error of its own kind.
    {"word/styles.xml": b'<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:style w:type="paragraph" w:styleId="A"><w:name/></w:style></w:styles>'},
])
def test_whatever_is_wrong_inside_the_document_it_is_refused_by_its_name(tmp_path, parts):
    broken = rewritten(day().save(tmp_path / "day.docx"), tmp_path / "README.md.docx", parts)
    with pytest.raises(ValueError, match=r"^README\.md\.docx не читается как документ Word \(\w+\)$"):
        word.markdown(broken)


def test_a_document_whose_bytes_were_damaged_is_refused_by_its_name(tmp_path):
    path = day().save(tmp_path / "README.md.docx")
    data = bytearray(path.read_bytes())
    with zipfile.ZipFile(path) as packed:
        info = packed.getinfo("word/document.xml")
    for place in range(info.header_offset + 60, info.header_offset + 100):
        data[place] ^= 0xFF
    path.write_bytes(bytes(data))
    with pytest.raises(ValueError, match=r"^README\.md\.docx не читается как документ Word \(\w+\)$"):
        word.markdown(path)


def test_an_archive_python_cannot_open_is_refused_by_its_name_too(tmp_path):
    path = day().save(tmp_path / "README.md.docx")
    data = bytearray(path.read_bytes())
    place = data.rfind(b"PK\x01\x02")  # the last entry of the directory of the archive: «version needed» set beyond any there is
    data[place + 6] = 128
    path.write_bytes(bytes(data))
    with pytest.raises(ValueError, match=r"^README\.md\.docx не читается как документ Word \(\w+\)$"):
        word.markdown(path)


def test_the_limit_is_on_every_part_whatever_its_name(tmp_path, monkeypatch):
    # Which part is the text is said inside the archive: a document may keep it under any name.
    path = rewritten(day().save(tmp_path / "day.docx"), tmp_path / "README.md.docx", {"word/media/huge.bin": b"x" * 700_000})
    assert word.markdown(path).startswith("# День 01. Журнал")
    monkeypatch.setattr(word, "MAX_PART", 699_999)
    with pytest.raises(ValueError, match="после распаковки больше"):
        word.markdown(path)
    assert (word.MAX_WORD, word.MAX_UNPACKED) == (30_000_000, 60_000_000)  # the limits the document describes


def test_the_text_inside_is_what_is_limited_not_the_size_of_the_file(tmp_path, monkeypatch):
    # A megabyte of a file may hold a hundred of XML, and that unpacks into gigabytes of memory.
    filler = b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + b"<w:p/>" * 100_000 + b"</w:body></w:document>"  # the largest part of the document now
    path = rewritten(day().save(tmp_path / "day.docx"), tmp_path / "README.md.docx", {"word/document.xml": filler})
    assert path.stat().st_size < len(filler) // 5 and word.markdown(path) == ""
    monkeypatch.setattr(word, "MAX_PART", len(filler))
    assert word.markdown(path) == ""  # a part of exactly the limit is read
    monkeypatch.setattr(word, "MAX_PART", len(filler) - 1)
    with pytest.raises(ValueError, match=r"^README\.md\.docx после распаковки больше 0 МБ$"):
        word.markdown(path)
    monkeypatch.setattr(word, "MAX_PART", 10_000_000)
    with zipfile.ZipFile(path) as packed:
        whole = sum(info.file_size for info in packed.infolist())
    monkeypatch.setattr(word, "MAX_UNPACKED", whole)
    assert word.markdown(path) == ""
    monkeypatch.setattr(word, "MAX_UNPACKED", whole - 1)
    with pytest.raises(ValueError, match="после распаковки больше"):
        word.markdown(path)
    monkeypatch.setattr(word, "MAX_WORD", path.stat().st_size - 1)
    with pytest.raises(ValueError, match=r"^README\.md\.docx больше 0 МБ$"):
        word.markdown(path)
    monkeypatch.setattr(word, "MAX_WORD", path.stat().st_size)
    with pytest.raises(ValueError, match="после распаковки"):
        word.markdown(path)


def test_the_small_things_of_a_run_and_of_an_item(tmp_path):
    built = Built()
    # A heading typed as two runs is one heading; a break of a column, like one of a page, is not a break of a line.
    built.heading("Задание 1.", 2).add_run(" Запуск")
    column = built.text("колонка")
    built.raw(column, f'<w:r {W}><w:br w:type="column"/><w:t>дальше</w:t><w:br w:type="textWrapping"/><w:t>строка</w:t></w:r>')
    # Words and a picture in one run are both kept; the words after what Word keeps twice are kept too.
    picture = '<a:blip xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"/>'
    built.raw(built.text(""), f'<w:r {W}><w:t>схема: </w:t><w:drawing>{picture}</w:drawing><w:t> — выше</w:t></w:r>')
    mc = 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
    twice = built.text("до ")
    built.raw(twice, f'<mc:AlternateContent {mc} {W}><mc:Choice Requires="w14"><w:r><w:t>новое</w:t></w:r></mc:Choice><mc:Fallback><w:r><w:t>старое</w:t></w:r></mc:Fallback></mc:AlternateContent>')
    built.raw(twice, f'<w:r {W}><w:t xml:space="preserve"> после</w:t></w:r>')
    # An item is written from its first letter, whatever spaces were typed before it; a list that names no level is of the first.
    built.item("   шаг с пробелами\n     и вторая строка")
    bare = built.text("пункт без уровня")
    bare._p.get_or_add_pPr().append(parse_xml(f'<w:numPr {W}><w:numId w:val="{BULLET}"/></w:numPr>'))
    table = built.table([["в ячейке"]])
    table.rows[0].cells[0].paragraphs[0].add_run(" первая\nвторая")
    assert read(built, tmp_path) == ("## Задание 1. Запуск\n\nколонкадальше\nстрока\n\nсхема: ![](рисунок) — выше\n\nдо новое после\n\n"
                                     "1. шаг с пробелами\n   и вторая строка\n- пункт без уровня\n\n| в ячейке первая<br>вторая |\n|---|\n")


def test_a_document_without_a_description_of_its_lists_is_read(tmp_path):
    built = Built()
    built.item("пункт")
    built.text("текст")
    source = built.save(tmp_path / "day.docx")
    with zipfile.ZipFile(source) as packed:
        links = packed.read("word/_rels/document.xml.rels").decode("utf-8")
    import re

    without = re.sub(r"<Relationship [^>]*numbering[^>]*/>", "", links)
    assert without != links
    path = rewritten(source, tmp_path / "README.md.docx", {"word/_rels/document.xml.rels": without.encode("utf-8"), "word/numbering.xml": None})
    assert word.markdown(path) == "- пункт\n\nтекст\n"


def test_the_refusal_names_what_the_library_said(tmp_path):
    broken = rewritten(day().save(tmp_path / "day.docx"), tmp_path / "README.md.docx", {"word/document.xml": b"<<< not xml"})
    with pytest.raises(ValueError, match=r"^README\.md\.docx не читается как документ Word \(XMLSyntaxError\)$"):
        word.markdown(broken)
