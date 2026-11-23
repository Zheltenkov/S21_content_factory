"""The account of what a project cost: every call of a model is written under the project it was made for."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError
from project_generator_lab.core import llm, search, usage
from project_generator_lab.core.access import current_owner
from project_generator_lab.tests.core.test_llm_contract import Provider, Tiny, reply

ONE = "3b0978c6-8e12-4299-8596-e7a696071369"
TWO = "d3f55f69-cb54-47d5-9396-abb1dcf9cc8b"
PRICES = {"writer": {"input": 2.0, "output": 10.0}}
PACKAGE = Path(usage.__file__).resolve().parents[1]


@pytest.fixture
def within():
    """A request that works on project ONE."""
    token = usage.scope.set(ONE)
    yield ONE
    usage.scope.reset(token)


def call(**given):
    return usage.Call(**{"at": "2026-10-07T08:00:00+00:00", "scope": ONE, "asked": "TaskDraft", "model": "writer", **given})


def test_the_tests_do_not_write_into_the_working_account():
    assert usage.USAGE_DIR != PACKAGE / "data" / "usage" and PACKAGE not in usage.USAGE_DIR.parents


def test_a_call_is_written_under_the_project_of_the_request(within):
    usage.record("model", "TaskDraft", "writer", 1200, 300, 4500, repairs=1, cost_rub=1.25)
    usage.record("search", "search_text", "finder", 40, 60, 900, outcome="failed:http502")
    lines = [json.loads(line) for line in (usage.USAGE_DIR / f"{ONE}.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [set(line) for line in lines] == [set(usage.Call.model_fields)] * 2
    first, second = usage.calls_of([ONE])
    assert first.model_dump(exclude={"at"}) == {
        "scope": ONE, "owner": "local", "kind": "model", "asked": "TaskDraft", "model": "writer", "input_tokens": 1200,
        "output_tokens": 300, "elapsed_ms": 4500, "repairs": 1, "outcome": "ok", "cost_rub": 1.25}
    assert (second.kind, second.outcome, second.cost_rub, second.repairs) == ("search", "failed:http502", None, 0)
    assert first.at <= second.at and first.at.endswith("+00:00")


def test_the_folder_of_the_account_is_made_with_the_folders_above_it(within, tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "USAGE_DIR", tmp_path / "data" / "not" / "yet" / "usage")
    usage.record("model", "TaskDraft", "writer", 1, 1, 1)
    assert len(usage.calls_of([ONE])) == 1


def test_a_file_of_the_folder_that_no_id_names_is_not_read(within):
    usage.record("model", "TaskDraft", "writer", 1, 1, 1)
    line = (usage.USAGE_DIR / f"{ONE}.jsonl").read_bytes()
    for name in ("notes", "..", ONE.upper(), ONE[:8]):
        if name != "..":
            (usage.USAGE_DIR / f"{name}.jsonl").write_bytes(line)
        assert usage.calls_of([name]) == [], name


def test_a_call_outside_any_project_is_not_written():
    assert usage.scope.get() == ""
    usage.record("model", "TaskDraft", "writer", 1, 1, 1)
    assert not usage.USAGE_DIR.exists()


@pytest.mark.parametrize("key", ["../../evil", "content-d3f55f69", ONE.upper(), ONE + "/x", ""])
def test_only_an_id_names_a_file(key):
    token = usage.scope.set(key)
    try:
        usage.record("model", "TaskDraft", "writer", 1, 1, 1)
    finally:
        usage.scope.reset(token)
    assert not usage.USAGE_DIR.exists()
    assert usage.calls_of([key]) == []


def test_the_owner_of_the_request_is_kept_and_asked_for(within):
    usage.record("model", "TaskDraft", "writer", 1, 1, 1)
    token = current_owner.set("user-7")
    try:
        usage.record("model", "Concept", "writer", 2, 2, 2)
    finally:
        current_owner.reset(token)
    assert [c.owner for c in usage.calls_of([ONE])] == ["local", "user-7"]
    assert [c.asked for c in usage.calls_of([ONE], "user-7")] == ["Concept"]
    assert usage.calls_of([ONE], "nobody") == []


def test_an_account_that_cannot_be_written_does_not_break_the_call(within, tmp_path, monkeypatch, caplog):
    blocked = tmp_path / "taken"
    blocked.write_text("a file where the folder should be", encoding="utf-8")
    monkeypatch.setattr(usage, "USAGE_DIR", blocked)
    usage.record("model", "TaskDraft", "writer", 1, 1, 1)
    assert blocked.read_text(encoding="utf-8") == "a file where the folder should be"
    assert f"usage of {ONE} not written: " in caplog.text and caplog.text.rstrip().endswith(("FileExistsError", "NotADirectoryError"))


@pytest.mark.parametrize("given", [
    {"input_tokens": "много"}, {"output_tokens": None}, {"elapsed_ms": object()}, {"kind": "чужой"}, {"cost_rub": "дорого"},
])
def test_whatever_it_is_given_the_account_does_not_raise(within, given, caplog):
    """It is called from inside a model call: an answer of the gateway it cannot write down must cost the account its
    line, never the methodologist the answer."""
    fine = {"kind": "model", "asked": "TaskDraft", "model": "writer", "input_tokens": 1, "output_tokens": 1, "elapsed_ms": 1}
    usage.record(**{**fine, **given})
    assert usage.calls_of([ONE]) == [] and f"usage of {ONE} not written" in caplog.text
    usage.record(**fine)  # type: ignore[arg-type]
    assert len(usage.calls_of([ONE])) == 1


def test_numbers_the_gateway_gives_as_fractions_and_marks_of_other_alphabets_are_written(within):
    # 12.0 tokens, a lone surrogate and the line separators Unicode knows inside the fields: one line each all the same.
    usage.record("model", "TaskDraft", "writer", 12.0, 3.9, 7.2, outcome="failed:\ud83d \u2028 \x85 конец")  # type: ignore[arg-type]
    usage.record("model", "Concept", "автор\u2029модель", 1, 1, 1)
    first, second = usage.calls_of([ONE])
    assert (first.input_tokens, first.output_tokens, first.elapsed_ms) == (12, 3, 7) and first.outcome.endswith("конец")
    assert second.model == "автор\u2029модель"
    assert (usage.USAGE_DIR / f"{ONE}.jsonl").read_bytes().count(b"\n") == 2


def test_a_line_cut_short_does_not_take_the_next_one_with_it(within):
    usage.record("model", "First", "writer", 1, 1, 1)
    path = usage.USAGE_DIR / f"{ONE}.jsonl"
    path.write_bytes(path.read_bytes() + b'{"at": "2026-10-07T08:00:00+00:00", "scope": "')   # the server stopped here
    usage.record("model", "Second", "writer", 1, 1, 1)
    usage.record("model", "Third", "writer", 1, 1, 1)
    assert [c.asked for c in usage.calls_of([ONE])] == ["First", "Second", "Third"]
    assert path.read_bytes().count(b"\n") == 4 and path.read_bytes().endswith(b"}\n")


def test_bytes_that_are_not_text_hide_only_their_own_line(within):
    usage.record("model", "First", "writer", 1, 1, 1)
    path = usage.USAGE_DIR / f"{ONE}.jsonl"
    path.write_bytes(path.read_bytes() + b"\xff\xfe not text at all\n" + b"\r\n")
    usage.record("model", "Second", "writer", 1, 1, 1)
    assert [c.asked for c in usage.calls_of([ONE])] == ["First", "Second"]


def test_the_folder_of_the_account_is_checked_when_the_server_starts(tmp_path, monkeypatch, caplog):
    assert usage.ready() is True and usage.USAGE_DIR.is_dir() and list(usage.USAGE_DIR.iterdir()) == []
    assert usage.ready() is True     # asked again when the server starts again: the folder is there already
    monkeypatch.setattr(usage, "USAGE_DIR", tmp_path / "not" / "yet" / "usage")
    assert usage.ready() is True and list(usage.USAGE_DIR.iterdir()) == []
    blocked = tmp_path / "taken"
    blocked.write_text("a file where the folder should be", encoding="utf-8")
    monkeypatch.setattr(usage, "USAGE_DIR", blocked / "usage")
    assert usage.ready() is False
    # mkdir reports a different native exception for a file used as a parent on Windows and POSIX.
    assert "PROJECT_LAB_USAGE_DIR" in caplog.text and str(blocked) in caplog.text
    assert "(NotADirectoryError)" in caplog.text or "(FileExistsError)" in caplog.text


def test_a_long_outcome_is_cut(within):
    usage.record("model", "TaskDraft", "writer", 1, 1, 1, outcome="failed:" + "я" * 200)
    assert len(usage.calls_of([ONE])[0].outcome) == 80


def test_calls_of_several_ids_come_in_the_order_they_were_made_and_a_damaged_line_hides_nothing():
    usage.USAGE_DIR.mkdir(parents=True)
    late, early, middle = (call(at=f"2026-10-07T0{hour}:00:00+00:00", asked=asked) for hour, asked in ((9, "late"), (7, "early"), (8, "middle")))
    (usage.USAGE_DIR / f"{ONE}.jsonl").write_text(f"{late.model_dump_json()}\nnot json\n{{\"at\": 1}}\n{early.model_dump_json()}\n", encoding="utf-8")
    (usage.USAGE_DIR / f"{TWO}.jsonl").write_text(middle.model_dump_json() + "\n", encoding="utf-8")
    assert [c.asked for c in usage.calls_of([ONE, TWO, ONE])] == ["early", "middle", "late"]
    assert [c.asked for c in usage.calls_of([TWO])] == ["middle"]
    assert usage.calls_of(["00000000-0000-0000-0000-000000000000"]) == []


@pytest.mark.parametrize("reported, cost", [
    (SimpleNamespace(model_extra={"cost_rub": 12.5, "cost": 0.1}), 12.5),
    # «cost» alone is another gateway's sum in its own currency: not roubles, not read.
    (SimpleNamespace(model_extra={"cost": 0.25}), None),
    (SimpleNamespace(model_extra={"cost_rub": float("nan")}), None),
    (SimpleNamespace(model_extra={"cost_rub": float("inf")}), None),
    (SimpleNamespace(model_extra={"cost_rub": 0}), 0.0),
    ({"cost_rub": 3}, 3.0),
    (SimpleNamespace(model_extra={"cost_rub": "12.5"}), None),
    (SimpleNamespace(model_extra={"cost_rub": True}), None),
    (SimpleNamespace(model_extra={}), None),
    (SimpleNamespace(model_extra=None), None),
    (SimpleNamespace(prompt_tokens=10), None),
    (None, None),
])
def test_the_cost_is_read_where_the_gateway_puts_it(reported, cost):
    found = usage.charged(reported)
    assert found == cost and (found is None or isinstance(found, float))


def test_the_cost_is_read_from_the_answer_of_the_gateway_as_the_client_library_gives_it():
    """The example of the usage block from the documentation of Polza (07.10.2026), parsed by the OpenAI client the
    adapters use: the cost is not a field of its contract and must survive the parsing."""
    from openai.types import CompletionUsage

    told = CompletionUsage.model_validate({"prompt_tokens": 10, "completion_tokens": 50, "total_tokens": 60, "cost_rub": 0.04131306,
                                           "cost": 0.04131306, "prompt_tokens_details": {"cached_tokens": 0}})
    assert usage.charged(told) == 0.04131306
    assert usage.charged(CompletionUsage(prompt_tokens=10, completion_tokens=50, total_tokens=60)) is None
    assert usage.charged(CompletionUsage.model_validate({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.5})) is None


def test_price_by_the_list():
    assert usage.price(PRICES, "writer", 1_000_000, 100_000) == 3.0
    assert usage.price(PRICES, "writer", 0, 0) == 0.0
    assert usage.price(PRICES, "unknown", 1_000_000, 1_000_000) is None
    assert usage.price({}, "writer", 1, 1) is None


def test_the_sum_says_what_it_leaves_out():
    calls = [call(input_tokens=1_000_000, output_tokens=100_000, elapsed_ms=61_400, repairs=1, cost_rub=250.0),
             call(input_tokens=500_000, output_tokens=0, elapsed_ms=600, outcome="failed:schema", cost_rub=90.5),
             call(model="finder", kind="search", asked="search_text", input_tokens=30, output_tokens=70, elapsed_ms=1_000)]
    assert usage.total(calls, PRICES) == {
        "calls": 3, "failed": 1, "repairs": 1, "input_tokens": 1_500_030, "output_tokens": 100_070, "seconds": 63,
        "price_usd": 4.0, "unpriced": 1, "charged_rub": 340.5, "charged_calls": 2}
    silent = usage.total(calls[2:], PRICES)
    assert (silent["price_usd"], silent["unpriced"], silent["charged_rub"], silent["charged_calls"]) == (0.0, 1, None, 0)
    assert usage.total([], PRICES) == {
        "calls": 0, "failed": 0, "repairs": 0, "input_tokens": 0, "output_tokens": 0, "seconds": 0, "price_usd": 0.0,
        "unpriced": 0, "charged_rub": None, "charged_calls": 0}
    assert usage.total([call(cost_rub=0.0)], PRICES)["charged_rub"] == 0.0
    # Seconds are thousandths of the milliseconds; the sums are kept to the fourth decimal.
    fine = usage.total([call(elapsed_ms=1_001_000, input_tokens=1, output_tokens=7, cost_rub=0.123449)], PRICES)
    assert (fine["seconds"], fine["price_usd"], fine["charged_rub"]) == (1001, 0.0001, 0.1234)


def test_the_account_goes_by_stage_in_the_order_of_the_chain_and_by_model():
    stages = {"Concept": "Замысел", "TaskDraft": "Задания", "PlainTaskDraft": "Задания", "CriticReport": "Критик"}
    calls = [call(at="2026-10-07T08:00:03+00:00", asked="CriticReport", model="critic", input_tokens=5),
             call(at="2026-10-07T08:00:02+00:00", asked="PlainTaskDraft", input_tokens=100),
             call(at="2026-10-07T08:00:01+00:00", asked="TaskDraft", input_tokens=10),
             call(at="2026-10-07T08:00:04+00:00", asked="Zebra", input_tokens=1),
             call(at="2026-10-07T08:00:05+00:00", asked="Apple", input_tokens=2),
             call(at="2026-10-07T08:00:00+00:00", asked="Concept", input_tokens=1000)]
    found = usage.summary(sorted(calls, key=lambda c: c.at), PRICES, stages)
    assert [(row["stage"], row["calls"], row["input_tokens"]) for row in found["stages"]] == [
        ("Замысел", 1, 1000), ("Задания", 2, 110), ("Критик", 1, 5), ("Apple", 1, 2), ("Zebra", 1, 1)]
    assert [(row["model"], row["calls"], row["unpriced"]) for row in found["models"]] == [("critic", 1, 1), ("writer", 5, 0)]
    assert (found["calls"], found["input_tokens"], found["unpriced"]) == (6, 1118, 1)
    assert (found["first_at"], found["last_at"]) == ("2026-10-07T08:00:00+00:00", "2026-10-07T08:00:05+00:00")
    empty = usage.summary([], PRICES, stages)
    assert (empty["calls"], empty["stages"], empty["models"], empty["first_at"], empty["last_at"]) == (0, [], [], None, None)


def test_the_price_list_of_the_module_reads_and_a_missing_one_prices_nothing(tmp_path):
    prices = usage.read_prices(PACKAGE / "concept" / "config" / "model_prices.json")
    assert prices and all(set(one) == {"input", "output"} and all(isinstance(v, float) for v in one.values()) for one in prices.values())
    assert usage.read_prices(tmp_path / "absent.json") == {}
    for text in ("not json", "{}", '{"prices": {"m": {"input": 1}}}', '{"prices": {"m": {"input": "x", "output": 1}}}', '{"prices": []}'):
        (tmp_path / "bad.json").write_text(text, encoding="utf-8")
        assert usage.read_prices(tmp_path / "bad.json") == {}
    (tmp_path / "good.json").write_text('{"currency": "USD", "prices": {"m": {"input": 1, "output": "2.5"}}}', encoding="utf-8")
    assert usage.read_prices(tmp_path / "good.json") == {"m": {"input": 1.0, "output": 2.5}}


def test_a_job_runs_under_its_own_id_and_leaves_the_request_as_it_was():
    token = current_owner.set("user-7")
    try:
        context = usage.under(TWO.upper())
    finally:
        current_owner.reset(token)
    assert context.run(usage.scope.get) == TWO and context.run(current_owner.get) == "user-7"
    assert usage.scope.get() == "" and current_owner.get() == "local"


# --- the adapters of the models write every call ---

def refused(status):
    request = httpx.Request("POST", "https://gateway.test/v1/chat/completions")
    return APIStatusError("refused", response=httpx.Response(status, request=request), body=None)  # type: ignore[arg-type]


def test_a_model_call_is_written_with_what_the_gateway_charged_for_every_attempt(within, monkeypatch):
    first = reply('{"wrong": 1}', usage=(100, 20))
    first.usage.model_extra = {"cost_rub": 1.5}
    second = reply('{"answer": "да"}', usage=(130, 10))
    second.usage.model_extra = {"cost_rub": 2.0}
    Provider(monkeypatch, [first, second])
    asyncio.run(llm.ModelClient().generate(Tiny, "Правила", "Входы"))
    [written] = usage.calls_of([ONE])
    assert (written.kind, written.asked, written.model, written.input_tokens, written.output_tokens, written.repairs, written.outcome, written.cost_rub) == (
        "model", "Tiny", "configured/model", 230, 30, 1, "ok", 3.5)


def test_a_gateway_that_names_no_cost_leaves_it_unknown(within, monkeypatch):
    Provider(monkeypatch, [reply('{"answer": "да"}')])
    asyncio.run(llm.ModelClient().generate(Tiny, "Правила", "Входы"))
    [written] = usage.calls_of([ONE])
    assert written.cost_rub is None and (written.input_tokens, written.output_tokens) == (10, 20)


def test_a_failed_model_call_is_written_with_what_it_had_taken(within, monkeypatch):
    cut = reply("{", finish="length", usage=(500, 16000))
    cut.usage.model_extra = {"cost_rub": 9.0}
    Provider(monkeypatch, [cut])
    with pytest.raises(llm.ModelUnavailable):
        asyncio.run(llm.ModelClient().generate(Tiny, "Правила", "Входы"))
    [written] = usage.calls_of([ONE])
    assert written.outcome.startswith("failed:") and (written.input_tokens, written.output_tokens, written.cost_rub) == (500, 16000, 9.0)


def test_an_answer_that_fails_the_schema_twice_is_written_as_failed_with_both_attempts(within, monkeypatch):
    Provider(monkeypatch, [reply('{"wrong": 1}', usage=(100, 20)), reply('{"wrong": 2}', usage=(130, 25))])
    with pytest.raises(llm.ModelUnavailable):
        asyncio.run(llm.ModelClient().generate(Tiny, "Правила", "Входы"))
    [written] = usage.calls_of([ONE])
    assert (written.outcome, written.input_tokens, written.output_tokens, written.repairs) == ("failed:schema", 230, 45, 1)


def test_a_gateway_that_fails_after_an_answer_is_written_with_what_the_answer_took(within, monkeypatch):
    for failure, outcome in ((refused(502), "failed:http502"), (TimeoutError("slow"), "failed:TimeoutError")):
        first = reply('{"wrong": 1}', usage=(100, 20))
        first.usage.model_extra = {"cost_rub": 1.5}
        Provider(monkeypatch, [first, failure])
        with pytest.raises(llm.ModelUnavailable):
            asyncio.run(llm.ModelClient().generate(Tiny, "Правила", "Входы"))
        written = usage.calls_of([ONE])[-1]
        assert (written.outcome, written.input_tokens, written.output_tokens, written.repairs, written.cost_rub) == (outcome, 100, 20, 1, 1.5)
    assert len(usage.calls_of([ONE])) == 2


def test_a_call_stopped_while_the_model_answers_is_written_and_the_stop_goes_on(within, monkeypatch):
    first = reply('{"wrong": 1}', usage=(9000, 3000))
    first.usage.model_extra = {"cost_rub": 7.0}
    Provider(monkeypatch, [first, asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(llm.ModelClient().generate(Tiny, "Правила", "Входы"))
    [written] = usage.calls_of([ONE])
    assert (written.outcome, written.input_tokens, written.output_tokens, written.repairs, written.cost_rub) == ("cancelled", 9000, 3000, 1, 7.0)
    for ask in (search.ask_text, lambda system, payload: search.ask(Tiny, system, payload)):
        Finder(monkeypatch, asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(ask("Найди", "{}"))
    assert [(c.kind, c.asked, c.outcome) for c in usage.calls_of([ONE])[1:]] == [("search", search.TEXT_SEARCH, "cancelled"), ("search", "Tiny", "cancelled")]


def test_paid_adds_what_was_reported_and_keeps_unknown_unknown():
    silent, told = SimpleNamespace(model_extra={}), SimpleNamespace(model_extra={"cost_rub": 2.0})
    assert llm.paid(None, None) is None and llm.paid(None, silent) is None
    assert llm.paid(None, told) == 2.0 and llm.paid(1.5, told) == 3.5
    assert llm.paid(1.5, silent) == 1.5 and llm.paid(1.5, None) == 1.5
    assert llm.paid(None, SimpleNamespace(model_extra={"cost_rub": 0.0})) == 0.0


class Finder:
    """The search gateway: answers from a script, as Provider does for the writer."""

    def __init__(self, monkeypatch, answer):
        monkeypatch.setattr(search, "model_configuration", lambda: ("secret-key", "https://gateway.test/v1", "configured/model"))
        monkeypatch.setattr(search, "search_model", lambda: "finder/model")
        monkeypatch.setattr(search, "AsyncOpenAI", self.open)
        self.answer = answer

    def open(self, **kwargs):
        finder = self

        class Session:
            async def __aenter__(self):
                async def create(**request):
                    if isinstance(finder.answer, BaseException):
                        raise finder.answer
                    return finder.answer

                return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

            async def __aexit__(self, *exc):
                return False

        return Session()


def found(content, finish="stop", tokens=(40, 60), cost_rub=None):
    counted = SimpleNamespace(prompt_tokens=tokens[0], completion_tokens=tokens[1], model_extra={} if cost_rub is None else {"cost_rub": cost_rub})
    answer = SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))], usage=counted)
    answer.model_dump = lambda: {"citations": ["https://example.org/a"]}
    return answer


def test_a_search_is_written_too(within, monkeypatch):
    Finder(monkeypatch, found("Нашлось: https://example.org/b", cost_rub=0.8))
    text, urls, metrics = asyncio.run(search.ask_text("Найди", "{}"))
    assert urls == ["https://example.org/a", "https://example.org/b"] and (metrics.model, metrics.input_tokens, metrics.output_tokens) == ("finder/model", 40, 60)
    Finder(monkeypatch, found('{"answer": "да"}', cost_rub=0.3))
    asyncio.run(search.ask(Tiny, "Найди", "{}"))
    assert [(c.kind, c.asked, c.model, c.input_tokens, c.output_tokens, c.outcome, c.cost_rub) for c in usage.calls_of([ONE])] == [
        ("search", search.TEXT_SEARCH, "finder/model", 40, 60, "ok", 0.8), ("search", "Tiny", "finder/model", 40, 60, "ok", 0.3)]


def test_a_failed_search_is_written_as_failed(within, monkeypatch):
    for answer in (refused(402), RuntimeError("down")):
        Finder(monkeypatch, answer)
        for ask in (search.ask_text("Найди", "{}"), search.ask(Tiny, "Найди", "{}")):
            with pytest.raises(llm.ModelUnavailable):
                asyncio.run(ask)
    Finder(monkeypatch, found('{"answer": "да"}', finish="length", cost_rub=0.2))
    with pytest.raises(llm.ModelUnavailable):
        asyncio.run(search.ask(Tiny, "Найди", "{}"))
    Finder(monkeypatch, found("не JSON", cost_rub=0.1))
    with pytest.raises(llm.ModelUnavailable):
        asyncio.run(search.ask(Tiny, "Найди", "{}"))
    assert [(c.asked, c.outcome, c.input_tokens, c.cost_rub) for c in usage.calls_of([ONE])] == [
        (search.TEXT_SEARCH, "failed:http402", 0, None), ("Tiny", "failed:http402", 0, None),
        (search.TEXT_SEARCH, "failed:RuntimeError", 0, None), ("Tiny", "failed:RuntimeError", 0, None),
        ("Tiny", "failed:length", 40, 0.2), ("Tiny", "failed:schema", 40, 0.1)]
    assert all(c.kind == "search" for c in usage.calls_of([ONE]))


def test_a_search_that_answers_nothing_is_a_failure_and_is_written(within, monkeypatch):
    empty = found("…", cost_rub=0.4)
    empty.choices = []
    for ask in (search.ask_text, lambda system, payload: search.ask(Tiny, system, payload)):
        Finder(monkeypatch, empty)
        with pytest.raises(llm.ModelUnavailable, match="пустой ответ"):
            asyncio.run(ask("Найди", "{}"))
    assert [(c.asked, c.outcome, c.input_tokens, c.cost_rub) for c in usage.calls_of([ONE])] == [
        (search.TEXT_SEARCH, "failed:empty", 40, 0.4), ("Tiny", "failed:empty", 40, 0.4)]
