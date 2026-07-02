from content_gen.llm.observed_client import ObservedLLMClient
from content_gen.observability import (
    CompatibilityEvent,
    FallbackTraceEvent,
    LLMTraceRecorder,
    NodeTraceEvent,
    UnifiedTraceSink,
    build_unified_observability_report,
    record_runtime_fallback_traces,
    stable_input_hash,
)


def test_stable_input_hash_is_order_independent_for_json_payloads() -> None:
    assert stable_input_hash({"b": 2, "a": 1}) == stable_input_hash({"a": 1, "b": 2})


def test_node_trace_event_captures_required_generation_fields() -> None:
    event = NodeTraceEvent.from_node_execution(
        node="theory",
        inputs={"seed": {"title": "Проект"}},
        latency_ms=42.5,
        status="success",
        issues=["minor warning"],
        prompt_version="flow-v1",
        model="gpt-test",
        repair_attempts=1,
        output_schema="markdown,theory_parts",
    )

    payload = event.model_dump(mode="json")

    assert payload["node"] == "theory"
    assert payload["input_hash"]
    assert payload["prompt_version"] == "flow-v1"
    assert payload["model"] == "gpt-test"
    assert payload["latency_ms"] == 42.5
    assert payload["validation"]["status"] == "warning"
    assert payload["validation"]["issues_count"] == 1
    assert payload["repair_attempts"] == 1
    assert payload["output_schema"] == "markdown,theory_parts"


def test_observed_llm_client_records_complete_calls() -> None:
    class FakeLLM:
        model = "test-model"
        _last_finish_reason = "stop"
        _last_token_usage = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}

        def complete(self, **_kwargs):
            return "ok"

    recorder = LLMTraceRecorder()
    client = ObservedLLMClient(FakeLLM(), recorder, node="generation", agent="root").scoped(
        node="theory",
        agent="TheoryAgent",
        prompt_version="theory-v1",
    )

    assert client.complete(system="s", user="u", response_format="json_object") == "ok"

    event = recorder.events[0]
    assert event["node"] == "theory"
    assert event["agent"] == "TheoryAgent"
    assert event["prompt_version"] == "theory-v1"
    assert event["model"] == "test-model"
    assert event["schema"] == "json_object"
    assert event["tokens"]["total_tokens"] == 5
    assert event["status"] == "success"
    assert event["input_hash"]


def test_llm_trace_recorder_links_events_to_unified_sink() -> None:
    class FakeLLM:
        model = "test-model"
        _last_token_usage = {"prompt_tokens": 4, "completion_tokens": 5, "total_tokens": 9}
        _last_cost_usd = 0.003

        def complete(self, **_kwargs):
            return "ok"

    sink = UnifiedTraceSink(run_id="run-1", user_id="user-1")
    recorder = LLMTraceRecorder(sink=sink)
    client = ObservedLLMClient(FakeLLM(), recorder, node="practice", agent="PracticeAgent")

    client.complete(system="s", user="u", prompt_version="practice-v2")
    report = sink.report()

    assert report["events"][0]["event_type"] == "llm"
    assert report["events"][0]["run_id"] == "run-1"
    assert report["events"][0]["cost_usd"] == 0.003
    assert report["prompt_registry"][0]["prompt_version"] == "practice-v2"


def test_prompt_registry_uses_explicit_prompt_metadata_and_does_not_forward_to_provider() -> None:
    class FakeLLM:
        model = "test-model"
        _last_token_usage = {"prompt_tokens": 4, "completion_tokens": 5, "total_tokens": 9}

        def complete(self, **kwargs):
            self.kwargs = kwargs
            return "ok"

    sink = UnifiedTraceSink(run_id="run-1", user_id="user-1")
    recorder = LLMTraceRecorder(sink=sink)
    llm = FakeLLM()
    client = ObservedLLMClient(llm, recorder, node="theory", agent="TheoryAgent")

    client.complete(
        system="s",
        user="u",
        prompt_id="theory.system+theory.user_template",
        prompt_version="1.0.0",
        prompt_hash="prompt-hash",
        prompt_owner="methodology",
        prompt_input_schema="TheoryInput",
        prompt_output_schema="TheoryPart[]",
        prompt_source="content_gen/prompts/theory/system.md+content_gen/prompts/theory/user_template.md",
    )
    report = sink.report()

    assert "prompt_id" not in llm.kwargs
    assert "prompt_hash" not in llm.kwargs
    assert recorder.events[0]["metadata"]["prompt_id"] == "theory.system+theory.user_template"
    registry_entry = report["prompt_registry"][0]
    assert registry_entry["prompt_id"] == "theory.system+theory.user_template"
    assert registry_entry["prompt_hash"] == "prompt-hash"
    assert registry_entry["owner"] == "methodology"
    assert registry_entry["input_schema"] == "TheoryInput"
    assert registry_entry["output_schema"] == "TheoryPart[]"


def test_observed_llm_client_passes_node_as_gateway_role() -> None:
    class FakeGateway:
        supports_llm_roles = True
        model = "test-model"
        _last_finish_reason = "stop"
        _last_provider = "openai"
        _last_route = {"role": "theory", "provider": "openai", "model": "gpt-test"}
        _last_cost_usd = 0.001
        _last_budget_spent_usd = 0.002
        _last_token_usage = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}

        def complete(self, **kwargs):
            self.kwargs = kwargs
            return "ok"

    gateway = FakeGateway()
    recorder = LLMTraceRecorder()
    client = ObservedLLMClient(gateway, recorder, node="theory", agent="TheoryAgent")

    assert client.complete(system="s", user="u") == "ok"

    assert gateway.kwargs["llm_role"] == "theory"
    assert recorder.events[0]["metadata"]["provider"] == "openai"
    assert recorder.events[0]["metadata"]["route"]["role"] == "theory"
    assert recorder.events[0]["metadata"]["cost_usd"] == 0.001


def test_observed_llm_client_records_gateway_route_fallback() -> None:
    class FakeGateway:
        supports_llm_roles = True
        model = "deepseek-chat"
        _last_finish_reason = "stop"
        _last_provider = "deepseek"
        _last_route = {
            "role": "theory",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "fallback_errors": ["openai/gpt-test: provider down"],
        }
        _last_cost_usd = 0.001
        _last_budget_spent_usd = 0.002
        _last_token_usage = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}

        def complete(self, **kwargs):
            self.kwargs = kwargs
            return "ok"

    sink = UnifiedTraceSink(run_id="run-1", user_id="user-1")
    recorder = LLMTraceRecorder(sink=sink)
    client = ObservedLLMClient(FakeGateway(), recorder, node="theory", agent="TheoryAgent")

    assert client.complete(system="s", user="u") == "ok"

    report = sink.report()
    fallback = [event for event in report["events"] if event["event_type"] == "fallback"][0]
    assert fallback["trace_id"]
    assert fallback["node"] == "theory"
    assert fallback["fallback"]["type"] == "llm_provider_route_fallback"
    assert fallback["fallback"]["visible_to_user"] is False


def test_fallback_trace_event_captures_degradation_contract() -> None:
    event = FallbackTraceEvent.from_fallback(
        node="task_planning",
        fallback_type="default_task_plan",
        reason="planner failed",
        quality_risk="medium",
        visible_to_user=True,
        inputs={"title": "Проект"},
        trace={"resolved_tasks_count": 2},
    )

    payload = event.model_dump(mode="json")
    assert payload["trace_id"]
    assert payload["node"] == "task_planning"
    assert payload["fallback_type"] == "default_task_plan"
    assert payload["reason"] == "planner failed"
    assert payload["quality_risk"] == "medium"
    assert payload["visible_to_user"] is True
    assert payload["trace"] == {"resolved_tasks_count": 2}
    assert payload["input_hash"]


def test_compatibility_event_captures_legacy_contract() -> None:
    event = CompatibilityEvent(
        source="paused_generation_codec",
        compatibility_type="unknown_paused_type",
        reason="unsupported stored type",
        risk="medium",
        metadata={"type_name": "old.module:Thing"},
    )

    payload = event.model_dump(mode="json")
    assert payload["source"] == "paused_generation_codec"
    assert payload["compatibility_type"] == "unknown_paused_type"
    assert payload["metadata"] == {"type_name": "old.module:Thing"}


def test_record_runtime_fallback_traces_normalizes_events() -> None:
    class Runtime:
        pass

    runtime = Runtime()
    event = FallbackTraceEvent.from_fallback(
        node="quality",
        fallback_type="style_guard_markdown_boundary",
        reason="typed style guard unavailable",
        quality_risk="low",
    )

    record_runtime_fallback_traces(runtime, [event, {"node": "practice", "fallback_type": "critic"}])

    assert runtime.fallback_traces[0]["node"] == "quality"
    assert runtime.fallback_traces[0]["fallback_type"] == "style_guard_markdown_boundary"
    assert runtime.fallback_traces[1]["node"] == "practice"
    assert runtime.fallback_traces[1]["fallback_type"] == "critic"
    assert runtime.fallback_traces[1]["reason"] == "critic"
    assert runtime.fallback_traces[1]["visible_to_user"] is False
    assert runtime.fallback_traces[1]["trace_id"]


def test_unified_trace_sink_records_node_eval_artifacts_and_fallbacks() -> None:
    sink = UnifiedTraceSink(run_id="run-1", user_id="user-1")
    node = NodeTraceEvent.from_node_execution(
        node="theory",
        inputs={"seed": "x"},
        latency_ms=10,
        status="success",
        output_schema="markdown",
    )
    fallback = FallbackTraceEvent.from_fallback(
        node="practice",
        fallback_type="critic_json_recovery",
        reason="invalid json",
        quality_risk="medium",
    )

    sink.record_node_trace(node, output_artifact={"markdown": {"preview": "# A"}})
    sink.record_fallback_trace(fallback)
    report = sink.report()

    assert [event["event_type"] for event in report["events"]] == ["node", "fallback"]
    assert report["eval_artifacts"][0]["node"] == "theory"
    assert report["eval_artifacts"][0]["validator_report"]["status"] == "passed"


def test_build_unified_observability_report_from_legacy_trace_lists() -> None:
    report = build_unified_observability_report(
        run_id="run-2",
        user_id="user-2",
        node_traces=[{"node": "finalize", "input_hash": "abc"}],
        llm_traces=[{"node": "theory", "agent": "TheoryAgent", "input_hash": "def"}],
        fallback_traces=[{"node": "task_planning", "fallback_type": "default_task_plan"}],
        compatibility_events=[
            {
                "source": "paused_generation_codec",
                "compatibility_type": "unknown",
                "reason": "legacy",
            }
        ],
    )

    assert [event["event_type"] for event in report["events"]] == [
        "node",
        "llm",
        "fallback",
        "compatibility",
    ]
    assert report["events"][0]["run_id"] == "run-2"
    assert report["eval_artifacts"][0]["artifact_type"] == "node_output"
