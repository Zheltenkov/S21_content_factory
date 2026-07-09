"""HTML rendering and local UI helpers for the audit web interface."""

from __future__ import annotations

import hmac
import html
from collections import Counter
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, unquote

from content_factory.audit.domain import (
    CRITERION_LABELS,
    ISSUE_KIND_LABELS,
    SEVERITY_LABELS,
    VERDICT_LABELS,
    AuditReport,
    Criterion,
    Finding,
    Severity,
)
from content_factory.audit.report_formatting import (
    format_finding_explanation_html,
    format_finding_fragment,
)

AUTH_COOKIE_NAME = "audit_auth"
AVATAR_ROUTE = "/assets/avatar-placeholder.jpg"
ARCHIVE_FIELD_NAME = "project_archive"
AUDIT_WEB_ASSET_DIR = Path(__file__).with_name("templates")


@cache
def _audit_web_asset(name: str) -> str:
    """Load packaged CSS/JS used by the inline audit report UI."""

    return (AUDIT_WEB_ASSET_DIR / name).read_text(encoding="utf-8").strip()


class WebRenderState(Protocol):
    """State shape required by audit page rendering."""

    default_input: Path | None
    report_dir: Path
    last_error: str | None
    auth_username: str
    auth_password: str

    @property
    def auth_enabled(self) -> bool:
        """Return True when static UI credentials are configured."""

        ...


def render_page(report: AuditReport | None, state: WebRenderState, form_values: dict[str, str] | None = None) -> str:
    """Собирает полную страницу веб-интерфейса."""

    form = form_values or {}
    if form_values is not None and "input_path" in form:
        input_value = form.get("input_path", "")
    else:
        input_value = report.summary.input_path if report else str(state.default_input or "")
    body = "\n".join(
        [
            _render_topbar(),
            '<main class="shell">',
            _render_run_panel(
                report,
                input_value,
            ),
            _render_error(state.last_error),
            _render_dashboard(report, state.report_dir) if report else _render_empty_state(),
            "</main>",
            _render_script(),
        ]
    )
    return f"<!doctype html><html lang=\"ru\"><head>{_render_head()}</head><body>{body}</body></html>"


def render_login_page(error: str = "") -> str:
    """Собирает страницу статической авторизации."""

    error_html = f'<div class="login-error">{_esc(error)}</div>' if error else ""
    body = f"""
<main class="login-shell">
  <form class="login-box" method="post" action="/login">
    <div class="login-mark" aria-hidden="true">
      <img src="{AVATAR_ROUTE}" alt="">
    </div>
    <h1>Авторизация</h1>
    <label for="username">Логин</label>
    <input id="username" name="username" autocomplete="username" required>
    <label for="password">Пароль</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required>
    <button class="login-submit" type="submit">Войти</button>
    {error_html}
  </form>
</main>
"""
    return f"<!doctype html><html lang=\"ru\"><head>{_render_head('Авторизация')}</head><body class=\"login-page\">{body}</body></html>"


def credentials_match(username: str, password: str, state: WebRenderState) -> bool:
    """Сравнивает логин и пароль с `.env` без раннего выхода по символам."""

    if not state.auth_enabled:
        return True
    username_ok = hmac.compare_digest(username.encode("utf-8"), state.auth_username.encode("utf-8"))
    password_ok = hmac.compare_digest(password.encode("utf-8"), state.auth_password.encode("utf-8"))
    return username_ok and password_ok


def _auth_cookie(token: str) -> str:
    """Формирует защищённую cookie локальной сессии."""

    return f"{AUTH_COOKIE_NAME}={token}; Path=/; Max-Age=28800; HttpOnly; SameSite=Lax"


def _clear_auth_cookie() -> str:
    """Формирует cookie для сброса локальной сессии."""

    return f"{AUTH_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"


def _render_head(title: str = "Аудит контента · Панель отчёта") -> str:
    """Возвращает заголовок страницы и стили."""

    return f"""
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<link rel="stylesheet" href="/static/css/s21-tokens.css?v=20260704-shared-tokens">
<style>
{_audit_web_asset("report.css")}
</style>
"""


def _render_topbar() -> str:
    """Возвращает верхнюю панель."""

    return """
<nav class="eco-nav" aria-label="Разделы платформы">
  <a class="eco-nav-link" href="/app">Главная</a>
  <a class="eco-nav-link" href="/app/generate">Генерация</a>
  <a class="eco-nav-link active" href="/app/auditor">Аудитор</a>
  <a class="eco-nav-link" href="/app/translate">Перевод</a>
  <a class="eco-nav-link" href="/app/curriculum">УП</a>
  <a class="eco-nav-link" href="/app/spravochnik">Справочник</a>
  <a class="eco-nav-link" href="/app/instruction">Инструкция</a>
</nav>
<header class="topbar">
  <div class="topbar-inner">
    <div class="wordmark">
      <span class="glyph"><img src="/assets/avatar-placeholder.jpg" alt=""></span>
      <span>
        <div class="brand-title">Аудит контента</div>
        <div class="brand-sub">проверка учебных проектов</div>
      </span>
    </div>
    <nav class="top-actions">
      <a class="link-button" href="#summary">Сводка</a>
      <a class="link-button" href="/logout">Выйти</a>
    </nav>
  </div>
</header>
"""


def _render_run_panel(
    report: AuditReport | None,
    input_value: str,
) -> str:
    """Возвращает форму запуска, свёрнутую после построения отчёта."""

    open_attr = "" if report is not None else " open"
    restart_button = '<span class="link-button run-restart" role="button" tabindex="0">Перезапустить</span>' if report is not None else ""
    return f"""
<section class="run-panel">
  <details class="run-details"{open_attr}>
    <summary class="run-bar">
      <span class="run-bar-text">{_esc(_run_bar_text(report, input_value))}</span>
      <span class="run-bar-actions">
        {restart_button}
        <span class="run-bar-edit"></span>
      </span>
    </summary>
    <div class="panel-head">
      <div>
        <h1>Проверка локального проекта</h1>
      </div>
    </div>
    <form id="run-form" method="post" action="/run" enctype="multipart/form-data">
      <div class="form-grid">
        <div>
          <label for="input_path">Путь к проекту</label>
          <input id="input_path" name="input_path" type="text" value="{_esc(input_value)}" spellcheck="false">
        </div>
        <button class="button" type="submit">Запустить</button>
      </div>
      <label class="upload-zone" for="{ARCHIVE_FIELD_NAME}">
        <input id="{ARCHIVE_FIELD_NAME}" name="{ARCHIVE_FIELD_NAME}" type="file" accept=".zip,.rar,.tar,.gz,.tgz,.bz2,.xz">
        <span class="upload-title">Архив проекта</span>
        <span class="upload-name" id="archive-file-name">ZIP / RAR / TAR</span>
      </label>
      <div class="run-progress" id="run-progress" role="status" aria-live="polite" aria-busy="false" hidden>
        <div class="run-progress-head">
          <span>Готовность отчёта</span>
          <span class="run-progress-stage" id="run-progress-stage">Подготовка запуска</span>
        </div>
        <div class="run-progress-track" aria-hidden="true">
          <div class="run-progress-fill" id="run-progress-fill"></div>
        </div>
        <div class="run-progress-meta">
          <span id="run-progress-percent">0%</span>
          <span id="run-progress-elapsed">0 с</span>
        </div>
      </div>
    </form>
  </details>
</section>
"""


def _run_bar_text(report: AuditReport | None, input_value: str) -> str:
    """Короткая строка-шапка для свёрнутого блока запуска."""

    if report is None:
        return "Проверка локального проекта"
    project = Path(report.summary.input_path).name or Path(input_value).name or input_value
    cases = len(report.findings)
    return f"{project} · {cases} случаев"


def _render_error(error: str | None) -> str:
    """Показывает ошибку запуска, если она есть."""

    if not error:
        return ""
    return f'<div class="alert">{_esc(error)}</div>'


def _render_empty_state() -> str:
    """Показывает состояние до первого запуска."""

    return """
<section class="empty">
  <strong>Отчёта пока нет.</strong>
  <div>Запустите проверку по пути к проекту, чтобы увидеть панель отчёта.</div>
</section>
"""


def _render_dashboard(report: AuditReport, report_dir: Path) -> str:
    """Собирает панель отчёта."""

    return "\n".join(
        [
            _render_summary(report),
            _render_criterion_filters(report),
            _render_filter_bar(),
            _render_findings_table(report.findings),
            _render_observability(report),
            _render_downloads(report_dir),
        ]
    )


def _render_filter_bar() -> str:
    """Фильтры таблицы: работают на клиенте, без повторного прогона."""

    return """
<section class="filter-bar" id="filter-bar">
  <span class="filter-bar-label">Фильтры таблицы</span>
  <span class="filter-chip" id="active-criterion-label">Критерий: все</span>
  <span class="filter-chip" id="active-severity-label">Критичность: все</span>
  <span class="filter-chip" id="active-column-filter-label">Колонки: нет</span>
  <label class="check"><input type="checkbox" id="flt-hide-unknown"> Скрыть «нужна проверка»</label>
  <span class="filter-note" id="filter-result-count">видно: 0</span>
</section>
"""


def _case_findings(report: AuditReport) -> list[Finding]:
    """Возвращает строки, требующие внимания аудитора."""

    return list(report.findings)


def _render_summary(report: AuditReport) -> str:
    """Показывает главные счётчики."""

    summary = report.summary
    cases = _case_findings(report)
    by_severity = Counter(finding.severity.value for finding in cases)
    return f"""
<section id="summary" class="summary-strip">
  <div class="summary-main">
    <span class="summary-number">{len(cases)}</span>
    <span class="summary-text">случаев · {summary.files_total} файлов</span>
  </div>
  <div class="severity-inline">
    {_severity_chip(Severity.CRITICAL, by_severity.get(Severity.CRITICAL.value, 0))}
    {_severity_chip(Severity.MAJOR, by_severity.get(Severity.MAJOR.value, 0))}
    {_severity_chip(Severity.MINOR, by_severity.get(Severity.MINOR.value, 0))}
    {_severity_chip(Severity.INFO, by_severity.get(Severity.INFO.value, 0))}
  </div>
</section>
"""


def _render_observability(report: AuditReport) -> str:
    """Показывает техническую сводку выполнения."""

    usage = report.summary.model_usage
    usage_rows = {
        "Свежие вызовы": usage.calls_total,
        "Ответы из кэша": usage.cache_hits,
        "Токены": usage.total_tokens,
        "Стоимость, $": round(usage.cost_usd, 6),
    }
    step_rows = {step.name: step.duration_ms for step in report.summary.steps}
    usage_markup = (
        _bars(usage_rows, {})
        if any(value for value in usage_rows.values())
        else '<div class="metric-empty">Свежих вызовов моделей нет.</div>'
    )
    return f"""
<details class="section diagnostics">
  <summary>Диагностика прогона — шаги, стоимость, покрытие ТЗ, версии запросов</summary>
  <div class="diagnostics-body">
    <div class="muted">версии запросов: {_esc(', '.join(report.summary.prompt_versions.values()) or 'нет')}</div>
    <div class="grid-three">
    <div class="panel">
      <label>Стоимость и кэш</label>
      {usage_markup}
    </div>
    <div class="panel">
      <label>Шаги, мс</label>
      {_bars(step_rows, {})}
    </div>
    <div class="panel">
      <label>Покрытие ТЗ</label>
      {_render_requirement_status(report)}
    </div>
  </div>
  </div>
</details>
"""


def _render_criterion_filters(report: AuditReport) -> str:
    """Рисует компактные чипы критериев, которые фильтруют таблицу."""

    cases = _case_findings(report)
    by_criterion = Counter(finding.criterion.value for finding in cases)
    buttons = [
        f"""
<button type="button" class="criterion-filter is-active" data-criterion-filter="all" data-criterion-label="все">
  <span>Все</span><span class="criterion-count">{len(cases)}</span>
</button>
"""
    ]
    for criterion in Criterion:
        count = by_criterion.get(criterion.value, 0)
        empty_class = " is-empty" if count == 0 else ""
        buttons.append(
            f"""
<button type="button" class="criterion-filter{empty_class}" data-criterion-filter="{criterion.value}" data-criterion-label="{_esc(CRITERION_LABELS[criterion])}" title="{_esc(CRITERION_LABELS[criterion])}">
  <span>{_esc(_criterion_short_label(criterion))}</span><span class="criterion-count">{count}</span>
</button>
"""
        )
    return f"""
<section class="criteria-strip">
  <div class="criteria-title">Критерий — фильтр таблицы</div>
  <div class="criteria-hint">Нажмите критерий, чтобы оставить в таблице только связанные с ним сообщения.</div>
  <div class="criteria-chips">{"".join(buttons)}</div>
</section>
"""


def _render_requirement_status(report: AuditReport) -> str:
    """Показывает, какие управленческие поля из ТЗ есть в текущем отчёте."""

    summary = report.summary
    usage = summary.model_usage
    cases = _case_findings(report)
    affected_units = len({finding.unit_id for finding in cases})
    affected_branches = len({finding.branch or "без ветки" for finding in cases})
    rows = [
        ("Ветка и единица", f"{affected_branches} веток · {affected_units} ед."),
        ("Критичность", "Critical / Major / Minor / Info"),
        ("Экспорт", "XLSX / CSV / JSON"),
        ("Ссылки", "сеть использовалась" if summary.network_used else "сеть выключена"),
        ("Стоимость", "учтена" if usage.calls_total or usage.cache_hits else "нет модельных вызовов"),
    ]
    return _metric_rows(rows)


def _severity_chip(severity: Severity, count: int) -> str:
    """Рисует компактный счётчик критичности в общей сводке."""

    return (
        f'<button type="button" class="severity-chip severity-chip-{severity.value}" '
        f'data-severity-filter="{severity.value}" '
        f'data-severity-label="{_esc(SEVERITY_LABELS[severity].lower())}">'
        f'{count} {_esc(SEVERITY_LABELS[severity].lower())}</button>'
    )


def _criterion_short_label(criterion: Criterion) -> str:
    """Короткие подписи нужны, чтобы фильтры помещались в одну строку."""

    labels = {
        Criterion.ACTUALITY: "Актуальность",
        Criterion.LINKS: "Ссылки",
        Criterion.TECHNOLOGY_FRESHNESS: "Технологии",
        Criterion.FACTS: "Факты",
        Criterion.MARKET_FIT: "Рынок",
        Criterion.RIGHTS: "Права",
        Criterion.CORRECTNESS: "Точность",
        Criterion.READABILITY: "Грамотность",
        Criterion.CHECKLIST_ALIGNMENT: "Чек-лист",
        Criterion.WORKLOAD: "Трудоёмкость",
        Criterion.EXAM: "Экзамен",
        Criterion.LANGUAGE: "Язык",
        Criterion.IMAGE_QUALITY: "Изображения",
    }
    return labels.get(criterion, CRITERION_LABELS[criterion])


TABLE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("criterion", "Критерий"),
    ("kind", "Тип"),
    ("verdict", "Вердикт"),
    ("severity", "Критичность"),
    ("file", "Файл"),
    ("line", "Строка"),
    ("fragment", "Фрагмент"),
    ("evidence", "Обоснование"),
    ("source", "Источник"),
    ("checked", "Дата проверки"),
    ("support", "Статус поддержки"),
    ("latest", "Последняя версия"),
    ("recommended", "Рекомендуемая версия"),
    ("confidence", "Уверенность"),
    ("module", "Модуль"),
)


def _render_table_header(column_key: str, label: str) -> str:
    """Возвращает заголовок колонки с клиентским фильтром."""

    return f"""
          <th>
            <button type="button" class="column-filter-trigger" data-column-filter="{column_key}" data-column-label="{_esc(label)}" aria-expanded="false">
              <span>{_esc(label)}</span>
              <span class="column-filter-mark" aria-hidden="true">▾</span>
            </button>
          </th>"""


def _render_findings_table(findings: list[Finding]) -> str:
    """Показывает таблицу найденных случаев."""

    headers = "\n".join(_render_table_header(key, label) for key, label in TABLE_COLUMNS)
    rows = "\n".join(_render_finding_row(finding) for finding in findings)
    if not rows:
        rows = '<tr><td colspan="15">По выбранным условиям случаев нет.</td></tr>'
    rows += '\n<tr id="no-match" class="no-match" style="display:none"><td colspan="15">Под выбранные фильтры ничего не попадает.</td></tr>'
    return f"""
<section id="findings" class="section">
  <div class="section-head">
    <h2>Таблица результата</h2>
    <span class="muted">одна строка — один найденный случай</span>
  </div>
  <div class="table-wrap">
    <table id="findings-table" class="findings">
      <colgroup>
        <col class="col-criterion">
        <col class="col-kind">
        <col class="col-verdict">
        <col class="col-severity">
        <col class="col-file">
        <col class="col-line">
        <col class="col-fragment">
        <col class="col-evidence">
        <col class="col-source">
        <col class="col-checked">
        <col class="col-support">
        <col class="col-latest">
        <col class="col-recommended">
        <col class="col-confidence">
        <col class="col-module">
      </colgroup>
      <thead>
        <tr>
{headers}
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</section>
"""


def _render_finding_row(finding: Finding) -> str:
    """Показывает строку найденного случая."""

    file_path = finding.location.file_path if finding.location else ""
    line = str(finding.location.line_start or "") if finding.location else ""
    checked_at = finding.checked_at.isoformat() if finding.checked_at else ""
    explanation = format_finding_explanation_html(finding, _esc)
    fragment = format_finding_fragment(finding)
    return f"""
<tr class="frow" data-criterion="{finding.criterion.value}" data-kind="{finding.issue_kind.value}" data-verdict="{finding.verdict.value}" data-severity="{finding.severity.value}">
  <td>{_esc(CRITERION_LABELS[finding.criterion])}</td>
  <td>{_esc(ISSUE_KIND_LABELS[finding.issue_kind])}</td>
  <td>{_pill(VERDICT_LABELS[finding.verdict], f"pill-{finding.verdict.value}")}</td>
  <td>{_pill(SEVERITY_LABELS[finding.severity], f"pill-{finding.severity.value}")}</td>
  <td class="mono">{_esc(file_path)}</td>
  <td class="mono">{_esc(line)}</td>
  <td class="fragment">{_esc(fragment)}</td>
  <td class="evidence">{explanation}</td>
  <td class="source">{_esc(finding.source or "")}</td>
  <td class="mono">{_esc(checked_at)}</td>
  <td>{_esc(finding.support_status or "")}</td>
  <td class="mono">{_esc(finding.latest_version or "")}</td>
  <td class="mono">{_esc(finding.recommended_version or "")}</td>
  <td class="mono">{finding.confidence:.2f}</td>
  <td class="mono">{_esc(finding.checker_name)}</td>
</tr>
"""


def _render_downloads(report_dir: Path) -> str:
    """Показывает ссылки на файлы отчёта."""

    links = [
        ("XLSX", "report.xlsx"),
        ("CSV", "report.csv"),
        ("JSON", "report.json"),
    ]
    items = "\n".join(f'<a class="link-button" href="/download?file={quote(name)}">{label}</a>' for label, name in links)
    return f"""
<section class="section">
  <div class="section-head">
    <h2>Файлы отчёта</h2>
  </div>
  <div class="downloads">{items}</div>
</section>
"""


def _render_script() -> str:
    """Добавляет минимальное поведение формы."""

    return f"""
<script>
{_audit_web_asset("report.js")}
</script>
"""


def _metric_rows(rows: Sequence[tuple[str, object]]) -> str:
    """Рисует компактные строки без шкалы, когда важнее статус, а не объём."""

    if not rows:
        return '<div class="metric-empty">Нет данных.</div>'
    return "\n".join(
        f"""
<div class="metric-item">
  <div class="metric-name">{_esc(name)}</div>
  <div class="metric-value">{_esc(str(value))}</div>
</div>
"""
        for name, value in rows
    )


def _bars(values: Mapping[str, float], labels: dict[str, str], sort_by_count: bool = True) -> str:
    """Рисует горизонтальные полосы распределения."""

    if not values:
        return '<div class="muted">Нет данных.</div>'
    max_value = max(values.values()) or 1
    rows = []
    items = sorted(values.items(), key=lambda item: item[1], reverse=True) if sort_by_count else values.items()
    for key, count in items:
        width = 0 if count <= 0 else max(4, round(count / max_value * 100))
        rows.append(
            f"""
<div class="bar-row">
  <div class="bar-label">{_esc(labels.get(key, key))}</div>
  <div class="bar-track"><div class="bar-fill" style="width:{width}%"></div></div>
  <div class="bar-count">{count}</div>
</div>
"""
        )
    return "\n".join(rows)


def _pill(label: str, css_class: str) -> str:
    """Рисует статусную метку."""

    return f'<span class="pill {css_class}">{_esc(label)}</span>'


def _esc(value: str) -> str:
    """Экранирует текст для HTML."""

    return html.escape(unquote(value), quote=True)
