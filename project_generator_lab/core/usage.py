"""What a project cost: one line for every call of a model, kept under the project it was made for.

The page and the stored drafts keep the tokens of the calls whose answers they show — and lose the rest: a search, a
second check of an answer, a call that failed after the model had answered. A developer who asks what one project
costs, dialogue included, needs every call. So the two adapters of the models (core.llm, core.search) write each call
here: when, for which project, which model, what was asked for (the name of the answer's schema — never the prompt or
the answer), tokens in and out, how long, how it ended, and what the gateway charged for it when it says.

The project a call belongs to is the id in the address of the request that made it (`scope`, set in app.py); a job
that outlives its request is run under the id of its own run or draft (`under`). A call made outside any project — a
benchmark, a script — is not written.
"""

import json
import logging
import math
import os
import re
import threading
from collections.abc import Iterable
from contextvars import Context, ContextVar, copy_context
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .access import current_owner
from .contracts import Contract

logger = logging.getLogger(__name__)
USAGE_DIR = Path(os.getenv("PROJECT_LAB_USAGE_DIR", Path(__file__).resolve().parents[1] / "data" / "usage"))
# The id of a run, a thread or a draft: what the files of a project are named by. Nothing else names a file here.
SCOPE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
scope: ContextVar[str] = ContextVar("project_lab_usage_scope", default="")
writing = threading.Lock()


def under(key: str) -> Context:
    """The context in hand, with the calls written under this id: what a job that outlives its request is run in."""
    context = copy_context()
    context.run(scope.set, key.lower())
    return context


class Call(Contract):
    at: str
    scope: str
    owner: str = "local"
    kind: Literal["model", "search"] = "model"
    asked: str  # the name of the schema of the answer: «TaskDraft», «CriticReport»; «search_text» for a free-text search
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_ms: int = 0
    repairs: int = 0
    outcome: str = "ok"
    # What the gateway charged for this call, in roubles; None — it did not say.
    cost_rub: float | None = None


def charged(usage: object) -> float | None:
    """What the gateway charged for a call, in roubles: Polza names it beside the tokens as «cost_rub» («списано с
    баланса клиента»). Not a field of the OpenAI contract: read where the client library keeps what it does not know,
    and absent is absent — not zero. «cost», which other gateways give in their own currency, is not read: a sum of
    dollars under the sign of the rouble would be a wrong price."""
    extra = getattr(usage, "model_extra", None) or (usage if isinstance(usage, dict) else {})
    value = extra.get("cost_rub") if isinstance(extra, dict) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def plain(value: object) -> str:
    """A text as it can be stored: half of a character pair, which a gateway's cut-off answer may end with, is not a
    character and becomes «?»."""
    return str(value).encode("utf-8", "replace").decode("utf-8")


def record(kind: Literal["model", "search"], asked: str, model: str, input_tokens: int, output_tokens: int, elapsed_ms: int,
           repairs: int = 0, outcome: str = "ok", cost_rub: float | None = None) -> None:
    """Write the call under the project of the request in hand. Never raises, whatever it is given: an account that
    cannot be written must not cost the call it is about."""
    key = scope.get()
    if not SCOPE.fullmatch(key):
        return
    try:
        call = Call(at=datetime.now(UTC).isoformat(), scope=key, owner=plain(current_owner.get()), kind=kind, asked=plain(asked), model=plain(model),
                    input_tokens=int(input_tokens), output_tokens=int(output_tokens), elapsed_ms=int(elapsed_ms), repairs=int(repairs),
                    outcome=plain(outcome)[:80], cost_rub=cost_rub)
        # One line of ASCII: a line separator of another alphabet inside a field must not cut the line in two.
        line = json.dumps(call.model_dump(), ensure_ascii=True) + "\n"
        with writing:
            USAGE_DIR.mkdir(parents=True, exist_ok=True)
            with (USAGE_DIR / f"{key}.jsonl").open("ab+") as file:
                # A line cut short — the server stopped while writing it — must not take this one with it.
                if file.seek(0, os.SEEK_END):
                    file.seek(-1, os.SEEK_END)
                    if file.read(1) != b"\n":
                        file.write(b"\n")
                file.write(line.encode("ascii"))
    except Exception as error:  # noqa: BLE001 - see the docstring
        logger.warning("usage of %s not written: %s", key, type(error).__name__)


def ready() -> bool:
    """Whether the account can be written where it is kept — asked once when the server starts, so that a folder the
    server may not write to (a read-only image, a volume not mounted) is one clear line in the log, not a silence."""
    try:
        USAGE_DIR.mkdir(parents=True, exist_ok=True)
        probe = USAGE_DIR / ".writable"
        probe.write_text("", encoding="ascii")
        probe.unlink()
    except OSError as error:
        logger.error("the account of model calls cannot be written to %s (%s): set PROJECT_LAB_USAGE_DIR to a folder the server "
                     "may write to. The calls themselves are not affected.", USAGE_DIR, type(error).__name__)
        return False
    return True


def calls_of(scopes: Iterable[str], owner: str | None = None) -> list[Call]:
    """Every call written under these ids, in the order they were made. A line that does not read — cut short,
    damaged, not text at all — is passed over: it must not hide the rest. `owner` — only this owner's calls."""
    found: list[Call] = []
    for key in dict.fromkeys(scopes):
        path = USAGE_DIR / f"{key}.jsonl"
        if not SCOPE.fullmatch(key) or not path.is_file():
            continue
        try:
            written = path.read_bytes()
        except OSError:
            continue
        # By the byte of the line's end only: read as text, the file would be cut at every separator Unicode knows.
        for line in written.split(b"\n"):
            try:
                call = Call.model_validate_json(line)
            except ValueError:
                continue
            if owner is None or call.owner == owner:
                found.append(call)
    return sorted(found, key=lambda call: call.at)


def price(prices: dict[str, dict[str, float]], model: str, input_tokens: int, output_tokens: int) -> float | None:
    """What the tokens cost by the price list, in its currency per million tokens; None — the model is not in it."""
    listed = prices.get(model)
    if not listed:
        return None
    return (input_tokens * listed["input"] + output_tokens * listed["output"]) / 1_000_000


def total(calls: list[Call], prices: dict[str, dict[str, float]]) -> dict[str, Any]:
    """The sum of some calls: how many, of them failed, tokens, the price by the list and what the gateway charged.
    The price by the list is of the calls whose model is listed — `unpriced` counts the rest, so a sum that leaves
    calls out says so. `charged_rub` is None when no call named what it cost, `charged_calls` — how many did."""
    priced = [(call, price(prices, call.model, call.input_tokens, call.output_tokens)) for call in calls]
    reported = [call.cost_rub for call in calls if call.cost_rub is not None]
    return {
        "calls": len(calls), "failed": sum(call.outcome != "ok" for call in calls), "repairs": sum(call.repairs for call in calls),
        "input_tokens": sum(call.input_tokens for call in calls), "output_tokens": sum(call.output_tokens for call in calls),
        "seconds": round(sum(call.elapsed_ms for call in calls) / 1000),
        "price_usd": round(sum(cost for _, cost in priced if cost is not None), 4), "unpriced": sum(cost is None for _, cost in priced),
        "charged_rub": round(sum(reported), 4) if reported else None, "charged_calls": len(reported),
    }


def summary(calls: list[Call], prices: dict[str, dict[str, float]], stages: dict[str, str]) -> dict[str, Any]:
    """The account of a project: the whole, then by stage of the chain and by model. `stages` names the stage a call
    belongs to by what it asked for; a call it does not know is a stage under its own name."""
    by_stage: dict[str, list[Call]] = {}
    by_model: dict[str, list[Call]] = {}
    for call in calls:
        by_stage.setdefault(stages.get(call.asked, call.asked), []).append(call)
        by_model.setdefault(call.model, []).append(call)
    order = list(dict.fromkeys(stages.values()))
    named = sorted(by_stage, key=lambda name: (order.index(name) if name in order else len(order), name))
    return {
        **total(calls, prices), "first_at": calls[0].at if calls else None, "last_at": calls[-1].at if calls else None,
        "stages": [{"stage": name, **total(by_stage[name], prices)} for name in named],
        "models": [{"model": name, **total(found, prices)} for name, found in sorted(by_model.items())],
    }


def read_prices(path: Path) -> dict[str, dict[str, float]]:
    """The price list of the module; empty when it is not there or does not read — then nothing is priced."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(name): {"input": float(one["input"]), "output": float(one["output"])} for name, one in data["prices"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}
