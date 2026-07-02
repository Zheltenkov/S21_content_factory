from content_gen.exceptions import LLMAPIError
from content_gen.llm.factory import create_llm_client
from content_gen.llm.gateway import LLMUsageBudgetTracker


def test_memory_budget_tracker_uses_node_bucket_and_role_compatibility() -> None:
    tracker = LLMUsageBudgetTracker()

    tracker.record(user_id="u1", run_id="r1", node="theory", role="planner", cost_usd=0.25)

    assert tracker.spent(user_id="u1", run_id="r1", node="theory") == 0.25
    assert tracker.spent(user_id="u1", run_id="r1", role="planner") == 0.0


def test_memory_budget_tracker_rejects_spent_node_budget() -> None:
    tracker = LLMUsageBudgetTracker()
    tracker.record(user_id="u1", run_id="r1", node="practice", cost_usd=0.5)

    try:
        tracker.assert_within_budget(user_id="u1", run_id="r1", node="practice", budget_usd=0.5)
    except LLMAPIError as exc:
        assert "node=practice" in str(exc)
    else:
        raise AssertionError("budget check should reject spent node budget")


def test_factory_can_disable_db_budget_tracker(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BUDGET_DB_ENABLED", "false")

    client = create_llm_client(enable_cache=False, enable_batching=False)

    assert isinstance(client._budget_tracker, LLMUsageBudgetTracker)
