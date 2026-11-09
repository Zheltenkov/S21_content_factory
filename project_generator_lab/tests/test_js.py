"""The chat page: the kinds the server tells apart all have a label, and the page scripts pass their own tests
(tests/js, run by node when it is installed)."""

import re
import shutil
import subprocess
from pathlib import Path
from typing import get_args

import pytest
from fastapi.testclient import TestClient
from project_generator_lab import app as app_module
from project_generator_lab.concept.models import Brief
from project_generator_lab.core.intents import MessageKind

PACKAGE = Path(__file__).resolve().parents[1]


def test_every_kind_of_message_has_a_label_on_the_page():
    js = (PACKAGE / "static" / "chat.js").read_text(encoding="utf-8")
    labels = re.search(r"const KIND_LABEL = \{([^}]*)\}", js)
    assert labels, "KIND_LABEL not found in chat.js"
    assert set(re.findall(r"(\w+):", labels.group(1))) == set(get_args(MessageKind))
    # One list for both chats: the content chat has no copy of its own to drift.
    assert "понял как" in (PACKAGE / "static" / "content.js").read_text(encoding="utf-8")
    assert "UNDERSTOOD" not in (PACKAGE / "static" / "content.js").read_text(encoding="utf-8")


def listed(js: str, name: str) -> list[str]:
    found = re.search(rf"const {name} = (?:new Set\()?\[([^\]]*)\]", js)
    assert found, f"{name} not found in chat.js"
    return re.findall(r"'(\w+)'", found.group(1))


def test_every_field_the_form_sends_is_on_the_page_and_in_the_brief():
    """A field added to the brief and to the script but not to the page reads «undefined.value» when the form is sent;
    one the brief does not have is refused by the server. Checked by name, both ways (07.10.2026: «repository»)."""
    html, js = ((PACKAGE / "static" / name).read_text(encoding="utf-8") for name in ("chat.html", "chat.js"))
    sent = [*listed(js, "TEXT_FIELDS"), *listed(js, "NUMBER_FIELDS"), *listed(js, "FLAG_FIELDS"), "format", "start_level", "storytelling_type", "style_profile"]
    on_page = set(re.findall(r'<(?:input|textarea|select)\b[^>]*\bname="(\w+)"', html))
    assert "repository" in sent and len(sent) == len(set(sent))
    assert set(sent) - on_page == set(), "no field on the page"
    assert set(sent) - set(Brief.model_fields) == set(), "not a field of the brief"
    assert set(listed(js, "MORE_FIELDS")) <= set(sent)


def test_the_form_names_its_fields_as_the_curriculum_names_its_columns():
    html = (PACKAGE / "static" / "chat.html").read_text(encoding="utf-8")
    assert "Покажем, какие колонки распознаны" not in html
    assert re.search(r"<label[^>]*>\s*(?:<span[^>]*>)?\s*Сторителлинг\b", html) and "Тип сторителлинга" in html
    assert "Результаты всей программы, не этого проекта" in html and "Репозиторий проекта в GitLab" in html


@pytest.mark.parametrize("address, file", [("/", "chat.html"), ("/document?run=r1&doc=preview", "document.html"), ("/classic", "index.html")])
def test_a_page_is_asked_for_anew_every_time_and_its_scripts_are_named_with_their_version(address, file):
    """07.10.2026: after an update the browser went on showing the page of a document it had kept — with the old
    script, without the tables. A page without «no-cache» is kept by the browser's own guess, for hours."""
    with TestClient(app_module.app) as client:
        served = client.get(address)
    assert served.status_code == 200 and served.content == (PACKAGE / "static" / file).read_bytes()
    assert served.headers["cache-control"] == "no-cache"
    # The scripts of the page are named with a version, and what is named with a version has one: a script changed
    # without the name is the old script in a browser that has it.
    scripts = re.findall(r'<script src="static/[\w.]+(?:\?v=([\w-]+))?"', served.text)
    versions = set(re.findall(r'"static/[\w./-]+\?v=([\w-]+)"', served.text))
    assert scripts and all(scripts) and len(versions) == 1, (scripts, versions)


def test_a_link_to_the_page_of_a_document_is_not_the_address_browsers_kept_the_old_page_at():
    content, document = ((PACKAGE / "static" / name).read_text(encoding="utf-8") for name in ("content.js", "document.js"))
    assert "const view = (doc) => `document?run=${encodeURIComponent(S.content.id)}&doc=${doc}&v=7`;" in content
    assert "back.href = `document?run=${encodeURIComponent(run)}&doc=preview&v=7`;" in document


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed: the page scripts were not tested")
def test_the_page_scripts_pass_their_tests():
    files = sorted(str(p) for p in (PACKAGE / "tests" / "js").glob("*.test.mjs"))
    done = subprocess.run(["node", "--test", *files], capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-2000:]
