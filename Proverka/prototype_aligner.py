"""Prototype of the new aligner: anchor matching + soft criterion + line-as-bonus.

Reuses the loaders from content_audit.corpus_evaluation and only swaps the
gold<->finding matching. Computes recall/precision on the current gold set and
compares against the old (strict) matcher.

All Russian marker words live in prototype_markers.json (this source stays ASCII).

Run:
    PYTHONPATH=src python3 prototype_aligner.py \
        --report .tmp/metrics_evaluation_strict_rules_after_layers/audit_report/report.json \
        --gold metrics/<gold>.xlsx
"""

from __future__ import annotations

import argparse
import enum
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

# Shim for Python 3.10 (prod needs 3.11+, prototype does not).
if not hasattr(enum, "StrEnum"):
    class StrEnum(str, enum.Enum):
        def _generate_next_value_(name, start, count, last_values):  # type: ignore[override]
            return name.lower()

        def __str__(self) -> str:
            return str(self.value)

    enum.StrEnum = StrEnum  # type: ignore[attr-defined]

if "tomllib" not in sys.modules:
    try:
        import tomllib  # noqa: F401
    except ModuleNotFoundError:
        import tomli as _tomli
        sys.modules["tomllib"] = _tomli

from content_audit.domain import AuditReport
from content_audit.corpus_evaluation import (
    GoldCorpusCase,
    PredictedCorpusItem,
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

_MARKERS = json.loads((Path(__file__).parent / "prototype_markers.json").read_text(encoding="utf-8"))
PLACEHOLDER_MARKERS = tuple(_MARKERS["placeholders"])
OPINION_MARKERS = tuple(_MARKERS["opinions"])
RU_STOP = set(_MARKERS["ru_stop"])

STOPWORDS = set(
    "i v vo ne na chto on a to vse ona tak ego no da ty k u zhe vy za by po ee"
    " the a to of in is on are and not".split()
) | RU_STOP

URL_RE = re.compile(r"https?:[/\\]+[^\s)>\]\"'|]+", re.IGNORECASE)
FILE_RE = re.compile(
    r"[\w\-./]+\.(?:sql|docx?|md|ya?ml|png|jpe?g|pcapng|pcap|py|js|java|go|c|h|sh|txt|xlsx|csv)",
    re.IGNORECASE,
)
QUOTE_RE = re.compile(r"[`\u00ab\"']([^`\u00ab\u00bb\"']{3,80})[`\u00bb\"']")


@dataclass(frozen=True)
class Anchors:
    urls: frozenset
    files: frozenset
    phrases: frozenset


def _norm_url(url: str) -> str:
    u = url.lower().strip().rstrip(".,);]>")
    u = re.sub(r"https?:[/\\]+", "https://", u)
    return u.rstrip("/")


def extract_anchors(text: str) -> Anchors:
    urls = {_norm_url(m.group(0)) for m in URL_RE.finditer(text)}
    files = {Path(m.group(0).replace("\\", "/")).name.lower() for m in FILE_RE.finditer(text)}
    phrases = set()
    for m in QUOTE_RE.finditer(text):
        phrase = _normalize_match_text(m.group(1))
        if len(phrase.split()) >= 2:
            phrases.add(phrase)
    return Anchors(frozenset(urls), frozenset(files), frozenset(phrases))


def content_tokens(text: str) -> set:
    norm = _normalize_match_text(text)
    return {t for t in norm.split() if len(t) > 2 and t not in STOPWORDS}


def content_similarity(gold_text: str, found_text: str) -> float:
    g = content_tokens(gold_text)
    f = content_tokens(found_text)
    if not g or not f:
        return 0.0
    token_score = len(g & f) / min(len(g), len(f))
    seq = SequenceMatcher(a=_normalize_match_text(gold_text), b=_normalize_match_text(found_text)).ratio()
    return max(token_score, seq)


def phrase_anchor_hit(a: Anchors, b: Anchors) -> bool:
    if a.phrases & b.phrases:
        return True
    for p in a.phrases:
        for q in b.phrases:
            if p in q or q in p:
                return True
    return False


def is_placeholder(item: PredictedCorpusItem) -> bool:
    low = item.found_text.lower()
    return any(marker in low for marker in PLACEHOLDER_MARKERS)


def dedupe_mirror(items):
    """Collapse RU/EN and check-list mirror findings by text core."""

    mirror = {
        "readme.md": "readme",
        "readme_rus.md": "readme",
        "check-list.yml": "checklist",
        "check-list_rus.yml": "checklist",
    }
    seen = {}
    out = []
    for item in items:
        base = Path((item.file_path or "").replace("\\", "/")).name.lower()
        fam = mirror.get(base, base)
        core = " ".join(sorted(content_tokens(item.found_text)))[:120]
        key = (item.project_id, item.criterion, item.issue_type, fam, core)
        if key in seen:
            continue
        seen[key] = item
        out.append(item)
    return out


NEW_THRESHOLD = 0.5


@dataclass(frozen=True)
class NewCand:
    gold_id: str
    pred_id: str
    score: float
    match_type: str


def new_score(gold: GoldCorpusCase, pred: PredictedCorpusItem) -> NewCand:
    ga = extract_anchors(gold.gold_text)
    pa = extract_anchors(pred.found_text + " " + (pred.file_path or ""))
    line_rel = _line_relation(gold.line_start, gold.line_end, pred.line_start, pred.line_end)
    text = content_similarity(gold.gold_text, pred.found_text)

    shared_url = bool(ga.urls & pa.urls)
    shared_file = bool(ga.files & pa.files)
    shared_phrase = phrase_anchor_hit(ga, pa)
    crit_bonus = 0.12 if gold.criterion == pred.criterion else -0.1

    def cand(score, mtype):
        return NewCand(gold.case_id, pred.finding_id, min(1.0, max(0.1, score + crit_bonus)), mtype)

    if _same_missing_artifact_signal(gold.gold_text, pred.found_text):
        return cand(0.85, "artifact_missing")
    if shared_url:
        return cand(0.9, "anchor_url")
    if shared_phrase:
        return cand(0.88, "anchor_phrase")
    if shared_file:
        return cand(0.82, "anchor_file")
    if line_rel == "overlap" and text >= 0.12:
        return cand(0.85, "line_and_text")
    if line_rel == "overlap":
        return cand(0.7, "line_overlap")
    if line_rel == "near" and text >= 0.12:
        return cand(0.72, "near_line_and_text")
    if text >= 0.35:
        return cand(text, "text_similarity")
    return cand(text, "weak")


def _assign(cands, assigned_g, assigned_p, matched):
    for c in sorted(cands, key=lambda x: x.score, reverse=True):
        if c.gold_id in assigned_g or c.pred_id in assigned_p:
            continue
        assigned_g.add(c.gold_id)
        assigned_p.add(c.pred_id)
        matched[c.gold_id] = c


def match_new(gold_cases, predicted_items):
    pred_crit = {p.finding_id: p.criterion for p in predicted_items}
    gold_crit = {g.case_id: g.criterion for g in gold_cases}
    same, cross = [], []
    for gold in gold_cases:
        for pred in predicted_items:
            if gold.project_id != pred.project_id:  # criterion does NOT gate, but is preferred
                continue
            c = new_score(gold, pred)
            if c.score < NEW_THRESHOLD:
                continue
            (same if gold.criterion == pred.criterion else cross).append(c)
    assigned_g, assigned_p, matched = set(), set(), {}
    _assign(same, assigned_g, assigned_p, matched)   # phase 1: same-criterion
    _assign(cross, assigned_g, assigned_p, matched)  # phase 2: cross-criterion leftovers
    return matched, assigned_p


def is_opinion(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in OPINION_MARKERS)


def per_criterion(gold_cases, predicted_items, matched_pred_ids, counted_gold_ids):
    crits = sorted({g.criterion for g in gold_cases} | {p.criterion for p in predicted_items})
    rows = []
    for crit in crits:
        gold = [g for g in gold_cases if g.criterion == crit]
        pred = [p for p in predicted_items if p.criterion == crit]
        tp = sum(1 for g in gold if g.case_id in counted_gold_ids)
        matched_pred = sum(1 for p in pred if p.finding_id in matched_pred_ids)
        fp = len(pred) - matched_pred
        fn = len(gold) - tp
        prec = round(tp / (tp + fp), 3) if tp + fp else 0.0
        rec = round(tp / (tp + fn), 3) if tp + fn else 0.0
        rows.append((crit, len(gold), len(pred), tp, fp, fn, prec, rec))
    return rows


def print_table(title, rows):
    print("")
    print("=== {} ===".format(title))
    head = "{:<22}{:>5}{:>6}{:>4}{:>5}{:>4}{:>7}{:>7}"
    print(head.format("criterion", "gold", "pred", "TP", "FP", "FN", "prec", "rec"))
    for crit, gold, pred, tp, fp, fn, prec, rec in rows:
        print(head.format(crit, gold, pred, tp, fp, fn, prec, rec))
    tp = sum(r[3] for r in rows)
    fp = sum(r[4] for r in rows)
    fn = sum(r[5] for r in rows)
    prec = round(tp / (tp + fp), 3) if tp + fp else 0.0
    rec = round(tp / (tp + fn), 3) if tp + fn else 0.0
    print(head.format("TOTAL (micro)", "", "", tp, fp, fn, prec, rec))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--gold", required=True)
    args = ap.parse_args()

    report = AuditReport.model_validate(json.loads(Path(args.report).read_text(encoding="utf-8")))
    units_by_id = {u.unit_id: u for u in report.units}
    candidates = _project_candidates_from_report(report)
    gold_items, _ = load_gold_items(Path(args.gold), candidates)
    gold_cases = _gold_cases_from_items(gold_items)
    evaluated = {g.criterion for g in gold_cases}

    all_pred = _predicted_items_from_report(report, units_by_id)
    in_scope = [p for p in all_pred if p.criterion in evaluated and _is_strict_evaluation_signal(p)]

    old_matches, _ = _match_gold_cases(gold_cases, in_scope)
    old_counted_pred = {m.found_finding_id for m in old_matches if m.counted}
    old_counted_gold = {mg.case_id for mg, m in zip(gold_cases, old_matches) if m.counted}
    print_table(
        "OLD matcher (line+text, hard criterion)",
        per_criterion(gold_cases, in_scope, old_counted_pred, old_counted_gold),
    )

    cleaned = dedupe_mirror([p for p in in_scope if not is_placeholder(p)])
    matched, _ = match_new(gold_cases, cleaned)
    print_table(
        "NEW matcher (anchors + soft criterion + dedupe + no placeholders)",
        per_criterion(gold_cases, cleaned, {c.pred_id for c in matched.values()}, set(matched.keys())),
    )

    defect_cases = [g for g in gold_cases if not is_opinion(g.gold_text)]
    matched_d, _ = match_new(defect_cases, cleaned)
    print_table(
        "NEW matcher, recall on defects only (opinions excluded)",
        per_criterion(defect_cases, cleaned, {c.pred_id for c in matched_d.values()}, set(matched_d.keys())),
    )

    n_op = sum(1 for g in gold_cases if is_opinion(g.gold_text))
    print("")
    print("Gold cases total: {}; marked as opinions/wishes: {}".format(len(gold_cases), n_op))
    print("Predicted in gold-scope: {}; after placeholder+mirror cleanup: {}".format(len(in_scope), len(cleaned)))
    types = defaultdict(int)
    for c in matched.values():
        types[c.match_type] += 1
    print("Counted match types (new): {}".format(dict(sorted(types.items()))))


if __name__ == "__main__":
    main()
