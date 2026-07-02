"""LLM-as-judge aligner: decides "same defect?" for gold<->finding pairs.

Two backends:
  * openrouter  - real model via content_audit.openrouter (run on your machine,
                  where OpenRouter is reachable; uses OPEN_ROUTER_API_KEY from .env)
  * offline     - semantic stand-in (Russian light stemmer + rare-token overlap),
                  used here because the sandbox blocks OpenRouter.

Cost control: only candidate pairs that pass a cheap lexical pre-screen
(same project + prescreen score, top-K per gold case) are sent to the judge.
A JSON cache avoids paying twice for the same pair.

Run (offline, here):
    PYTHONPATH=src python3 llm_judge_matcher.py --backend offline \
        --report .tmp/metrics_evaluation_full/audit_report/report.json \
        --gold metrics/<gold>.xlsx

Run (real model, on your machine):
    PYTHONPATH=src python3 llm_judge_matcher.py --backend openrouter \
        --report <report.json> --gold metrics/<gold>.xlsx --model qwen/qwen-2.5-coder-32b-instruct
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# reuse the shims + helpers from the lexical prototype
import prototype_aligner as proto
from prototype_aligner import (
    extract_anchors,
    content_tokens,
    content_similarity,
    phrase_anchor_hit,
    dedupe_mirror,
    is_placeholder,
    is_opinion,
    per_criterion,
    print_table,
)

from content_audit.domain import AuditReport
from content_audit.corpus_evaluation import (
    _gold_cases_from_items,
    _is_strict_evaluation_signal,
    _line_relation,
    _match_gold_cases,
    _predicted_items_from_report,
    load_gold_items,
    _project_candidates_from_report,
    _normalize_match_text,
    _same_missing_artifact_signal,
)

PROMPTS = json.loads((Path(__file__).parent / "llm_judge_prompts.json").read_text(encoding="utf-8"))

PRESCREEN_MIN = 0.10   # lexical floor to even consider a pair
TOPK = 6               # max candidate findings judged per gold case
JUDGE_ACCEPT = 0.55    # min judge confidence to accept "same defect"

# crude Russian suffix stripper for the offline semantic stand-in
_SUFFIXES = sorted(
    [
        "ами", "ями", "ому", "ему", "ого", "его", "ыми", "ими", "ая", "яя", "ое",
        "ее", "ые", "ие", "ой", "ей", "ом", "ем", "ах", "ях", "ов", "ев", "ам",
        "ям", "ть", "ся", "ие", "ия", "ию", "ий", "ть", "ла", "ло", "ли", "на",
        "ну", "ет", "ут", "ют", "ат", "ят", "а", "я", "о", "е", "ы", "и", "у",
        "ю", "й", "ь",
    ],
    key=len,
    reverse=True,
)


def stem(token: str) -> str:
    for suf in _SUFFIXES:
        if len(token) - len(suf) >= 4 and token.endswith(suf):
            return token[: -len(suf)]
    return token


def stem_tokens(text: str) -> set:
    return {stem(t) for t in content_tokens(text)}


# ---------------- judge backends ----------------

class OfflineJudge:
    name = "offline"

    def __init__(self):
        self.calls = 0

    def same_defect(self, gold, pred):
        self.calls += 1
        ga = extract_anchors(gold.gold_text)
        pa = extract_anchors(pred.found_text + " " + (pred.file_path or ""))
        if ga.urls & pa.urls or ga.files & pa.files or phrase_anchor_hit(ga, pa):
            return True, 0.9, "shared anchor"
        if _same_missing_artifact_signal(gold.gold_text, pred.found_text):
            return True, 0.85, "missing-artifact signal"
        gs, fs = stem_tokens(gold.gold_text), stem_tokens(pred.found_text)
        if not gs or not fs:
            return False, 0.0, "empty"
        rare_shared = {t for t in (gs & fs) if len(t) >= 5}
        if len(rare_shared) >= 2:
            return True, 0.72, "shared rare stems: " + ",".join(sorted(rare_shared)[:3])
        jac = len(gs & fs) / len(gs | fs)
        line = _line_relation(gold.line_start, gold.line_end, pred.line_start, pred.line_end)
        if line == "overlap" and (gs & fs):
            return True, 0.7, "line overlap + shared stem"
        if jac >= 0.34:
            return True, 0.6, "stem jaccard %.2f" % jac
        return False, round(jac, 2), "low overlap"


class OpenRouterJudge:
    name = "openrouter"

    def __init__(self, api_key, model, cache_path):
        from content_audit.openrouter import OpenRouterClient

        self.client = OpenRouterClient(api_key=api_key, model=model)
        self.calls = 0
        self.cache_path = Path(cache_path)
        self.cache = json.loads(self.cache_path.read_text(encoding="utf-8")) if self.cache_path.exists() else {}

    def _key(self, gold, pred):
        raw = (gold.gold_text + "||" + pred.found_text).encode("utf-8")
        return hashlib.sha1(raw).hexdigest()

    def same_defect(self, gold, pred):
        key = self._key(gold, pred)
        if key in self.cache:
            v = self.cache[key]
            return bool(v["same_defect"]), float(v.get("confidence", 0.6)), v.get("reason", "cache")
        loc = (pred.file_path or "") + (":%s" % pred.line_start if pred.line_start else "")
        user = PROMPTS["user_template"].format(
            gold_criterion=gold.criterion,
            gold_text=gold.gold_text[:1200],
            pred_criterion=pred.criterion,
            pred_loc=loc,
            pred_text=pred.found_text[:1200],
        )
        self.calls += 1
        data = self.client.complete_json(PROMPTS["system"], user)
        same = bool(data.get("same_defect"))
        conf = float(data.get("confidence", 0.6) or 0.6)
        reason = str(data.get("reason", ""))[:200]
        self.cache[key] = {"same_defect": same, "confidence": conf, "reason": reason}
        self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1), encoding="utf-8")
        return same, conf, reason


# ---------------- prescreen + assignment ----------------

def prescreen(gold, pred):
    ga = extract_anchors(gold.gold_text)
    pa = extract_anchors(pred.found_text + " " + (pred.file_path or ""))
    if ga.urls & pa.urls or ga.files & pa.files or phrase_anchor_hit(ga, pa):
        return 1.0
    if _same_missing_artifact_signal(gold.gold_text, pred.found_text):
        return 0.95
    score = content_similarity(gold.gold_text, pred.found_text)
    if _line_relation(gold.line_start, gold.line_end, pred.line_start, pred.line_end) in ("overlap", "near"):
        score += 0.1
    return score


def match_judge(gold_cases, predicted_items, judge):
    by_project = defaultdict(list)
    for p in predicted_items:
        by_project[p.project_id].append(p)

    accepted = []  # (confidence, gold_id, pred_id, same_criterion)
    for gold in gold_cases:
        cands = []
        for pred in by_project.get(gold.project_id, []):
            s = prescreen(gold, pred)
            if s >= PRESCREEN_MIN:
                cands.append((s, pred))
        cands.sort(key=lambda x: x[0], reverse=True)
        for _, pred in cands[:TOPK]:
            same, conf, _reason = judge.same_defect(gold, pred)
            if same and conf >= JUDGE_ACCEPT:
                accepted.append((conf, gold.case_id, pred.finding_id, gold.criterion == pred.criterion))

    # two-phase one-to-one: same-criterion first, then cross-criterion leftovers
    assigned_g, assigned_p, matched = set(), set(), {}

    def assign(rows):
        for conf, gid, pid, _ in sorted(rows, key=lambda x: x[0], reverse=True):
            if gid in assigned_g or pid in assigned_p:
                continue
            assigned_g.add(gid)
            assigned_p.add(pid)
            matched[gid] = pid

    assign([r for r in accepted if r[3]])
    assign([r for r in accepted if not r[3]])
    return matched, set(matched.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--backend", choices=["offline", "openrouter"], default="offline")
    ap.add_argument("--model", default="qwen/qwen-2.5-coder-32b-instruct")
    args = ap.parse_args()

    report = AuditReport.model_validate(json.loads(Path(args.report).read_text(encoding="utf-8")))
    units_by_id = {u.unit_id: u for u in report.units}
    candidates = _project_candidates_from_report(report)
    gold_items, _ = load_gold_items(Path(args.gold), candidates)
    gold_cases = _gold_cases_from_items(gold_items)
    evaluated = {g.criterion for g in gold_cases}

    all_pred = _predicted_items_from_report(report, units_by_id)
    in_scope = [p for p in all_pred if p.criterion in evaluated and _is_strict_evaluation_signal(p)]
    cleaned = dedupe_mirror([p for p in in_scope if not is_placeholder(p)])

    if args.backend == "offline":
        judge = OfflineJudge()
    else:
        from content_audit.env import get_env_value, load_env_file

        env = load_env_file(Path(".env"))
        api_key = get_env_value(("OPENROUTER_API_KEY", "OPEN_ROUTER_API_KEY"), env)
        if not api_key:
            print("No OpenRouter API key found in .env")
            return 1
        judge = OpenRouterJudge(api_key, args.model, ".tmp/llm_judge_cache.json")

    # baseline (old strict matcher) for reference
    old_matches, _ = _match_gold_cases(gold_cases, in_scope)
    old_pred = {m.found_finding_id for m in old_matches if m.counted}
    old_gold = {mg.case_id for mg, m in zip(gold_cases, old_matches) if m.counted}
    print_table("OLD matcher (line+text, hard criterion)",
                per_criterion(gold_cases, in_scope, old_pred, old_gold))

    matched, matched_pred = match_judge(gold_cases, cleaned, judge)
    print_table("LLM-JUDGE matcher (%s backend)" % judge.name,
                per_criterion(gold_cases, cleaned, matched_pred, set(matched.keys())))

    defect_cases = [g for g in gold_cases if not is_opinion(g.gold_text)]
    matched_d, matched_pred_d = match_judge(defect_cases, cleaned, judge)
    print_table("LLM-JUDGE matcher, recall on defects only (opinions excluded)",
                per_criterion(defect_cases, cleaned, matched_pred_d, set(matched_d.keys())))

    print("")
    print("Backend: {}; judge calls: {}".format(judge.name, judge.calls))
    print("Gold cases: {}; predicted in scope: {}; after cleanup: {}".format(
        len(gold_cases), len(in_scope), len(cleaned)))


if __name__ == "__main__":
    sys.exit(main())
