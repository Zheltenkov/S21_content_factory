"""One-shot scoring + report.

Re-scores an existing audit report.json against the cleaned atomic gold using
the anchor+judge matcher, then writes an honest, presentation-ready report
(report.md + report_metrics.csv): recall on real human defects, precision, F1,
per-criterion table, and an HONEST "beyond-human coverage" block (extra
candidate findings the algorithm surfaces beyond the human list - explicitly
labelled as requiring review, with the actionable major/critical subset).

Usage:
    PYTHONPATH=src python3 make_report.py \
        --report .tmp/metrics_evaluation/audit_report/report.json \
        --gold metrics/Проекты_очищенные_атомарные.xlsx \
        --judge-backend offline            # or: openrouter --judge-model gpt-5.4-mini
"""

from __future__ import annotations

import argparse
import enum
import json
import sys
from pathlib import Path

# guarded shim so the script also runs on Python 3.10 (prod is 3.11+)
if not hasattr(enum, "StrEnum"):
    class StrEnum(str, enum.Enum):
        def __str__(self):
            return str(self.value)
    enum.StrEnum = StrEnum
if "tomllib" not in sys.modules:
    try:
        import tomllib  # noqa
    except ModuleNotFoundError:
        import tomli
        sys.modules["tomllib"] = tomli

# make `content_audit` importable without setting PYTHONPATH
_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from content_audit.domain import AuditReport
from content_audit.corpus_evaluation import evaluate_corpus_report
from content_audit.env import get_env_value, load_env_file
from content_audit import aligner


def _fmt(x):
    return ("%.3f" % x) if isinstance(x, float) else str(x)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--gold", default="metrics/Проекты_очищенные_атомарные.xlsx")
    ap.add_argument("--judge-backend", choices=["offline", "openrouter", "polza"], default="offline")
    ap.add_argument("--judge-model", default="openai/gpt-5.4-mini")
    ap.add_argument("--judge-topk", type=int, default=10)
    ap.add_argument("--cap-repetitive", type=int, default=3)
    ap.add_argument("--out", default="report_quality")
    ap.add_argument("--validate", action="store_true", help="Прогнать судью валидности по находкам сверх эталона.")
    ap.add_argument("--validity-backend", choices=["offline", "openrouter", "polza"], default="offline")
    ap.add_argument("--validate-limit", type=int, default=0, help="Проверить судьёй только N случайных кандидатов (0 = все); долю экстраполировать.")
    args = ap.parse_args()

    report = AuditReport.model_validate(json.loads(Path(args.report).read_text(encoding="utf-8")))
    env = load_env_file(Path(".env"))

    def _key(backend):
        if backend == "polza":
            return get_env_value(("POLZA_AI_API_KEY", "POLZA_API_KEY"), env)
        if backend == "openrouter":
            return get_env_value(("OPENROUTER_API_KEY", "OPEN_ROUTER_API_KEY"), env)
        return None

    # Preflight: make sure the LLM endpoint actually answers before the big run.
    for label, backend in (("matcher", args.judge_backend), ("validity", args.validity_backend if args.validate else "offline")):
        ok, msg = aligner.preflight(backend, _key(backend), args.judge_model)
        print("LLM preflight [%s]: %s" % (label, msg))
        if not ok:
            print("Остановка: LLM недоступна. Проверьте ключ/сеть/баланс и повторите.")
            sys.exit(2)

    api_key = _key(args.judge_backend)

    s = evaluate_corpus_report(
        report,
        Path(args.gold),
        matcher="anchor_judge",
        judge_backend=args.judge_backend,
        judge_model=args.judge_model,
        judge_api_key=api_key,
        judge_topk=args.judge_topk,
        judge_cache_path=".tmp/judge_cache.json",
        defects_only=True,
        mirror_dedupe=True,
        cap_repetitive=args.cap_repetitive,
    )

    findings_total = report.summary.findings_total
    am = s.actionable_metrics
    ratio = (s.predicted_total / s.gold_total) if s.gold_total else 0.0

    validity = None
    if args.validate:
        vkey = _key(args.validity_backend)
        vjudge = aligner.build_validity_judge(
            args.validity_backend, api_key=vkey, model=args.judge_model, cache_path=".tmp/validity_cache.json"
        )
        extras = list(s.detailed_false_positive_items)
        sampled = False
        if args.validate_limit and len(extras) > args.validate_limit:
            import random
            random.seed(42)
            extras = random.sample(extras, args.validate_limit)
            sampled = True
        validity = aligner.assess_validity(extras, vjudge)
        validity["sampled"] = sampled
        validity["population"] = len(s.detailed_false_positive_items)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    lines = []
    lines.append("# Отчёт о качестве аудита контента")
    lines.append("")
    lines.append("_Сверка с ручной разметкой методологов (только дефекты, мнения исключены)._")
    lines.append("")
    lines.append("## 1. Главное")
    lines.append("")
    lines.append("| Метрика | Значение |")
    lines.append("|---|---|")
    lines.append("| Дефектов в эталоне (defects) | %d |" % s.gold_total)
    lines.append("| Поймано (recall) | **%.1f%%** (%d из %d) |" % (s.recall * 100, s.true_positive, s.gold_total))
    lines.append("| Точность сопоставленных (precision) | %.1f%% |" % (s.precision * 100))
    lines.append("| F1 | %.3f |" % s.f1_score)
    lines.append("| Покрытие на уровне проект×критерий (recall) | %.1f%% |" % (s.overview_recall * 100))
    lines.append("")
    lines.append("## 2. По критериям (дефекты)")
    lines.append("")
    lines.append("| Критерий | Дефектов | Поймано | Recall |")
    lines.append("|---|---|---|---|")
    for m in s.per_criterion:
        if m.gold_total == 0:
            continue
        lines.append("| %s | %d | %d | %.1f%% |" % (m.label, m.gold_total, m.true_positive, m.recall * 100))
    lines.append("")
    lines.append("## 3. Покрытие сверх ручного списка")
    lines.append("")
    lines.append("> Это кандидаты, которых **нет** в ручной разметке. Часть из них — реальные находки, "
                 "которые человек пропустил, часть — шум. **Требуют ревью**, не засчитываются как подтверждённые.")
    lines.append("")
    lines.append("| Показатель | Значение |")
    lines.append("|---|---|")
    lines.append("| Всего находок алгоритма в проекте | %d |" % findings_total)
    lines.append("| Кандидатов в зоне критериев эталона | %d |" % s.predicted_total)
    lines.append("| Сопоставлено с ручными дефектами | %d |" % s.true_positive)
    lines.append("| Дополнительных кандидатов (сверх эталона) | %d |" % s.false_positive)
    if am is not None:
        lines.append("| из них actionable (major/critical) | %d |" % am.false_positive)
    lines.append("| Кандидатов на один ручной дефект | ×%.1f |" % ratio)
    lines.append("")
    if validity is not None:
        share = 100.0 * validity["valid"] / max(validity["total"], 1)
        lines.append("### 3b. Сколько «лишних» кандидатов реально валидны (судья: %s)" % validity["backend"])
        lines.append("")
        lines.append("| Показатель | Значение |")
        lines.append("|---|---|")
        lines.append("| Проверено кандидатов | %d из %d |" % (validity["total"], validity.get("population", validity["total"])))
        lines.append("| Признаны валидными дефектами | **%d (%.0f%%)** |" % (validity["valid"], share))
        lines.append("| из них actionable (major/critical) | %d |" % validity["valid_actionable"])
        if validity.get("sampled"):
            est = int(round(share / 100.0 * validity.get("population", validity["total"])))
            lines.append("| Оценка на все %d (экстраполяция) | ~%d валидных |" % (validity.get("population", 0), est))
        lines.append("")
        if validity["backend"] == "offline":
            lines.append("> Offline-оценка консервативна: засчитывает только находки высокоточных детерминированных модулей. "
                         "Реальную долю валидных точнее измерит судья gpt-5.4-mini (--validity-backend openrouter).")
            lines.append("")
    lines.append("## 4. Вклад проверяющих модулей (по подтверждённым)")
    lines.append("")
    lines.append("| Чекер | Подтверждено | Всего находок | Точность |")
    lines.append("|---|---|---|---|")
    for c in sorted(s.checker_metrics, key=lambda x: x.true_positive, reverse=True)[:12]:
        if c.true_positive == 0 and c.predicted_total == 0:
            continue
        lines.append("| %s | %d | %d | %.1f%% |" % (c.label, c.true_positive, c.predicted_total, c.precision * 100))
    lines.append("")
    lines.append("## 5. Параметры прогона")
    lines.append("")
    lines.append("- Матчер: anchor_judge, судья: %s%s" % (args.judge_backend, ("/" + args.judge_model) if args.judge_backend == "openrouter" else ""))
    lines.append("- defects_only=on, mirror_dedupe=on, cap_repetitive=%d, judge_topk=%d" % (args.cap_repetitive, args.judge_topk))
    lines.append("- Источник находок: %s" % args.report)
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")

    import csv as _csv
    with (out / "report_metrics.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["metric", "value"])
        for k, v in [
            ("gold_defects", s.gold_total), ("recall", s.recall), ("precision", s.precision), ("f1", s.f1_score),
            ("overview_recall", s.overview_recall), ("predicted_in_scope", s.predicted_total),
            ("true_positive", s.true_positive), ("extra_candidates", s.false_positive),
            ("findings_total", findings_total), ("candidates_per_defect", round(ratio, 2)),
            ("actionable_extra", am.false_positive if am else ""),
        ]:
            w.writerow([k, _fmt(v) if isinstance(v, float) else v])

    print("Recall(defects) %.1f%%  Precision %.1f%%  F1 %.3f  | gold=%d TP=%d extra=%d (x%.1f)"
          % (s.recall * 100, s.precision * 100, s.f1_score, s.gold_total, s.true_positive, s.false_positive, ratio))
    if validity is not None:
        print("Validity(%s): %d/%d extra candidates judged real defects (%d actionable)"
              % (validity["backend"], validity["valid"], validity["total"], validity["valid_actionable"]))
    print("Report written to:", out / "report.md")


if __name__ == "__main__":
    main()
