"""Anchor pre-screen + LLM-as-judge aligner for corpus evaluation.

This is an optional matching strategy for corpus_evaluation: instead of the
strict line+text matcher it screens candidate pairs cheaply (shared anchors:
url / file / quote / missing-artifact, plus lexical similarity and line
proximity) and asks a judge "same defect?" only for the top-K candidates per
gold case. The judge is pluggable:

  * "offline"    - semantic stand-in (Russian light stemmer + rare-token
                   overlap). No network. Used for local/CI runs.
  * "openrouter" - real model via content_factory.audit.openrouter. Run where
                   OpenRouter is reachable (key from .env).

The module is import-light and is loaded lazily by corpus_evaluation to avoid a
circular import. All Russian marker words and prompt text live in
aligner_markers.json / aligner_prompts.json so this source stays ASCII.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Protocol

from content_factory.audit.corpus_evaluation import (
    CorpusEvaluationMatch,
    GoldCorpusCase,
    PredictedCorpusItem,
    _criterion_label,
    _format_prediction_range,
    _format_range,
    _line_relation,
    _normalize_match_text,
    _same_missing_artifact_signal,
)

_DATA_DIR = Path(__file__).parent
_MARKERS = json.loads((_DATA_DIR / "aligner_markers.json").read_text(encoding="utf-8"))
_PROMPTS = json.loads((_DATA_DIR / "aligner_prompts.json").read_text(encoding="utf-8"))

PLACEHOLDER_MARKERS = tuple(_MARKERS["placeholders"])
OPINION_MARKERS = tuple(_MARKERS["opinions"])
RU_STOP = set(_MARKERS["ru_stop"])
STOPWORDS = set("the a to of in is on are and not".split()) | RU_STOP

PRESCREEN_MIN = 0.10
DEFAULT_TOPK = 10
JUDGE_ACCEPT = 0.45
STRONG_ANCHOR = 0.82  # prescreen score at/above which a match is accepted without the judge
JUDGE_PROMPT_VERSION = "v2"
VALIDITY_PROMPT_VERSION = "v2"

# OpenAI-compatible providers. Polza is a drop-in OpenRouter alternative.
PROVIDER_URLS = {
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "polza": "https://polza.ai/api/v1/chat/completions",
}
MODEL_BACKENDS = ("openrouter", "polza")
DEFAULT_JUDGE_MODEL = "openai/gpt-5.4-mini"

URL_RE = re.compile(r"https?:[/\\]+[^\s)>\]\"'|]+", re.IGNORECASE)
FILE_RE = re.compile(
    r"[\w\-./]+\.(?:sql|docx?|md|ya?ml|png|jpe?g|pcapng|pcap|py|js|java|go|c|h|sh|txt|xlsx|csv)",
    re.IGNORECASE,
)
QUOTE_RE = re.compile(r"[`«\"']([^`«»\"']{3,80})[`»\"']")

_SUFFIXES = sorted(
    [
        "ами", "ями", "ому", "ему",
        "ого", "его", "ыми", "ими",
        "ая", "яя", "ое", "ее", "ые", "ие",
        "ой", "ей", "ом", "ем", "ах", "ях",
        "ов", "ев", "ам", "ям", "ть", "ся",
        "ия", "ию", "ий", "ла", "ло", "ли",
        "на", "ну", "ет", "ут", "ют", "ат",
        "ят",
        "а", "я", "о", "е", "ы", "и", "у", "ю", "й", "ь",
    ],
    key=len,
    reverse=True,
)

_MIRROR_FAMILY = {
    "readme.md": "readme",
    "readme_rus.md": "readme",
    "readme_eng.md": "readme",
    "check-list.yml": "checklist",
    "check-list_rus.yml": "checklist",
    "check-list_uzb.yml": "checklist",
}


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


def _content_tokens(text: str) -> set:
    return {t for t in _normalize_match_text(text).split() if len(t) > 2 and t not in STOPWORDS}


def content_similarity(gold_text: str, found_text: str) -> float:
    g = _content_tokens(gold_text)
    f = _content_tokens(found_text)
    if not g or not f:
        return 0.0
    token_score = len(g & f) / min(len(g), len(f))
    seq = SequenceMatcher(a=_normalize_match_text(gold_text), b=_normalize_match_text(found_text)).ratio()
    return max(token_score, seq)


def _phrase_hit(a: Anchors, b: Anchors) -> bool:
    if a.phrases & b.phrases:
        return True
    for p in a.phrases:
        for q in b.phrases:
            if p in q or q in p:
                return True
    return False


def _stem(token: str) -> str:
    for suf in _SUFFIXES:
        if len(token) - len(suf) >= 4 and token.endswith(suf):
            return token[: -len(suf)]
    return token


def _stem_tokens(text: str) -> set:
    return {_stem(t) for t in _content_tokens(text)}


def is_opinion(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in OPINION_MARKERS)


def is_placeholder_prediction(item: PredictedCorpusItem) -> bool:
    low = (item.found_text or "").lower()
    return any(m in low for m in PLACEHOLDER_MARKERS)


def mirror_family(file_path: str | None) -> str:
    base = Path((file_path or "").replace("\\", "/")).name.lower()
    return _MIRROR_FAMILY.get(base, base)


def dedupe_mirror(items: list[PredictedCorpusItem]) -> list[PredictedCorpusItem]:
    """Collapse RU/EN and README<->check-list mirror findings by text core."""

    seen: set = set()
    out: list[PredictedCorpusItem] = []
    for item in items:
        core = " ".join(sorted(_content_tokens(item.found_text)))[:120]
        key = (item.project_id, item.criterion, item.issue_type, mirror_family(item.file_path), core)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def confidence_gate(items: list[PredictedCorpusItem], floor: float) -> list[PredictedCorpusItem]:
    """Drop low-confidence predictions (keeps items without a confidence value)."""

    if floor <= 0.0:
        return items
    return [it for it in items if it.confidence is None or it.confidence >= floor]


_SEVERITY_RANK = {"critical": 3, "major": 2, "minor": 1, "info": 0}
NOISY_CRITERIA = ("readability",)
NOISY_SEVERITIES = ("info", "minor")


def cap_repetitive(
    items: list[PredictedCorpusItem],
    per_issue_type: int = 3,
    criteria: tuple = NOISY_CRITERIA,
    severities: tuple = NOISY_SEVERITIES,
) -> tuple[list[PredictedCorpusItem], int]:
    """Limit repetitive low-severity nitpicks of the same issue_type per unit.

    Keeps up to `per_issue_type` findings for each (unit, criterion, issue_type)
    bucket among noisy criteria/severities, preferring higher severity and
    confidence. Returns (kept, dropped_count). Findings outside the noisy set are
    always kept. `per_issue_type <= 0` disables capping.
    """

    order = sorted(
        items,
        key=lambda it: (_SEVERITY_RANK.get(it.severity or "", 0), it.confidence or 0.0),
        reverse=True,
    )
    seen_line: set = set()
    counts: dict = defaultdict(int)
    kept: list[PredictedCorpusItem] = []
    dropped = 0
    for it in order:
        noisy = it.criterion in criteria and (it.severity in severities)
        if noisy and it.line_start is not None:
            # distinct lines are distinct real defects: keep them all, drop only
            # exact same-line same-issue duplicates.
            key = (it.project_id, it.criterion, it.issue_type or "", it.line_start)
            if key in seen_line:
                dropped += 1
                continue
            seen_line.add(key)
        elif noisy and per_issue_type > 0:
            # line-less repetition: keep at most per_issue_type per issue type.
            key = (it.project_id, it.criterion, it.issue_type or "")
            if counts[key] >= per_issue_type:
                dropped += 1
                continue
            counts[key] += 1
        kept.append(it)
    return kept, dropped


# --------------- judge backends ---------------

class Judge(Protocol):
    name: str
    calls: int

    def same_defect(self, gold: GoldCorpusCase, pred: PredictedCorpusItem) -> tuple[bool, float, str]:
        ...


class OfflineJudge:
    name = "offline"

    def __init__(self) -> None:
        self.calls = 0

    def same_defect(self, gold: GoldCorpusCase, pred: PredictedCorpusItem) -> tuple[bool, float, str]:
        self.calls += 1
        ga = extract_anchors(gold.gold_text)
        pa = extract_anchors(pred.found_text + " " + (pred.file_path or ""))
        if ga.urls & pa.urls or ga.files & pa.files or _phrase_hit(ga, pa):
            return True, 0.9, "shared anchor"
        if _same_missing_artifact_signal(gold.gold_text, pred.found_text):
            return True, 0.85, "missing-artifact signal"
        gs, fs = _stem_tokens(gold.gold_text), _stem_tokens(pred.found_text)
        if not gs or not fs:
            return False, 0.0, "empty"
        rare_shared = {t for t in (gs & fs) if len(t) >= 5}
        if len(rare_shared) >= 2:
            return True, 0.72, "shared rare stems"
        if _line_relation(gold.line_start, gold.line_end, pred.line_start, pred.line_end) == "overlap" and (gs & fs):
            return True, 0.7, "line overlap + shared stem"
        jac = len(gs & fs) / len(gs | fs)
        if jac >= 0.34:
            return True, 0.6, "stem jaccard %.2f" % jac
        return False, round(jac, 2), "low overlap"


class OpenRouterJudge:
    name = "openrouter"

    def __init__(self, api_key: str, model: str, cache_path: str | None = None, base_url: str | None = None) -> None:
        from content_factory.audit.openrouter import OpenRouterClient

        kwargs = {"base_url": base_url} if base_url else {}
        self.client = OpenRouterClient(api_key=api_key, model=model, **kwargs)
        self.calls = 0
        self.cache_path = Path(cache_path) if cache_path else None
        self.cache: dict = {}
        if self.cache_path and self.cache_path.exists():
            self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))

    def _key(self, gold: GoldCorpusCase, pred: PredictedCorpusItem) -> str:
        raw = (JUDGE_PROMPT_VERSION + "||" + gold.gold_text + "||" + pred.found_text).encode("utf-8")
        return hashlib.sha1(raw).hexdigest()

    def _save(self) -> None:
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1), encoding="utf-8")

    def same_defect(self, gold: GoldCorpusCase, pred: PredictedCorpusItem) -> tuple[bool, float, str]:
        key = self._key(gold, pred)
        if key in self.cache:
            v = self.cache[key]
            return bool(v["same_defect"]), float(v.get("confidence", 0.6)), v.get("reason", "cache")
        loc = (pred.file_path or "") + (":%s" % pred.line_start if pred.line_start else "")
        user = _PROMPTS["user_template"].format(
            gold_criterion=gold.criterion,
            gold_text=gold.gold_text[:1200],
            pred_criterion=pred.criterion,
            pred_loc=loc,
            pred_text=pred.found_text[:1200],
        )
        self.calls += 1
        try:
            data = self.client.complete_json(_PROMPTS["system"], user, max_tokens=600)
        except Exception as exc:  # noqa: BLE001 - одиночный сбой судьи не должен валить весь замер.
            reason = f"judge_error: {str(exc)[:180]}"
            self.cache[key] = {"same_defect": False, "confidence": 0.0, "reason": reason}
            self._save()
            return False, 0.0, reason
        same = bool(data.get("same_defect"))
        conf = float(data.get("confidence", 0.6) or 0.6)
        reason = str(data.get("reason", ""))[:200]
        self.cache[key] = {"same_defect": same, "confidence": conf, "reason": reason}
        self._save()
        return same, conf, reason


def build_judge(backend: str, *, api_key: str | None = None, model: str | None = None, cache_path: str | None = None) -> Judge:
    if backend == "offline":
        return OfflineJudge()
    if backend in MODEL_BACKENDS:
        if not api_key:
            raise ValueError("%s judge requires an API key." % backend)
        return OpenRouterJudge(api_key, model or DEFAULT_JUDGE_MODEL, cache_path, base_url=PROVIDER_URLS[backend])
    raise ValueError("Unknown judge backend: %s" % backend)


# --------------- matching ---------------

def _prescreen(gold: GoldCorpusCase, pred: PredictedCorpusItem) -> float:
    ga = extract_anchors(gold.gold_text)
    pa = extract_anchors(pred.found_text + " " + (pred.file_path or ""))
    if ga.urls & pa.urls or ga.files & pa.files or _phrase_hit(ga, pa):
        return 1.0
    if _same_missing_artifact_signal(gold.gold_text, pred.found_text):
        return 0.95
    line_rel = _line_relation(gold.line_start, gold.line_end, pred.line_start, pred.line_end)
    # File anchor (from the gold "Файл" column) + matching line == same defect.
    if getattr(gold, "file_hint", None) and pred.file_path:
        gf = mirror_family(gold.file_hint)
        pf = mirror_family(pred.file_path)
        same_file = gf == pf or Path(gold.file_hint).name.lower() == Path(pred.file_path).name.lower()
        if same_file and line_rel in ("overlap", "near"):
            return 0.9
        if same_file and gold.line_start is None:
            return 0.84  # gold names the file but gives no line; file match alone is strong
    score = content_similarity(gold.gold_text, pred.found_text)
    if line_rel in ("overlap", "near"):
        score += 0.1
    return score


def _row(gold: GoldCorpusCase, pred: PredictedCorpusItem | None, conf: float, reason: str, counted: bool) -> CorpusEvaluationMatch:
    if pred is None:
        return CorpusEvaluationMatch(
            project=gold.matched_project,
            project_id=gold.project_id,
            criterion=gold.criterion,
            label=_criterion_label(gold.criterion),
            gold_row_number=gold.row_number,
            gold_line_range=_format_range(gold.line_start, gold.line_end),
            gold_text=gold.gold_text,
            found_line_range="",
            found_text="",
            match_type="missed",
            match_score=0.0,
            counted=False,
            reason="Подходящей находки не нашлось: судья не подтвердил совпадение ни по одному кандидату.",
        )
    return CorpusEvaluationMatch(
        project=gold.matched_project,
        project_id=gold.project_id,
        criterion=gold.criterion,
        label=_criterion_label(gold.criterion),
        gold_row_number=gold.row_number,
        gold_line_range=_format_range(gold.line_start, gold.line_end),
        gold_text=gold.gold_text,
        found_finding_id=pred.finding_id,
        found_checker=pred.checker_name,
        found_line_range=_format_prediction_range(pred),
        found_text=pred.found_text,
        match_type="judge_same_criterion" if gold.criterion == pred.criterion else "judge_cross_criterion",
        match_score=round(conf, 4),
        counted=counted,
        reason="Судья подтвердил один и тот же дефект (%s)." % reason,
    )


def match_anchor_judge(
    gold_cases: list[GoldCorpusCase],
    predicted_items: list[PredictedCorpusItem],
    judge: Judge,
    *,
    topk: int = DEFAULT_TOPK,
    accept: float = JUDGE_ACCEPT,
) -> tuple[list[CorpusEvaluationMatch], set]:
    """Returns (match rows aligned to gold_cases order, set of matched prediction ids)."""

    by_project: dict = defaultdict(list)
    for p in predicted_items:
        by_project[p.project_id].append(p)
    predicted_by_id = {p.finding_id: p for p in predicted_items}

    accepted: list[tuple] = []  # (conf, gold_id, pred_id, same_criterion, reason)
    for gold in gold_cases:
        scored = [(_prescreen(gold, p), p) for p in by_project.get(gold.project_id, [])]
        scored = [(s, p) for s, p in scored if s >= PRESCREEN_MIN]
        # Rerank: same-criterion candidates first, then by prescreen score, so the
        # right-criterion finding is not crowded out of top-K by lexically similar nitpicks.
        scored.sort(key=lambda sp: (sp[1].criterion == gold.criterion, sp[0]), reverse=True)
        for score, pred in scored[:topk]:
            if score >= STRONG_ANCHOR:
                # hard anchor (url/file/quote/artifact) == same defect; do not let the judge veto it
                accepted.append((max(score, 0.9), gold.case_id, pred.finding_id, gold.criterion == pred.criterion, "shared anchor"))
                continue
            same, conf, reason = judge.same_defect(gold, pred)
            if same and conf >= accept:
                accepted.append((conf, gold.case_id, pred.finding_id, gold.criterion == pred.criterion, reason))

    assigned_g: set = set()
    assigned_p: set = set()
    chosen: dict = {}

    def assign(rows: list[tuple]) -> None:
        for conf, gid, pid, _same, reason in sorted(rows, key=lambda x: x[0], reverse=True):
            if gid in assigned_g or pid in assigned_p:
                continue
            assigned_g.add(gid)
            assigned_p.add(pid)
            chosen[gid] = (pid, conf, reason)

    assign([r for r in accepted if r[3]])       # same-criterion first
    assign([r for r in accepted if not r[3]])   # cross-criterion leftovers

    rows: list[CorpusEvaluationMatch] = []
    for gold in gold_cases:
        if gold.case_id in chosen:
            pid, conf, reason = chosen[gold.case_id]
            rows.append(_row(gold, predicted_by_id.get(pid), conf, reason, True))
        else:
            rows.append(_row(gold, None, 0.0, "", False))
    return rows, assigned_p


# --------------- validity judge (are the extra findings real defects?) ---------------

# Issue types from high-precision deterministic / structural checkers: when the
# offline judge sees these it treats the finding as a confident real defect.
HIGH_PRECISION_ISSUE_TYPES = {
    "broken_url_syntax", "missing_label_colon", "duplicate_anchor", "duplicate_heading",
    "sort_direction_conflict", "invalid_definition", "tautology", "repeated_numbered_list_items",
    "numbered_list_reset", "expected_file_name_mismatch", "artifact_missing_expected_text",
    "missing_local_resource", "spec_code_output_mismatch", "spec_code_contradiction",
    "course_language_material_mismatch", "language_material_conflict", "inappropriate_tool",
    "language_tooling_conflict", "ungrounded_sql_condition", "ungrounded_self_join_order",
    "suspicious_duplicate_name_result", "missing_label_colon", "case", "typo",
}


class OfflineValidityJudge:
    """Conservative structural proxy: trusts high-precision deterministic issue types."""

    name = "offline"

    def __init__(self) -> None:
        self.calls = 0

    def is_valid(self, item: PredictedCorpusItem) -> tuple[bool, float, str]:
        self.calls += 1
        it = (item.issue_type or "").strip()
        if it in HIGH_PRECISION_ISSUE_TYPES:
            return True, 0.8, "high-precision rule type: %s" % it
        return False, 0.4, "uncertain (needs model judge)"


class OpenRouterValidityJudge:
    """Asks the model whether a single finding is a real, worth-fixing defect."""

    name = "openrouter"

    def __init__(self, api_key: str, model: str, cache_path: str | None = None, base_url: str | None = None) -> None:
        from content_factory.audit.openrouter import OpenRouterClient

        kwargs = {"base_url": base_url} if base_url else {}
        self.client = OpenRouterClient(api_key=api_key, model=model, **kwargs)
        self.calls = 0
        self.cache_path = Path(cache_path) if cache_path else None
        self.cache: dict = {}
        if self.cache_path and self.cache_path.exists():
            self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1), encoding="utf-8")

    def is_valid(self, item: PredictedCorpusItem) -> tuple[bool, float, str]:
        key = hashlib.sha1((VALIDITY_PROMPT_VERSION + "|" + item.finding_id + "|" + item.found_text).encode("utf-8")).hexdigest()
        if key in self.cache:
            v = self.cache[key]
            return bool(v["valid"]), float(v.get("confidence", 0.6)), v.get("reason", "cache")
        user = _PROMPTS["validity_user_template"].format(
            criterion=item.criterion, checker=item.checker_name,
            severity=item.severity or "", text=item.found_text[:1000],
        )
        self.calls += 1
        data = self.client.complete_json(_PROMPTS["validity_system"], user, max_tokens=400)
        valid = bool(data.get("valid"))
        conf = float(data.get("confidence", 0.6) or 0.6)
        reason = str(data.get("reason", ""))[:200]
        self.cache[key] = {"valid": valid, "confidence": conf, "reason": reason}
        self._save()
        return valid, conf, reason


def build_validity_judge(backend: str, *, api_key: str | None = None, model: str | None = None, cache_path: str | None = None):
    if backend == "offline":
        return OfflineValidityJudge()
    if backend in MODEL_BACKENDS:
        if not api_key:
            raise ValueError("%s validity judge requires an API key." % backend)
        return OpenRouterValidityJudge(api_key, model or DEFAULT_JUDGE_MODEL, cache_path, base_url=PROVIDER_URLS[backend])
    raise ValueError("Unknown validity backend: %s" % backend)


def preflight(backend: str, api_key: str | None, model: str | None = None) -> tuple[bool, str]:
    """One cheap test call to confirm the LLM endpoint answers. Returns (ok, message)."""

    if backend == "offline":
        return True, "offline backend, no network needed"
    if backend not in MODEL_BACKENDS:
        return False, "unknown backend: %s" % backend
    if not api_key:
        return False, "no API key for %s" % backend
    from content_factory.audit.openrouter import OpenRouterClient

    client = OpenRouterClient(api_key=api_key, model=model or DEFAULT_JUDGE_MODEL, base_url=PROVIDER_URLS[backend], timeout_seconds=30.0)
    try:
        client.complete_json(
            "Верни строго JSON.",
            'Ответь ровно: {"ok": true}',
            max_tokens=20,
        )
        return True, "%s/%s reachable" % (backend, model or DEFAULT_JUDGE_MODEL)
    except Exception as exc:  # noqa: BLE001
        return False, "%s unreachable: %s" % (backend, str(exc)[:200])


def assess_validity(items: list[PredictedCorpusItem], judge, accept: float = 0.55) -> dict:
    """Runs the validity judge over extra findings; returns aggregate stats."""

    valid = 0
    valid_actionable = 0
    by_checker: dict = defaultdict(lambda: [0, 0])  # checker -> [valid, total]
    samples: list = []
    for item in items:
        ok, conf, reason = judge.is_valid(item)
        by_checker[item.checker_name][1] += 1
        if ok and conf >= accept:
            valid += 1
            by_checker[item.checker_name][0] += 1
            if (item.severity or "") in ("major", "critical"):
                valid_actionable += 1
            if len(samples) < 15:
                samples.append((item.checker_name, item.severity, item.found_text[:140], round(conf, 2), reason))
    return {
        "total": len(items),
        "valid": valid,
        "valid_actionable": valid_actionable,
        "by_checker": {k: v for k, v in sorted(by_checker.items(), key=lambda kv: kv[1][0], reverse=True)},
        "samples": samples,
        "backend": getattr(judge, "name", "?"),
        "calls": getattr(judge, "calls", 0),
    }
