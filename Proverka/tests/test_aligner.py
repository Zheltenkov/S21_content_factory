from __future__ import annotations

import json

from content_audit.aligner import OpenRouterJudge
from content_audit.corpus_evaluation import GoldCorpusCase, PredictedCorpusItem


class _BrokenClient:
    def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, object]:
        del system_prompt, user_prompt
        raise RuntimeError("bad json")


def test_openrouter_judge_caches_model_errors_as_negative_decision(workspace_tmp_path) -> None:
    cache_path = workspace_tmp_path / "judge_cache.json"
    judge = OpenRouterJudge("test-key", "test-model", str(cache_path))
    judge.client = _BrokenClient()
    gold = GoldCorpusCase(
        case_id="g1",
        row_number=1,
        raw_project="Project",
        matched_project="Project",
        project_id="p1",
        criterion="correctness",
        gold_text="Expected defect",
    )
    pred = PredictedCorpusItem(
        finding_id="f1",
        project_id="p1",
        project="Project",
        criterion="correctness",
        checker_name="checker",
        found_text="Found defect",
    )

    same, confidence, reason = judge.same_defect(gold, pred)

    assert same is False
    assert confidence == 0.0
    assert reason.startswith("judge_error:")
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert len(payload) == 1
    cached = next(iter(payload.values()))
    assert cached["same_defect"] is False
