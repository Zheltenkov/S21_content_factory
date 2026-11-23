"""One bounded evaluator: code checks, evidence-bound model batches, deterministic aggregation."""
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Protocol

from ..content import checks
from ..content.checks import Check
from ..content.models import ContentDraft, now
from ..core.llm import ModelClient, ModelUnavailable
from . import registry, store
from .context import MAX_EVIDENCE_ROUNDS, ContextLimit, pack, retrieve
from .inputs import contract, missing_for
from .models import Evaluation, EvaluationBatch, Evidence, OutcomeMapping, Snapshot, Verdict
from .policy import SCORED_THROUGH
from .resources import path_issues
from .snapshot import hashed

VERSION = "1.3"
PROMPT = Path(__file__).with_name("prompt.md")
BATCH_SIZE = 8
MAX_CONTEXT_CHARS = 400_000
logger = logging.getLogger(__name__)


class Subject(Protocol):
    """Identity of an internal draft or an external immutable project; no invented handoff is needed."""
    @property
    def id(self) -> str: ...

    @property
    def owner_id(self) -> str: ...


def dependency(number: int, snapshot: Snapshot) -> str:
    # Only inventory checks have narrower dependencies. Semantic judgments conservatively depend on the whole project.
    if number in {31, 40}:
        return hashed(json.dumps({"files": [f.model_dump() for f in snapshot.files],
                                  "approved": snapshot.approved_files}, sort_keys=True))
    return snapshot.digest


def initial(draft: Subject, snapshot: Snapshot, model: str, use_model: bool) -> Evaluation:
    profile = registry.load()
    result = Evaluation(run_id=draft.id, owner_id=draft.owner_id, profile=profile, profile_digest=registry.digest(profile),
                        engine_version=VERSION, prompt_digest=hashed(PROMPT.read_text(encoding="utf-8")), snapshot=snapshot,
                        model=model, use_model=use_model,
                        verdicts=[Verdict(criterion=c.number, dependency=dependency(c.number, snapshot)) for c in profile.criteria])
    missing = {f.path for f in snapshot.files if not f.present and f.role == "issued"}
    mandatory_missing = {f.path for f in snapshot.files if f.path in missing and f.required is True}
    unknown_missing = {f.path for f in snapshot.files if f.path in missing and f.required is None}
    # Quote concrete inventory records, including absent files, without requiring a quote of nonexistent content.
    records = sorted(snapshot.files, key=lambda f: (f.path not in missing, f.path))
    inventory_evidence = [Evidence(unit=f"inventory:{f.path}", quote=f.model_dump_json()) for f in records[:8]]
    path_checks = path_issues(snapshot)
    inputs = contract(snapshot)
    for verdict in result.verdicts:
        n = verdict.criterion
        absent = missing_for(n, inputs)
        if absent:
            verdict.state, verdict.method = "no_data", "code"
            verdict.reason = "Для проверки не предоставлены: " + ", ".join(absent) + ". Это не отрицательная оценка проекта."
            continue
        reason = ""
        if n in {4, 19} and not snapshot.optional_tasks:
            reason = "В комплекте нет бонусных заданий."
        elif n == 13 and not snapshot.has_story and (inputs is None or inputs.story_known):
            reason = "В проекте не используется сюжет."
        elif n in {46, 47} and not snapshot.has_theory and (inputs is None or inputs.theory_known):
            reason = "В комплекте отсутствует отдельная теоретическая часть."
        elif n in {53, 54} and snapshot.format != "group":
            reason = "Проект индивидуальный."
        elif n == 56 and not any("readme_" in f.path.lower() for f in snapshot.files):
            reason = "Выдаётся одна языковая версия."
        if reason:
            verdict.state, verdict.reason, verdict.method = "not_applicable", reason, "code"
        elif n == 8 and not snapshot.curriculum_available:
            verdict.state, verdict.reason, verdict.method = "no_data", "Учебный план не предоставлен или исключён методологом.", "code"
        elif n in {21, 33, 42, 49}:
            verdict.state, verdict.reason, verdict.method = "unverified", "Требуется контрольное выполнение в заявленной среде. Этот этап файлы не запускает.", "execution"
        elif n == 31:
            if not inventory_evidence:
                verdict.state, verdict.reason, verdict.method = "unverified", "Не удалось построить опись выдаваемых файлов.", "code"
                continue
            verdict.state, verdict.method = "assessed", "code"
            verdict.mark = -1 if mandatory_missing else 0 if missing else 1 if snapshot.approved_files is not None else 0
            verdict.reason = ("Отсутствуют обязательные выдаваемые файлы: " + ", ".join(sorted(mandatory_missing)) if mandatory_missing else
                              "Не подтверждена обязательность отсутствующих файлов: " + ", ".join(sorted(unknown_missing)) if unknown_missing else
                              "Отсутствуют материалы только бонусных заданий: " + ", ".join(sorted(missing)) if missing else
                              "Все файлы утверждённого перечня выданы." if snapshot.approved_files is not None else
                              "Распознанные зависимости присутствуют. Полнота относительно отдельного утверждённого перечня не подтверждена.")
            if unknown_missing and not mandatory_missing:
                verdict.state, verdict.mark = "unverified", None
            verdict.evidence = inventory_evidence
            verdict.suggestion = "Добавьте недостающие материалы или уточните перечень выдаваемых файлов." if missing else ""
            verdict.confirmed_blocker = bool(mandatory_missing)
        elif n == 32:
            verdict.state, verdict.method = "assessed", "code"
            verdict.mark = -1 if any(required for _, _, required in path_checks) else 0
            verdict.reason = "; ".join(message for message, _, _ in path_checks) if path_checks else "Распознанные кодом пути проверены. Полный контроль ссылок и команд в заявленной среде ещё требуется."
            verdict.evidence = [e for _, e, _ in path_checks[:12]] or inventory_evidence
        elif n == 40:
            verdict.method = "code"
            if snapshot.approved_files is None:
                verdict.state, verdict.reason = "no_data", "Нет структурированного утверждённого перечня выдаваемых файлов."
            else:
                # Scaffold entries may be explicitly listed, but do not require approval by default.
                relevant = {f.path for f in snapshot.files if f.present and (f.role == "issued" or f.path in snapshot.approved_files)}
                difference = relevant.symmetric_difference(snapshot.approved_files)
                verdict.state, verdict.mark = "assessed", 0 if difference else 1
                verdict.reason = "Состав расходится с перечнем: " + ", ".join(sorted(difference)) if difference else "Имена и состав выдаваемых файлов соответствуют перечню. Содержательные версии проверяются отдельно."
                # A filename list alone does not prove that the approved content versions match.
                if not difference:
                    verdict.mark = 0
                verdict.evidence = inventory_evidence
    return result


def evidence_errors(references: list[Evidence], snapshot: Snapshot, visible: dict[str, list[str]] | None = None) -> list[str]:
    units = {u.id: u for u in snapshot.units}
    return [reference.unit for reference in references
            if reference.unit not in units or reference.quote not in units[reference.unit].text
            or visible is not None and not any(reference.quote in text for text in visible.get(reference.unit, []))]


def validated_outcomes(mappings: list[OutcomeMapping], snapshot: Snapshot, visible: dict[str, list[str]] | None = None) -> list[OutcomeMapping]:
    found = []
    for number, _ in enumerate(snapshot.outcomes, 1):
        matches = [m for m in mappings if m.outcome == number]
        if len(matches) != 1:
            found.append(OutcomeMapping(outcome=number, status="uncertain", reason="Модель не предъявила однозначную цепочку свидетельств для ОР."))
            continue
        item = matches[0].model_copy(deep=True)
        allowed = set(item.tasks) - set(snapshot.optional_tasks)
        valid = bool(allowed) and set(item.tasks) <= set(snapshot.task_numbers) and not set(item.tasks) & set(snapshot.optional_tasks)
        for refs, fields in ((item.action, {"steps"}), (item.result, {"result", "expected"}), (item.peer, {"peer_checks", "check"})):
            valid = valid and bool(refs) and not evidence_errors(refs, snapshot, visible)
            valid = valid and all(r.unit in {f"task:{n}:{f}" for n in allowed for f in fields} for r in refs)
        if item.status == "gap" and evidence_errors([*item.action, *item.result, *item.peer], snapshot, visible):
            item.status = "uncertain"
            item.reason += " Цитаты заявленного разрыва не подтвердились."
        if item.status == "confirmed" and not valid:
            item.status = "uncertain"
            item.reason += " Цепочка не подтверждена точными цитатами обязательных заданий."
        found.append(item)
    return found


def accept(report: Evaluation, batch: EvaluationBatch, expected: list[int], visible: dict[str, list[str]] | None = None) -> None:
    # Partial or duplicate model responses affect only their own criteria, never produce implicit passes.
    if any(item.criterion not in expected for item in batch.results):
        for number in expected:
            verdict = report.verdicts[number - 1]
            verdict.state, verdict.reason, verdict.method = "error", "Модель вернула критерии вне запрошенного пакета; оценки не приняты.", "model"
        return
    for n in expected:
        verdict = report.verdicts[n - 1]
        candidates = [v for v in batch.results if v.criterion == n]
        if len(candidates) != 1:
            verdict.state, verdict.reason, verdict.method = "error", "В ответе модели критерий пропущен или продублирован.", "model"
            continue
        item = candidates[0]
        scored = n <= SCORED_THROUGH
        verdict.state, verdict.mark = item.state, item.mark if scored and item.state == "assessed" else None
        verdict.reason, verdict.suggestion, verdict.method = item.reason, item.suggestion, "model"
        verdict.evidence = item.evidence
        bad = evidence_errors(item.evidence, report.snapshot, visible)
        if item.state == "assessed" and (scored and item.mark is None or not item.evidence or bad):
            verdict.state, verdict.mark, verdict.evidence_valid = "unverified", None, False
            verdict.reason += " Оценка не принята: нет проверяемого основания или цитата не совпадает с источником."
        criterion = report.profile.criteria[n - 1]
        if verdict.mark == 1 and criterion.requires_confirmation:
            verdict.mark = 0
            verdict.reason += f" Для +1 требуется: {criterion.requires_confirmation}."
        if verdict.mark == 1 and report.snapshot.limitations:
            verdict.state, verdict.mark = "unverified", None
            verdict.reason += " Полнота исходных данных или комплекта не подтверждена."
        inputs = contract(report.snapshot)
        if inputs is not None and item.delivery_claims:
            unknown = [path for path in item.delivery_claims if path not in inputs.files or inputs.files[path].delivery != "issued"]
            if unknown:
                verdict.state, verdict.mark = "unverified", None
                verdict.reason += " Вывод зависит от неподтверждённой выдачи файлов: " + ", ".join(unknown) + "."
        verdict.confirmed_blocker = bool(scored and criterion.blocker and verdict.mark == -1 and (n != 20 or item.checked_answer_error))
    if 3 in expected:
        report.outcomes = validated_outcomes(batch.outcomes, report.snapshot, visible)
        third = report.verdicts[2]
        inconsistent = (third.mark in {0, 1} and any(m.status == "gap" for m in report.outcomes)
                        or third.mark == 1 and any(m.status != "confirmed" for m in report.outcomes)
                        or third.mark == -1 and not any(m.status == "gap" for m in report.outcomes))
        if third.state == "assessed" and (not report.outcomes or inconsistent):
            third.state, third.mark = "unverified", None
            third.confirmed_blocker = False
            third.reason += " Оценка и цепочки ОР → действие → результат → P2P не согласованы. Требуется повторная проверка, автоматическая отрицательная оценка не выставляется."


def compatible(old: Evaluation, new: Evaluation) -> bool:
    return (old.profile_digest, old.engine_version, old.prompt_digest, old.model) == (new.profile_digest, new.engine_version, new.prompt_digest, new.model)


async def run(report: Evaluation, draft: ContentDraft | None, client: ModelClient | None, previous: Evaluation | None = None, force: bool = False,
              code_findings: list[Check] | None = None) -> None:
    try:
        if previous and compatible(previous, report) and not force:
            for verdict in report.verdicts:
                old = previous.verdicts[verdict.criterion - 1]
                if verdict.state == "pending" and old.dependency == verdict.dependency and old.state not in {"pending", "error"}:
                    report.verdicts[verdict.criterion - 1] = old.model_copy(update={"reused": True})
                    if verdict.criterion == 3:
                        report.outcomes = previous.outcomes
        pending = [v.criterion for v in report.verdicts if v.state == "pending"]
        # External repositories use the existing repository code layer, never a fabricated generator draft.
        if code_findings is not None:
            findings = code_findings
        elif draft is not None:
            findings = checks.run_checks(draft, final=True)
        else:
            raise ValueError("Для внешнего проекта необходимо явно передать результаты структурной проверки")
        if client is None:
            for n in pending:
                report.verdicts[n - 1].state = "unverified"
                report.verdicts[n - 1].reason = "Модельная проверка не выполнялась."
        else:
            # Finish scored checks before advisory review; malformed expert output must not fail a mixed batch.
            groups = ([n for n in pending if n <= SCORED_THROUGH], [n for n in pending if n > SCORED_THROUGH])
            batches = [group[start:start + BATCH_SIZE] for group in groups for start in range(0, len(group), BATCH_SIZE)]
            for numbers in batches:
                started = time.monotonic()
                try:
                    packed = pack(report.snapshot, numbers, findings, MAX_CONTEXT_CHARS)
                    criteria = [report.profile.criteria[n - 1].model_dump() for n in numbers]
                    for round_number in range(MAX_EVIDENCE_ROUNDS + 1):
                        payload = packed.text(criteria, MAX_CONTEXT_CHARS)
                        report.calls += 1
                        batch, metrics = await client.generate(EvaluationBatch, PROMPT.read_text(encoding="utf-8"), payload)
                        report.input_tokens += metrics.input_tokens or 0
                        report.output_tokens += metrics.output_tokens or 0
                        report.elapsed_ms += metrics.elapsed_ms
                        if not batch.requests:
                            accept(report, batch, numbers, packed.visible)
                            break
                        if round_number == MAX_EVIDENCE_ROUNDS:
                            # An unfinished evidence request is never a pass or a negative project grade.
                            for n in numbers:
                                verdict = report.verdicts[n - 1]
                                verdict.state, verdict.mark = "unverified", None
                                verdict.reason = "Не завершён запрос дополнительных источников в пределах лимита проверки."
                            report.notes.append(f"Пакет {numbers}: достигнут лимит дополнительных чтений.")
                            break
                        for request in batch.requests:
                            fragment = retrieve(request, report.snapshot)
                            packed.fragments.append(fragment)
                            text = fragment.get("text")
                            if isinstance(text, str) and text:
                                packed.visible.setdefault(request.unit, []).append(text)
                            report.notes.append("Дополнительное чтение: " + json.dumps(
                                {"request":request.model_dump(),"result":{k:v for k,v in fragment.items() if k != "text"},
                                 "characters":len(text) if isinstance(text,str) else 0},ensure_ascii=False))
                except ContextLimit as error:
                    for n in numbers:
                        report.verdicts[n - 1].state = "unverified"
                        report.verdicts[n - 1].reason = f"Модельный контекст не собран: {error}. Текст не обрезался."
                    report.notes.append(f"Пакет {numbers}: превышен лимит контекста.")
                except (ModelUnavailable, ValueError) as error:
                    # Failed structured calls can still consume tokens. Keep known usage in the report too.
                    report.failed_calls += 1
                    report.input_tokens += getattr(error, "input_tokens", 0) or 0
                    report.output_tokens += getattr(error, "output_tokens", 0) or 0
                    report.elapsed_ms += round((time.monotonic() - started) * 1000)
                    for n in numbers:
                        report.verdicts[n - 1].state = "error"
                        report.verdicts[n - 1].reason = f"Пакет проверки не завершён: {error}"
                    # Do not launch more paid requests into the same provider outage.
                    report.notes.append("Модельная проверка остановлена после сбоя. Готовые результаты сохранены; повторный запуск продолжит оставшиеся проверки.")
                    break
                store.save(report)
        report.state = "partial" if any(v.state in {"pending", "error"} for v in report.verdicts) else "complete"
        report.completed_at = now()
    except asyncio.CancelledError:
        report.state = "interrupted"
        report.notes.append("Проверка прервана. Готовые результаты сохранены.")
        raise
    except Exception:
        logger.exception("evaluation failed run=%s evaluation=%s", report.run_id, report.id)
        report.state = "partial"
        report.notes.append("Внутренняя ошибка проверки. Готовые результаты сохранены.")
    finally:
        store.save(report)
