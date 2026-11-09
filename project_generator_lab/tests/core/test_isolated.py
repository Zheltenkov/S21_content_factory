"""Uploaded files are parsed in a child process under memory and CPU limits (audit 30.09.2026: a 100 KB PDF whose
stream inflates to 100 MB, parsed in the server process, took the 1 GB container down; a 5 KB XLSX declaring
XFD200000 burned 76 s of CPU). Boundary: the crafted files are refused with a readable text. Retention: ordinary
files parse as before, and the parser's own refusals keep their words."""

import io
import os
import zlib

import pytest
from openpyxl import Workbook
from project_generator_lab.concept.ingestion import InputError, extract_material
from project_generator_lab.core import isolated
from project_generator_lab.tests.isolated_jobs import chatty, hog, refuse, spin

# A child process with limits exists where rlimits do (the Linux server); on Windows, for local development, files are
# parsed in-process, so these tests have nothing to check there (local run on Windows 01.10.2026: `import resource`
# stopped the whole test collection).
child_process = pytest.mark.skipif(not isolated.available(), reason="no rlimits on this system: files are parsed in-process")


def pdf(stream: bytes) -> bytes:
    comp = zlib.compress(stream, 9)
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(comp) + comp + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()


@child_process
def test_the_limits_hit_the_child_and_the_server_gets_a_readable_refusal():
    import resource

    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    with pytest.raises(isolated.ParseRefused) as error:
        isolated.run(hog, memory_mb=300)
    assert str(error.value) == isolated.BIG
    with pytest.raises(isolated.ParseRefused) as error:
        isolated.run(spin, cpu_seconds=1)
    assert str(error.value) == isolated.HEAVY
    # The parser's own refusal keeps its words.
    with pytest.raises(InputError, match="Формат не поддерживается"):
        isolated.run(refuse)
    # None of it grew the server process.
    assert resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before < 50 * 1024


@child_process
def test_a_pdf_bomb_is_refused_and_an_ordinary_pdf_still_parses():
    ordinary = isolated.run(extract_material, "text.pdf", pdf(b"BT /F1 12 Tf 72 720 Td (Hello material text) Tj ET"))
    assert ordinary.passages[0].text == "Hello material text"
    bomb = pdf(b"q Q\n" * (40 * 1024 * 1024 // 4))
    assert len(bomb) < 60_000
    with pytest.raises(isolated.ParseRefused):
        isolated.run(extract_material, "bomb.pdf", bomb, memory_mb=250)


def test_a_sheet_declared_huge_is_refused_before_it_is_walked():
    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"], sheet["XFD200000"] = "x", "y"
    data = io.BytesIO()
    workbook.save(data)
    with pytest.raises(InputError, match="объявлен размером 200000×16384"):
        extract_material("t.xlsx", data.getvalue())
    # Retention: an ordinary sheet reads as before.
    small = Workbook()
    small.active.append(["Задание", "Баллы"])
    small.active.append(["Интервью", 0])
    data = io.BytesIO()
    small.save(data)
    assert [p.text for p in extract_material("s.xlsx", data.getvalue()).passages][-1].endswith("Интервью | 0")


@child_process
def test_the_child_gets_no_server_secrets_and_its_prints_do_not_break_the_answer(monkeypatch):
    # Independent review 30.09.2026: the child inherited the whole environment, API keys included, and anything a
    # parser printed to stdout would corrupt the answer.
    monkeypatch.setenv("POLZA_AI_API_KEY", "секрет")
    assert isolated.run(chatty) == "нет ключа"


@child_process
def test_a_file_waits_for_its_turn_a_bounded_time_and_then_is_refused(monkeypatch):
    monkeypatch.setattr(isolated, "QUEUE_SECONDS", 0.1)
    assert isolated.SLOTS.acquire(timeout=1)
    try:
        with pytest.raises(isolated.ParseRefused, match="Сейчас разбирается другой файл"):
            isolated.run(refuse)
    finally:
        isolated.SLOTS.release()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="no fork on this platform")
def test_a_forked_process_has_a_pool_of_its_own():
    # A pool that has worked here has idle threads, and after a fork it still counts them — in a process that has
    # none. The mutation run forks a worker per mutant: every worker that got as far as an upload over HTTP waited
    # there for its timeout, and its mutant was never judged.
    assert isolated.POOL.submit(int, "1").result(timeout=5) == 1
    child = os.fork()
    if child == 0:
        try:
            answered = isolated.POOL.submit(int, "2").result(timeout=5) == 2
        except BaseException:
            answered = False
        os._exit(0 if answered else 1)
    assert os.waitstatus_to_exitcode(os.waitpid(child, 0)[1]) == 0
