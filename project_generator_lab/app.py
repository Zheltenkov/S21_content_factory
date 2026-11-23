"""Application assembly: the concept and content modules are mounted here and nowhere else.

Each module owns its routers, prompts, templates and storage. This file adds only what is common:
host and origin guards, the pages, configuration for the browser and static files.
"""

import hashlib
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT.parent / ".env", override=False)

from . import bridge  # noqa: E402 - modules read the environment loaded above
from . import usage as project_usage  # noqa: E402
from .concept import runs  # noqa: E402
from .concept.dialogue import router as dialogue_router  # noqa: E402
from .concept.freshness import router as freshness_router  # noqa: E402
from .concept.freshness import search_model  # noqa: E402
from .concept.planning import PROMPT_VERSION  # noqa: E402
from .content import api as content  # noqa: E402
from .content.writer import CONTENT_VERSION  # noqa: E402
from .core import usage  # noqa: E402
from .core.access import current_owner  # noqa: E402
from .core.llm import model_configuration, reviewer_model  # noqa: E402
from .evaluation import api as evaluation  # noqa: E402
from .evaluation import store as evaluation_store  # noqa: E402


@asynccontextmanager
async def lifespan(application: FastAPI):
    runs.recover_interrupted()
    content.recover()
    evaluation_store.recover()
    usage.ready()
    yield
    await evaluation.stop_all()
    await content.stop_all()
    await runs.stop_all()


# The project a request works on is named by its route: the id of a run, of a dialogue, of a content draft. Every
# call of a model the request makes is written into the account of that project (core/usage.py); a job it starts
# names its own run or draft (concept/runs.launch, content/flow.launch). The id is taken as the route read it — not
# from the text of the address, which in production begins with the prefix of the proxy and may spell the id in any
# of the ways a UUID is spelled.
PROJECT_PARAMS = ("run_id", "thread_id")


async def project_scope(request: Request) -> None:
    # `async`: the function must run in the task of the request, where the route then runs — a plain function is run
    # in a thread, and what it set would stay there.
    named = next((str(request.path_params[name]) for name in PROJECT_PARAMS if name in request.path_params), "")
    try:
        ident = str(UUID(named)) if named else ""
    except ValueError:
        ident = ""
    # Set for every request, an id or none: nothing the request before left is taken for this one's.
    usage.scope.set(ident)


app = FastAPI(title="Подготовка проекта", lifespan=lifespan, dependencies=[Depends(project_scope)])
app.add_middleware(TrustedHostMiddleware, allowed_hosts=os.getenv(
    "PROJECT_LAB_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver",
).split(","))


@app.middleware("http")
async def local_request_guard(request: Request, call_next):
    # Reject cross-origin writes and oversized bodies before parsing uploads.
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
            return JSONResponse({"detail": "Недопустимый источник запроса"}, status_code=403)
    limit = 6 * 1024 * 1024
    length = request.headers.get("content-length", "0")
    if not length.isdigit() or int(length) > limit:
        return JSONResponse({"detail": "Запрос слишком большой"}, status_code=413)
    received = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        received += len(chunk)
        if received > limit:
            return JSONResponse({"detail": "Запрос слишком большой"}, status_code=413)
        chunks.append(chunk)
    # Replay the bounded body to FastAPI after inspecting the stream.
    request._body = b"".join(chunks)
    return await call_next(request)


def page(name: str) -> FileResponse:
    """A page of the module. «no-cache»: the browser asks for the page every time — it is a few kilobytes. Without it
    a browser keeps a page by its own guess — the longer, the older the file — and after an update shows the old page
    with the old scripts: on 07.10.2026 the page of a document was the one of 30.09 for hours after the new one was
    served. The scripts and styles are named with their version and are kept."""
    return FileResponse(ROOT / "static" / name, headers={"Cache-Control": "no-cache"})


@app.get("/")
async def index():
    return page("chat.html")


@app.get("/document")
async def document():
    # The whole text, the README preview and the check-list as a readable page instead of a file download.
    return page("document.html")


@app.get("/classic")
async def classic():
    # The previous form-and-tabs interface stays available for comparison during the pilot.
    return page("index.html")


@app.get("/evaluation")
async def evaluation_dashboard():
    return page("evaluation.html")


@app.get("/api/curriculum-template.{kind}")
async def curriculum_template(kind: str):
    """Пустой план с теми заголовками, которые читает парсер.

    Собирается из той же таблицы колонок, поэтому разойтись с разбором не может. Отказ при
    загрузке плана отправляет сюда: методолог жаловался, что колонки «есть, а не находит», —
    они были, но названные по-своему.
    """
    from .concept.template import template_bytes

    media = {"csv": "text/csv; charset=utf-8",
             "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    if kind not in media:
        raise HTTPException(404, "Шаблон бывает в csv и xlsx")
    return Response(content=template_bytes(kind), media_type=media[kind],
                    headers={"Content-Disposition": f'attachment; filename="curriculum-template.{kind}"'})


@app.get("/api/config")
async def configuration():
    key, _, model = model_configuration()
    owner = current_owner.get()
    namespace = "s21.project-lab.v1" if owner == "local" else f"s21.project-lab.v1.{hashlib.sha256(owner.encode()).hexdigest()[:24]}"
    return {"model_ready": bool(key), "model": model, "reviewer_model": reviewer_model() or model,
            "version": PROMPT_VERSION, "content_version": CONTENT_VERSION, "search": "uploaded_materials_only",
            "search_model": search_model(), "storage_namespace": namespace, "home_url": "/app" if owner != "local" else "./"}


app.include_router(runs.router)
app.include_router(dialogue_router)
app.include_router(freshness_router)
# The content stage reads an approved concept only through the bridge: the modules never import each other.
content.configure(handoff=bridge.handoff)
app.include_router(content.router)
evaluation.context_provider = bridge.evaluation_context
app.include_router(evaluation.router)
# What a project cost, for its owner and the developer: read from the account the adapters of the models keep.
app.include_router(project_usage.router)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
