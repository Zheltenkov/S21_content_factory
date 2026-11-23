"""Reconcile a curriculum revision with the brief's inventory and the actual final rows."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

_ACTIONS = {"retain", "revise", "merge", "specialize", "omit", "hold"}
_FORM = {"team": "групповой", "individual": "индивидуальный"}

# These fields travel with the SQL row identity, even when a methodologist renumbers rows.
REVISION_ROW_FIELDS = (
    "revision_project_id", "source_unit_ids", "source_refs", "participant_action", "route", "hours_basis",
    "research_evidence", "decision_ref", "requirement_refs", "prerequisite_project_ids", "choose_one_group", "peer_reviews",
)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _revision(plan: dict[str, Any]) -> dict[str, Any]:
    planner = _object(_object(plan.get("report")).get("planner_meta"))
    return _object(planner.get("revision"))


def is_source_backed(plan: dict[str, Any]) -> bool:
    # A remaining decision or row binding is evidence even if an inventory was lost.
    return (
        ("source_curriculum" in plan and plan["source_curriculum"] != [])
        or plan.get("source_policy") == "revision_with_accepted_skills"
        or bool(_revision(plan))
        or any(isinstance(row, dict) and "revision_project_id" in row for row in plan.get("rows") or [])
    )


def _index(
    values: object, key: str, label: str, errors: list[str],
) -> dict[str, dict[str, Any]]:
    if not isinstance(values, list):
        errors.append(f"Отсутствует список «{label}».")
        return {}
    result: dict[str, dict[str, Any]] = {}
    for number, entry in enumerate(values, 1):
        identifier = _text(_object(entry).get(key))
        if not identifier:
            errors.append(f"{label}, запись {number}: отсутствует {key}.")
        elif identifier in result:
            errors.append(f"{label}: идентификатор {identifier} повторяется.")
        else:
            result[identifier] = entry
    return result


def _ids(value: object, label: str, errors: list[str]) -> set[str]:
    if not isinstance(value, list) or any(not _text(item) for item in value):
        errors.append(f"{label}: требуется список непустых ID.")
        return set()
    result = set(value)
    if len(result) != len(value):
        errors.append(f"{label}: один ID указан несколько раз.")
    return result


def _check_revision_sequence(
    rows: dict[str, dict[str, Any]], coverage: dict[str, set[str]], decisions: dict[str, dict[str, Any]],
    revision: dict[str, Any], positions: dict[str, tuple[int, int]],
) -> list[str]:
    """Check the exported order against recorded decisions even if a row's saved links were edited."""
    errors: list[str] = []
    ordinal = {project: n for n, project in enumerate(rows)}
    titles = {pid: _text(row.get("project_name")) or pid for pid, row in rows.items()}

    def earlier(left: str, right: str) -> bool:
        return (ordinal[left] < ordinal[right] and left in positions and right in positions
                and positions[left] < positions[right])

    for source_id, decision in decisions.items():
        required_sources = _ids(decision.get("prerequisite_source_ids"), source_id, errors)
        for target in coverage.get(source_id, set()):
            for source in sorted(required_sources):
                origins = coverage.get(source, set())
                if not origins:
                    errors.append(f"{titles[target]}: отсутствует предшествующая работа {source}.")
                for origin in origins:
                    link = f"«{titles[origin]}» → «{titles[target]}»"
                    if origin not in (rows[target].get("prerequisite_project_ids") or []):
                        errors.append(f"{link}: потеряна обязательная связь из решения по исходным работам.")
                    elif not earlier(origin, target):
                        errors.append(f"{link}: сдаваемый результат нужен раньше, чем он получен.")
    for src, dst in revision.get("required_skill_edges", []):
        for target, row in rows.items():
            if dst not in (row.get("node_ids") or []):
                continue
            providers = [pid for pid, candidate in rows.items() if src in (candidate.get("primary_node_ids") or [])
                         and candidate.get("route") != "specialization" and earlier(pid, target)]
            if not any(pid in (row.get("prerequisite_project_ids") or []) for pid in providers):
                errors.append(f"«{titles[target]}»: для навыка {dst} не подтверждена предшествующая работа по {src}.")
    terminal = revision.get("terminal_project_id")
    for start in revision.get("entry_project_ids", []):
        if start not in rows or any(not earlier(start, other) for other in rows if other != start):
            errors.append("Работа, назначенная началом программы, должна стоять первой.")
    if terminal and (terminal not in rows or any(not earlier(other, terminal) for other in rows if other != terminal)):
        errors.append("Итоговая защита должна завершать учебный план.")
    return list(dict.fromkeys(errors))


def source_fidelity_errors(plan: dict[str, Any]) -> list[str]:
    """Verify both directions of the source → decision → row relation."""
    if not is_source_backed(plan):
        return []
    errors: list[str] = []
    revision = _revision(plan)
    errors.extend(str(issue) for issue in revision.get("decision_errors") or [])
    expected = _index(plan.get("source_curriculum"), "source_id", "исходник брифа", errors)
    inventory = _index(revision.get("source_curriculum"), "source_id", "реестр редакции", errors)
    decisions = _index(revision.get("dispositions"), "source_id", "решения", errors)
    rows = _index(plan.get("rows"), "revision_project_id", "строки УП", errors)
    if not expected:
        errors.append("Нет исходных единиц для сверки.")
    if not rows:
        errors.append("Нет конечных проектов для выдачи.")
    positions: dict[str, tuple[int, int]] = {}
    for project, row in rows.items():
        if row.get("route") == "needs_design":
            errors.append(f"{project}: для принятого навыка требуется определить место, действие и сдаваемый результат.")
        try:
            positions[project] = (int(row.get("block_index") or 0), int(row.get("row_number") or 0))
        except (ValueError, TypeError, OverflowError):
            errors.append(f"{project}: некорректный порядковый номер блока или строки.")

    # Historical coverage alone cannot substantiate a revision against new program requirements.
    generation = _object(plan.get("revision_generation"))
    reviewed = bool(expected) and all(
        decisions.get(source, {}).get("decision_approved") is True
        and _text(decisions[source].get("decision_ref"))
        for source in expected
    )
    pending = generation.get("unassigned_requirements") or []
    if pending:
        errors.append(f"Требования без целевого проекта: {', '.join(str(item) for item in pending)}.")
    if generation.get("state") != "complete" and (not reviewed or pending):
        issues = generation.get("issues") or []
        details = " ".join(str(item) for item in issues)
        errors.append(f"Обновления по исследованию требуют проверки. {details}".strip())

    # Derive actual coverage from the output, independently of the planner's decisions.
    requirements = _index(generation.get("requirements", []), "ref", "требования брифа", errors)
    coverage: dict[str, set[str]] = defaultdict(set)
    handled_requirements: set[str] = set()
    for project, row in rows.items():
        bound_sources = _ids(row.get("source_unit_ids"), project, errors)
        for source_id in bound_sources:
            coverage[source_id].add(project)
        if bound_sources and all(key in expected for key in bound_sources):
            required_refs = [_text(expected[key].get("source_ref")) for key in row["source_unit_ids"]]
            if row.get("source_refs") != required_refs:
                errors.append(f"{project}: ссылки строки расходятся с исходниками {', '.join(sorted(bound_sources))}.")
        for preceding in _ids(row.get("prerequisite_project_ids", []), project, errors):
            if preceding not in positions or project not in positions or positions[preceding] >= positions[project]:
                errors.append(f"{project}: обязательный предшествующий проект {preceding} отсутствует или стоит позже.")
        row_refs = _ids(row.get("requirement_refs", []), project, errors)
        # A valid paragraph ID copied onto an unrelated row cannot establish even a technical link.
        if generation.get("version") == "curriculum-revision/2":
            decision_refs = set()
            for source_id in bound_sources:
                linked_decision = decisions.get(source_id, {})
                if not linked_decision.get("decision_ref") or project not in (linked_decision.get("target_project_ids") or []):
                    continue
                for ref in _ids(linked_decision.get("requirement_refs", []), source_id, errors):
                    cited = any(
                        isinstance(basis, dict) and basis.get("ref") == ref
                        and len(_text(basis.get("quote"))) >= 12
                        and _text(basis["quote"]) in _text(requirements.get(ref, {}).get("text"))
                        for basis in linked_decision.get("evidence") or []
                    )
                    if cited:
                        decision_refs.add(ref)
                    else:
                        errors.append(f"{source_id} → {project}: для {ref} отсутствует точная цитата требования.")
            if row_refs != decision_refs:
                errors.append(f"{project}: требования строки расходятся с решением по этой работе.")
            row_refs &= decision_refs
        handled_requirements.update(row_refs)
        for key, label in (
            ("project_name", "название"), ("participant_action", "действие участника"),
            ("artifact", "артефакт"), ("validation_criteria", "критерий приёмки"),
        ):
            if not _text(row.get(key)) and row.get("route") != "needs_design":
                errors.append(f"{project}: не заполнено поле «{label}».")
    if revision.get("version") == 2:
        errors.extend(_check_revision_sequence(rows, coverage, decisions, revision, positions))
    for ref in sorted(requirements.keys() - handled_requirements):
        errors.append(f"{ref}: требование не назначено ни одной конечной строке.")
    for ref in sorted(handled_requirements - requirements.keys()):
        errors.append(f"{ref}: конечная строка ссылается на неизвестное требование.")
    for identifier in sorted((inventory.keys() | decisions.keys() | coverage.keys()) - expected.keys()):
        errors.append(f"{identifier}: ссылка на единицу, отсутствующую в исходнике брифа.")

    for identifier, source in expected.items():
        original = inventory.get(identifier)
        if original is None:
            errors.append(f"{identifier}: исходная единица исчезла из реестра редакции.")
        elif any(original.get(key) != value for key, value in source.items()):
            errors.append(f"{identifier}: реестр редакции расходится с исходником брифа.")
        decision = decisions.get(identifier)
        if decision is None:
            errors.append(f"{identifier}: отсутствует решение по исходной единице.")
            continue
        action = _text(decision.get("disposition"))
        if generation.get("version") == "curriculum-revision/2" and (
            decision.get("decision_origin") == "historical_copy" or not _text(decision.get("decision_ref"))
        ):
            errors.append(f"{identifier}: по перенесённой работе отсутствует решение с основанием.")
        if action not in _ACTIONS:
            errors.append(f"{identifier}: неизвестное решение «{action}».")
        if not _text(decision.get("rationale")):
            errors.append(f"{identifier}: отсутствует обоснование решения.")
        if _text(decision.get("source_ref")) != _text(source.get("source_ref")):
            errors.append(f"{identifier}: решение ссылается на другой источник.")
        approved = decision.get("decision_approved") is True and bool(_text(decision.get("decision_ref")))
        if action == "hold":
            question = _text(decision.get("review_question")) or "требуется решение методолога"
            errors.append(f"{identifier}: {question}.")
        if (action == "omit" or decision.get("lost_outcomes")) and not approved:
            errors.append(f"{identifier}: потеря исходного содержания требует подтверждения и ссылки на решение.")

        targets = _ids(decision.get("target_project_ids"), identifier, errors)
        actual = coverage.get(identifier, set())
        if actual != targets:
            errors.append(
                f"{identifier}: ожидаемые проекты [{', '.join(sorted(targets))}], "
                f"фактические [{', '.join(sorted(actual))}]."
            )
        if action in {"retain", "revise", "merge", "specialize"} and not actual:
            errors.append(f"{identifier}: исходная единица не покрыта конечной строкой.")
        if action == "omit" and (actual or targets):
            errors.append(f"{identifier}: исключённая единица всё ещё назначена проекту.")

        # Compare with the source, allowing a change of form only with an explicit decision.
        mode = _text(source.get("team_mode"))
        if approved and _text(decision.get("target_team_mode")) in _FORM:
            mode = _text(decision.get("target_team_mode"))
        for project in sorted(actual):
            if mode in _FORM and rows[project].get("delivery_format") != _FORM[mode]:
                errors.append(f"{identifier} → {project}: ожидаемая форма — {_FORM[mode]}.")
    return errors


def _repeated_practice(plan: dict[str, Any]) -> list[list[dict[str, str]]]:
    """Surface exact reuse for human review. Intentional repetition can be pedagogically sound."""
    by_content: dict[tuple[str, ...], dict[str, str]] = defaultdict(dict)
    for row in plan.get("rows") or []:
        if not isinstance(row, dict) or not row.get("decision_ref") or not row.get("source_unit_ids"):
            continue
        project_id = _text(row.get("revision_project_id"))
        fields = ("participant_action", "artifact", "validation_criteria")
        content = tuple(" ".join(_text(row.get(key)).casefold().split()) for key in fields)
        if project_id and all(content):
            by_content[content][project_id] = _text(row.get("project_name")) or project_id
    return [[{"id": uid, "title": title} for uid, title in projects.items()]
            for projects in by_content.values() if len(projects) > 1]


def apply_source_fidelity_gate(plan: dict[str, Any]) -> None:
    """Refresh source failures without changing unrelated methodology gate decisions."""
    if not is_source_backed(plan):
        return
    report = plan.setdefault("report", {})
    if _object(plan.get("revision_generation")).get("version") == "curriculum-revision/2":
        report["repeated_revision_practice"] = _repeated_practice(plan)
    gate = report.setdefault("publication_gate", {})
    failures = [f for f in gate.get("failures", []) if f.get("code") != "source_fidelity"]
    for message in source_fidelity_errors(plan):
        failures.append({"code": "source_fidelity", "message": message, "value": 1, "threshold": 0})
    gate.update(passed=not failures, failures=failures)


def source_release_failures(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Return readable release blockers only for source-backed plans."""
    if not is_source_backed(plan):
        return []
    apply_source_fidelity_gate(plan)
    gate = plan["report"]["publication_gate"]
    failures = list(gate["failures"])
    if plan.get("status") in {"invalid", "deferred"}:
        failures.append({
            "code": "plan_status_not_ready", "message": str(plan.get("message") or "Проверьте статус УП."),
            "value": 1, "threshold": 0,
        })
    return failures
