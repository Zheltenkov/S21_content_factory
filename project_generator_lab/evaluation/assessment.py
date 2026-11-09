"""Read-only interpretation of criterion records; unknown evidence is never a partial defect."""
from .models import Evaluation, Verdict
from .resources import path_issues


def normalized(report: Evaluation) -> list[Verdict]:
    """Project legacy reports without changing their persisted judgments or source snapshots."""
    values: list[Verdict] = []
    for number in range(1, 57):
        matches = [v for v in report.verdicts if v.criterion == number]
        if len(matches) != 1:
            values.append(Verdict(criterion=number, state="unverified", method="validation",
                                  reason="Запись критерия отсутствует или повторяется. Требуется восстановить проверку."))
            continue
        verdict = matches[0].model_copy(deep=True)
        if number > 40:
            verdict.mark, verdict.confirmed_blocker = None, False
            values.append(verdict)
            continue
        qualification = ""
        criterion = next((c for c in report.profile.criteria if c.number == number), None)
        if verdict.state == "assessed" and verdict.mark is None:
            qualification = "Оценка обязательного критерия отсутствует; результат не подтверждён."
        elif verdict.state == "assessed" and not verdict.evidence_valid:
            qualification = "Свидетельство не подтверждено; это неизвестный результат, а не оценка проекта."
        elif verdict.state == "assessed" and verdict.mark == 0:
            if verdict.method == "model" and criterion and criterion.requires_confirmation:
                qualification = "Для содержательного вывода требуется дополнительное подтверждение: " + criterion.requires_confirmation
            elif number == 31 and verdict.method == "code" and not any(not f.present and f.role == "issued" for f in report.snapshot.files):
                qualification = "Отсутствующих зависимостей не обнаружено, но полнота обязательного комплекта не подтверждена."
            elif number == 32 and verdict.method == "code" and not path_issues(report.snapshot):
                qualification = "Проверка распознанных путей не подтверждает корректность всех ссылок и команд."
        if qualification:
            verdict.state, verdict.mark, verdict.confirmed_blocker = "unverified", None, False
            verdict.reason += " " + qualification
        if verdict.state != "assessed":
            verdict.mark, verdict.confirmed_blocker = None, False
        values.append(verdict)
    return values
