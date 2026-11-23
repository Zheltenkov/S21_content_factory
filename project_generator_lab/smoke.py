"""A free run of the service on the libraries of the image a release is about to switch to.

    python -m project_generator_lab.smoke

Reading the code cannot tell whether *this* image carries what the release relies on: the production image has its
own Python, FastAPI, pydantic and provider client, none of them the ones the tests ran on. So the release script
(deploy/release.sh) runs this before the switch, and stops when it fails — the working service is then untouched.

The run goes through the code as a methodologist's requests do: the session guard, the address prefix of the proxy,
the whole route of a content draft from its start to the files of the project, a chat edit, the account of model
calls. Only the ends that cost money or need the network are scripted, at the wire: the auth service, the model
gateway and the search answer in-process through httpx transports — the provider's client that sends the request
and reads the answer is the real one of the image. The concept is the fixture of the tests; pages are not opened.

Nothing of the service is read or written: every folder is a temporary one, made before the application is loaded.
That is why this is a program of its own and not a function to call — the folders are read when the modules load.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import tempfile
import time
import traceback
from importlib import metadata
from pathlib import Path
from typing import Any

# deploy/Dockerfile: --root-path. The requests come as uvicorn hands them over behind the proxy: the prefix is both the
# root path and the beginning of the path (uvicorn 0.26 and later; with the prefix only in the root path the files
# of the pages would not be found here, though they are in the service).
PREFIX = "/app/project-preparation"
SESSION = "smoke-session"
OWNER = "smoke-user"
WRITER, CRITIC = "writer-model", "critic-model"
# What the gateway says each call cost (Polza: usage.cost_rub). A sum of these is exact in binary, so the account
# is compared to the cent: 0.25 a model call, 0.5 a search.
PRICE = {"model": 0.25, "search": 0.5}
JSON_MARK = "\nВерни только JSON по схеме:\n"  # core/llm.py: how a call that answers by a schema ends its system text
WAIT_SECONDS = 60.0
# How the route tells a methodologist that a step did not go (content/flow.py, content/api.py).
FAILED_STEP = ("Не получилось", "Внутренняя ошибка")


class Failed(Exception):
    """A step did not go as the release needs it to."""


def expect(condition: object, what: str, got: object = "") -> None:
    if not condition:
        raise Failed(f"{what}{f': {str(got)[:400]}' if got != '' else ''}")


def versions() -> str:
    def of(name: str) -> str:
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            return "?"
    return f"python {platform.python_version()}, " + ", ".join(f"{name} {of(name)}" for name in ("fastapi", "starlette", "pydantic", "openai", "httpx", "uvicorn"))


def run(work: Path) -> str:
    # The folders, the key and the address of the gateway are read when the modules load: in a process that has the
    # application already, they would be the service's own.
    expect(f"{__package__}.app" not in sys.modules, "the application is already loaded in this process: run the module as a program")
    os.environ.update(
        PROJECT_LAB_RUNS_DIR=str(work / "runs"), PROJECT_LAB_CONTENT_DIR=str(work / "content"), PROJECT_LAB_USAGE_DIR=str(work / "usage"),
        POLZA_AI_API_KEY="smoke", POLZA_AI_BASE_URL="http://gateway.invalid/api/v1", PROJECT_LAB_MODEL=WRITER,
        PROJECT_LAB_REVIEWER_MODEL=CRITIC, PROJECT_LAB_ALLOWED_HOSTS="testserver",
    )
    import httpx
    from fastapi.testclient import TestClient
    from openai import AsyncOpenAI

    from .app import app
    from .concept import runs
    from .concept.models import Brief, Run, StartRequest
    from .content import api as content_api
    from .content import models, store
    from .content.models import ContentChange, ContentRevision
    from .content.writer import CONTENT_VERSION, Writer
    from .core import llm, usage
    from .core import search as search_module
    from .core.access import SessionGuard
    from .core.llm import ModelClient
    from .core.search import SearchClient
    from .tests.content.fixtures import FakeModel, FakeSearch, handoff_fixture, open_pages, read_pages

    expect(runs.RUNS_DIR == work / "runs" and store.CONTENT_DIR == work / "content" and usage.USAGE_DIR == work / "usage",
           "the folders of the run are not the temporary ones")
    writer, critic, search = FakeModel(WRITER), FakeModel(CRITIC), FakeSearch()
    asked: list[str] = []

    async def gateway(request: httpx.Request) -> httpx.Response:
        """The model gateway: answers a call as the provider would, with what it cost."""
        body = json.loads(request.content)
        system, payload = body["messages"][0]["content"], body["messages"][1]["content"]
        extra: dict[str, Any] = {}
        if JSON_MARK in system:
            prompt, shape = system.split(JSON_MARK, 1)
            schema = getattr(models, json.loads(shape)["title"])
            answer, _ = await (critic if body["model"] == CRITIC else writer).generate(schema, prompt, payload)
            content, kind = answer.model_dump_json(), "model"
        else:
            content, links, _ = await search.text(system, payload)
            extra, kind = {"citations": links}, "search"
        asked.append(kind)
        return httpx.Response(200, json={
            "id": f"smoke-{len(asked)}", "object": "chat.completion", "created": 0, "model": body["model"],
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200, "cost_rub": PRICE[kind]}, **extra})

    def provider(**settings: Any) -> AsyncOpenAI:
        # First as the service makes it — with the transport of its own, which is where a client and an HTTP library
        # that do not fit each other break. It is only made, never asked: making one needs no network.
        AsyncOpenAI(**settings)
        # Then the one that answers here. A client of httpx is taken by every version of the provider's client (the
        # later ones, built on httpx2, take it as the older kind).
        wire: Any = httpx.AsyncClient(transport=httpx.MockTransport(gateway))
        return AsyncOpenAI(**settings, http_client=wire)

    keep = usage.record

    def record(kind: Any, asked: str, model: str, *rest: Any, **named: Any) -> None:
        """The adapters tell a methodologist only the kind of a failure, by design. Here the cause is the finding."""
        outcome = named.get("outcome", rest[4] if len(rest) > 4 else "ok")
        if outcome != "ok" and sys.exc_info()[0] is not None:
            print(f"-- the call {asked} ({model}) ended as «{outcome}»:", file=sys.stderr)
            traceback.print_exc()
        keep(kind, asked, model, *rest, **named)

    usage.record = record

    def auth(request: httpx.Request) -> httpx.Response:
        known = f"content_gen_auth={SESSION}" in request.headers.get("cookie", "")
        return httpx.Response(200, json={"id": OWNER}) if known else httpx.Response(401, json={"detail": "no session"})

    llm.AsyncOpenAI = provider  # type: ignore[assignment,misc]
    search_module.AsyncOpenAI = provider  # type: ignore[assignment,misc]
    content_api.settings.handoff = handoff_fixture
    content_api.settings.writer = lambda: Writer(ModelClient(), reviewer=ModelClient(model=CRITIC), search=SearchClient(), opener=open_pages, reader=read_pages)
    # The guard of production.py, with the auth service answered here.
    app.add_middleware(SessionGuard, auth_url="http://auth.invalid/api/v1/auth/me", transport=httpx.MockTransport(auth))

    project = handoff_fixture().run_id
    # The concept is the fixture; its run is stored only so that the report of the cost knows the project by its id.
    runs.save_run(Run(id=project, fingerprint="smoke", owner_id=OWNER, status="approved", request=StartRequest(request_id=project, brief=Brief(
        title="Проверка релиза", description="Запуск без моделей перед переключением.", audience="Никто", outcomes="Сервис работает"))))
    steps = 0
    address = f"http://testserver{PREFIX}/"
    with TestClient(app, base_url=address, root_path=PREFIX) as stranger, TestClient(app, base_url=address, root_path=PREFIX) as client:
        answer = stranger.get("/api/config")
        expect(answer.status_code == 401, "a request without a session is not refused", answer.status_code)
        stranger.cookies.set("content_gen_auth", "somebody-else")
        answer = stranger.get(f"/api/content/{project}")
        expect(answer.status_code == 401, "a session the auth service does not know is not refused", answer.status_code)

        client.cookies.set("content_gen_auth", SESSION)
        answer = client.get("/api/config")
        expect(answer.status_code == 200 and answer.json()["content_version"] == CONTENT_VERSION, "the configuration", answer.text)
        for page in ("/", "/document"):
            answer = client.get(page)
            expect(answer.status_code == 200 and "<html" in answer.text.lower(), f"the page {page}", answer.status_code)
        for asset in ("chat.js", "content.js", "document.js", "document.css"):
            answer = client.get(f"/static/{asset}")
            expect(answer.status_code == 200 and answer.content, f"the file static/{asset}", answer.status_code)

        def idle() -> dict[str, Any]:
            deadline = time.monotonic() + WAIT_SECONDS
            while True:
                seen = client.get(f"/api/content/{project}")
                expect(seen.status_code == 200, "the draft does not open", seen.text)
                view: dict[str, Any] = seen.json()
                if not view.get("busy"):
                    return view
                expect(time.monotonic() < deadline, "a step of the route did not finish in a minute", view.get("busy"))
                time.sleep(0.02)

        answer = client.post(f"/api/content/{project}/start")
        expect(answer.status_code == 200, "the content does not start", answer.text)
        view = idle()
        failed = [m["text"] for m in view["messages"] if m["role"] == "system" and m["text"].startswith(FAILED_STEP)]
        expect(not failed, "the first step of the route failed (its cause is printed above)", failed[-1:])
        while view["next_step"]["action"]:
            step = view["next_step"]
            steps += 1
            expect(steps <= 60, "the route does not come to its end", step["action"])
            answer = client.post(f"/api/content/{project}/advance", json={"action": step["action"], "number": step["number"]})
            expect(answer.status_code == 200, f"the step {step['action']} is refused", answer.text)
            view = idle()
            failed = [m["text"] for m in view["messages"] if m["role"] == "system" and m["text"].startswith(FAILED_STEP)]
            expect(not failed, f"the step {step['action']} failed (its cause is printed above)", failed[-1:])
        expect(view["ready"] and view["assembled_at"], "the project is not assembled at the end of the route", [c["message"] for c in view["checks"] if c["severity"] == "block"])

        # A chat edit: the model proposes, the methodologist applies.
        title = "Название, изменённое проверкой релиза"
        writer.revisions = [ContentRevision(reply="Предлагаю название.", changes=[ContentChange(part="summary", field="title", value=title, reason="Проверка релиза")])]
        answer = client.post(f"/api/content/{project}/messages", json={"text": "Назови проект иначе"})
        expect(answer.status_code == 200, "the chat does not answer", answer.text)
        view = idle()
        pending = [m for m in view["messages"] if m["status"] == "pending"]
        expect(len(pending) == 1, "the chat proposed no edit", [m["text"] for m in view["messages"]][-2:])
        answer = client.post(f"/api/content/{project}/messages/{pending[0]['id']}/decision", json={"verdict": "apply"})
        expect(answer.status_code == 200 and answer.json()["summary"]["title"] == title, "the edit is not applied", answer.text)

        documents = {}
        for name in ("preview", "text", "readme", "check-list", "check-list-preview", "bundle"):
            answer = client.get(f"/api/content/{project}/{name}")
            expect(answer.status_code == 200 and len(answer.content) > 200, f"the document «{name}»", answer.status_code)
            documents[name] = answer
        expect(title in documents["preview"].text, "the page of the project does not show the applied edit")
        expect(documents["bundle"].content[:2] == b"PK", "the files of the project are not an archive")
        expect(view["materials"], "the project has no materials")
        for path in ("README.md", "materials/" + view["materials"][0]["path"]):
            answer = client.get(f"/api/content/{project}/file", params={"path": path})
            expect(answer.status_code == 200 and answer.text.strip(), "a file of the project is not given by its path", (path, answer.status_code))

        # The account: every call the route and the chat made is written under the project, by the one who made it,
        # with what the gateway said it cost — the jobs ran in tasks of their own, the provider's answer was read by
        # the client of this image.
        calls = usage.calls_of([project])
        expect(len(calls) == len(asked), "the account does not hold every call that was made", f"{len(calls)} written, {len(asked)} made")
        expect({call.owner for call in calls} == {OWNER}, "the calls are not written under the one who made them", {call.owner for call in calls})
        expect({call.outcome for call in calls} == {"ok"}, "a call did not end well", {call.outcome for call in calls})
        made = sum(PRICE[kind] for kind in asked)
        written = sum(call.cost_rub or 0 for call in calls)
        expect(all(call.cost_rub is not None for call in calls) and written == made, "the account does not hold what the gateway charged", f"{written} written, {made} charged")
        answer = client.get(f"/api/usage/{project}")
        expect(answer.status_code == 200, "the report of the cost", answer.text)
        report = answer.json()
        expect(report["calls"] == len(calls) and report["recorded"], "the report does not count the calls of the project", {k: report.get(k) for k in ("calls", "recorded", "failed")})
        answer = stranger.get(f"/api/usage/{project}")
        expect(answer.status_code == 401, "the report of the cost is given without a session", answer.status_code)
    return (f"route {steps} steps, project assembled, chat edit applied; account: {len(calls)} calls "
            f"({asked.count('model')} of models, {asked.count('search')} searches), {written:g} RUB written of {made:g} RUB the gateway named")


def main() -> int:
    # A failure quotes the service's own answer, which is in Russian: it must print whatever the console can show.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    work = Path(tempfile.mkdtemp(prefix="project-lab-smoke-"))
    try:
        done = run(work)
    except Failed as failure:
        print(f"smoke FAILED: {failure}\n({versions()})")
        return 1
    except Exception:
        # Not a step that went wrong but the code itself: what this run exists to find before the switch.
        traceback.print_exc()
        print(f"smoke FAILED: the run broke off, see above\n({versions()})")
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"smoke ok: {done}\n({versions()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
