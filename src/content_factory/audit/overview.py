"""Единый верхнеуровневый отчёт по ВСЕМ проведённым аудитам веток — self-contained HTML-файл.

Руководителю нужен один документ: какие ветки актуальные (идут пиры) и что чинить первым.
Приоритет ветки определяет учебная активность из DWH (когда были последние сдачи): ветки с
живыми проектами — вверх, «нет данных» (имя не сматчилось) — в свёрнутую секцию, не хоронить.

Собирается целиком из того, что уже в памяти аудитора: `BranchReport` на ветку (имена проектов +
их `summary` c findings/severity) плюс матчер активности ``activity``. Ноль чтений с диска,
без переаудита. Модуль чистый: агрегатор ``build_overview`` тестируется без сети и роутера,
``render_overview_html`` даёт автономный файл (inline CSS + нативные ``<details>``, без внешних
ассетов — открывается офлайн).
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any

from content_factory.audit import activity
from content_factory.audit.activity import ACTIVE, ARCHIVED, DWH_SNAPSHOT_DATE, FADING, NO_DATA

# Ветка на вход: (branch_id, archive_name, created_at, BranchReport-подобный объект с .projects).
BranchInput = tuple[str, str, str, Any]

_ACTIVITY_CSS = {ACTIVE: "active", FADING: "fading", ARCHIVED: "archived", NO_DATA: "nodata"}
_PROJECT_RANK = {ACTIVE: 0, FADING: 1, ARCHIVED: 2, NO_DATA: 3}

# Русское название ветки по её коду (имени zip). Проставлено по содержимому проектов ветки
# (см. историю разбора). Ключ — код в ВЕРХНЕМ регистре без ".zip". Служебные/личные/тестовые
# и транслит-ветки помечены явно. Нет ключа → в отчёте показывается сам код.
_BRANCH_NAMES = {
    "A": "Ядро: алгоритмы (A-серия, все языки)",
    "AI": "Искусственный интеллект",
    "AIE": "ИИ-инженер: LLM, агенты, RAG",
    "ANALYSIS": "Аналитика данных (pandas)",
    "AND": "Android-разработка",
    "ANNA": "Личные прогоны (Анна)",
    "AP": "Проверочные АР (по языкам)",
    "APAI": "ИИ: локальные LLM и агенты",
    "APC": "Проекты C#",
    "APEX": "Экзаменационные упражнения (APEx)",
    "APG": "Проекты Go",
    "APJ": "Проекты Java",
    "APP": "Проекты Python",
    "AQA": "Автотестирование (AQA)",
    "AR": "BrickGame (мультиязык)",
    "ASSEMBLER": "Ассемблер",
    "BA": "Бизнес-анализ",
    "BASSEYON": "Бассейн (интенсив)",
    "BE": "Бэкенд-разработка",
    "BINF": "Биоинформатика",
    "BSA": "Бизнес- и системный анализ",
    "BUSINESS": "Бизнес-анализ",
    "C": "Ядро C (s21-проекты)",
    "CAREER": "Карьерный трек",
    "CASE": "Кейсы (анализ)",
    "CBS": "Кибербезопасность",
    "CORE": "Core AI (проекты)",
    "COREAI": "Core AI (дни и проекты)",
    "CPP": "Ядро C++",
    "CPPE": "Экзамен C++",
    "CPPEX": "Упражнения C++",
    "CSEC": "Кибербезопасность (CTF/CSec)",
    "CT": "Карьерный трек (soft skills)",
    "D": "Дни основного потока (ITS/Linux/UX)",
    "DA": "Аналитика данных (DA)",
    "DAML": "Data Analytics и ML",
    "DATA": "Аналитика данных",
    "DAY": "Учебные дни (Python/SQL/PM/UX)",
    "DEAP": "Data Engineering и Deep Learning",
    "DEN": "Дни (личные прогоны)",
    "DEVOPS": "DevOps",
    "DIGITAL": "Цифровой маркетинг",
    "DO": "DevOps",
    "DS": "Data Science",
    "DSB": "Data Science Bootcamp",
    "DSPY": "Data Science (Python)",
    "DSPYTHON": "Data Science (Python)",
    "EEB": "Электроника и встраиваемые системы",
    "EX": "Упражнения (Ex, ITS)",
    "FE": "Фронтенд",
    "FEB": "Фронтенд Bootcamp",
    "FINAL": "Финальные проекты и экзамены",
    "FRONT": "Фронтенд",
    "FRONTEND": "Фронтенд",
    "GAI": "Генеративный ИИ",
    "GD": "Геймдев (Unity)",
    "GEN": "Gen: карьерный трек и данные",
    "GENAI": "Генеративный ИИ",
    "GO": "Go",
    "GRUP": "Групповые прогоны (тест)",
    "INTENSIVE": "Интенсивы",
    "IOS": "iOS-разработка",
    "JAVA": "Java Bootcamp",
    "KOTLIN": "Kotlin Bootcamp",
    "L": "Инженерные проекты (электроника, робо, 3D)",
    "LANG": "LLM и промпт-инжиниринг",
    "MISC": "Сборная: ИИ-безопасность и компьютерное зрение",
    "MK": "Мобильная разработка (MK)",
    "ML": "Машинное обучение",
    "MOBILE": "Мобильная разработка",
    "MT": "Ручное тестирование",
    "MV": "Творческий кодинг (MV Bootcamp)",
    "MVP": "MVP (продуктовый)",
    "OT": "Личные прогоны (тест)",
    "P": "Проектные дни (P-коды)",
    "PASKAL": "Паскаль",
    "PJM": "Проектный менеджмент",
    "PM": "Проектный и продуктовый менеджмент",
    "PRACTICE": "Практика для IT",
    "PRCTC": "Практика (проекты)",
    "PREMODULE": "Премодуль",
    "PRODUCT": "Продуктовая разработка",
    "PROJECT": "Сборная: проекты SQL/Python",
    "PROYEKT": "ИИ-агенты (проекты)",
    "PYTHON": "Python",
    "PYTHONDS": "Python для Data Science",
    "PYTHONDSBASICS": "Python DS (базовый)",
    "PYTHONDSLIGHT": "Python DS (лёгкий)",
    "QA": "Тестирование (QA)",
    "ROSB": "Робототехника (ROS)",
    "RV": "Групповые прогоны (тест)",
    "SA": "Системная архитектура",
    "SBERJV": "Java (Сбер, интенсив)",
    "SELECT": "Отборочные (Select ITS)",
    "SOFTLEAD": "Soft skills и лидерство",
    "SOZDANIYE": "ИИ-агенты (создание прототипа)",
    "SQL": "SQL",
    "SQLB": "SQL Bootcamp",
    "SWIFT": "Swift Bootcamp",
    "T": "Задания основного потока (T-коды)",
    "TASK": "Тестовые задания",
    "TEST": "Тестовое (служебное)",
    "TESTGROUP": "Тестовое (групповое)",
    "TESTIND": "Тестовое (индивидуальное)",
    "TESTOVOYE": "Тестовые задания",
    "TP": "Технологическое предпринимательство",
    "UX": "UX/UI дизайн",
    "UXUI": "UX/UI дизайн",
    "WEB": "Веб-разработка (ITS)",
    "WEEKLYTHON": "Виклитон (хакатон)",
    "YEKZAMEN": "Экзамен (ассемблер)",
    "ZADANIYE": "Тестовые задания",
    "ZV": "Личные прогоны (ZV)",
}


def _branch_label(archive_name: str) -> tuple[str, str]:
    """(русское название, код-ветки). Код = имя zip без ``.zip``; название — из ``_BRANCH_NAMES``
    (пусто, если кода нет в словаре — тогда в отчёте показывается сам код)."""

    code = archive_name[:-4] if archive_name.lower().endswith(".zip") else archive_name
    return _BRANCH_NAMES.get(code.upper(), ""), code


@dataclass(frozen=True)
class ProjectRow:
    name: str
    activity: str
    students_last_365d: int
    findings: int
    critical: int
    major: int
    fuzzy: bool


@dataclass(frozen=True)
class BranchRow:
    branch_id: str
    archive_name: str
    created_at: str
    tier: int  # 0 есть активные / 1 только затухают / 2 только архив / 3 всё нет данных
    total_projects: int
    active: int
    fading: int
    archived: int
    no_data: int
    students_last_365d: int
    days_since_last: int | None  # свежесть: самая недавняя сдача по ветке
    findings: int
    critical: int
    major: int
    critmaj_on_active: int  # critical+major на ЖИВЫХ проектах — «чинить первым»
    projects: tuple[ProjectRow, ...]

    @property
    def critmaj(self) -> int:
        return self.critical + self.major

    @property
    def pct_active(self) -> int:
        return round(100 * self.active / self.total_projects) if self.total_projects else 0


@dataclass(frozen=True)
class Overview:
    total_branches: int
    total_projects: int
    total_findings: int
    total_critical: int
    total_major: int
    total_students: int
    active_projects: int
    fading_projects: int
    archived_projects: int
    no_data_projects: int
    snapshot_date: str
    ranked: tuple[BranchRow, ...]  # tier 0..2 — основная таблица, приоритет сверху
    unknown: tuple[BranchRow, ...]  # tier 3 — свёрнутая секция «актуальность неизвестна»


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _severity(summary: Any, key: str) -> int:
    if not isinstance(summary, dict):
        return 0
    by_severity = summary.get("by_severity")
    return _int(by_severity.get(key)) if isinstance(by_severity, dict) else 0


def _build_branch_row(branch_id: str, archive_name: str, created_at: str, report: Any) -> BranchRow:
    projects = list(getattr(report, "projects", []))
    names = [project.name for project in projects]
    summary = activity.summarize_branch(names)

    rows: list[ProjectRow] = []
    findings = critical = major = critmaj_on_active = 0
    days_values: list[int] = []
    for project in projects:
        info = activity.lookup(project.name)
        f = _int(getattr(project, "summary", None) and project.summary.get("findings_total"))
        crit = _severity(project.summary, "critical")
        maj = _severity(project.summary, "major")
        findings += f
        critical += crit
        major += maj
        if info.activity == ACTIVE:
            critmaj_on_active += crit + maj
        if info.days_since_last is not None:
            days_values.append(info.days_since_last)
        rows.append(
            ProjectRow(
                name=project.name,
                activity=info.activity,
                students_last_365d=info.students_last_365d,
                findings=f,
                critical=crit,
                major=maj,
                fuzzy=info.fuzzy,
            )
        )
    rows.sort(key=lambda row: (_PROJECT_RANK.get(row.activity, 9), -row.students_last_365d, -row.findings))

    if summary.active:
        tier = 0
    elif summary.fading:
        tier = 1
    elif summary.archived:
        tier = 2
    else:
        tier = 3
    return BranchRow(
        branch_id=branch_id,
        archive_name=archive_name,
        created_at=created_at,
        tier=tier,
        total_projects=len(projects),
        active=summary.active,
        fading=summary.fading,
        archived=summary.archived,
        no_data=summary.no_data,
        students_last_365d=summary.students_last_365d,
        days_since_last=min(days_values) if days_values else None,
        findings=findings,
        critical=critical,
        major=major,
        critmaj_on_active=critmaj_on_active,
        projects=tuple(rows),
    )


def build_overview(branches: list[BranchInput]) -> Overview:
    """Свести все аудированные ветки в один приоритизированный отчёт.

    Приоритет: ветки с активными проектами сверху (tier 0), затем только затухающие (1), только
    архив (2); ветки, где всё «нет данных» (3), уходят в отдельную секцию. Внутри — активные
    ученики/год ↓, тай-брейк critical+major на живых проектах ↓."""

    all_rows = [_build_branch_row(bid, arch, created, report) for bid, arch, created, report in branches]
    ranked = sorted(
        (row for row in all_rows if row.tier < 3),
        key=lambda row: (row.tier, -row.students_last_365d, -row.critmaj_on_active, row.archive_name.lower()),
    )
    unknown = sorted((row for row in all_rows if row.tier == 3), key=lambda row: row.archive_name.lower())
    return Overview(
        total_branches=len(all_rows),
        total_projects=sum(row.total_projects for row in all_rows),
        total_findings=sum(row.findings for row in all_rows),
        total_critical=sum(row.critical for row in all_rows),
        total_major=sum(row.major for row in all_rows),
        total_students=sum(row.students_last_365d for row in all_rows),
        active_projects=sum(row.active for row in all_rows),
        fading_projects=sum(row.fading for row in all_rows),
        archived_projects=sum(row.archived for row in all_rows),
        no_data_projects=sum(row.no_data for row in all_rows),
        snapshot_date=DWH_SNAPSHOT_DATE,
        ranked=tuple(ranked),
        unknown=tuple(unknown),
    )


# --------------------------------------------------------------------------- rendering


def _num(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _mix_bar(row: BranchRow) -> str:
    total = row.total_projects or 1
    segments = [(ACTIVE, row.active), (FADING, row.fading), (ARCHIVED, row.archived), (NO_DATA, row.no_data)]
    cells = "".join(
        f'<span class="seg seg-{_ACTIVITY_CSS[status]}" style="width:{100 * count / total:.1f}%"></span>'
        for status, count in segments
        if count
    )
    return f'<span class="mixbar" title="активен {row.active} · затухает {row.fading} · архив {row.archived} · нет данных {row.no_data}">{cells}</span>'


def _freshness(days: int | None) -> str:
    if days is None:
        return '<span class="muted">—</span>'
    if days == 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    return f"{_num(days)} дн назад"


def _badge(status: str, fuzzy: bool = False) -> str:
    label = escape(status) + ("&nbsp;*" if fuzzy else "")
    return f'<span class="act act-{_ACTIVITY_CSS.get(status, "nodata")}">{label}</span>'


def _project_detail(row: BranchRow) -> str:
    body = "".join(
        f"<tr><td>{escape(project.name)}</td><td>{_badge(project.activity, project.fuzzy)}</td>"
        f'<td class="num">{_num(project.students_last_365d) if project.students_last_365d else "—"}</td>'
        f'<td class="num">{project.findings}</td>'
        f'<td class="num">{project.critical + project.major or "—"}</td></tr>'
        for project in row.projects
    )
    return (
        '<table class="proj"><thead><tr><th>Проект</th><th>Активность</th><th>Ученики/год</th>'
        f"<th>Замеч.</th><th>Crit+Maj</th></tr></thead><tbody>{body}</tbody></table>"
    )


def _branch_rows_html(branches: tuple[BranchRow, ...], start_rank: int) -> str:
    parts: list[str] = []
    for offset, row in enumerate(branches):
        rank = start_rank + offset
        name_ru, code = _branch_label(row.archive_name)
        bname = (
            f"{escape(name_ru)}<em>{escape(code)}</em>" if name_ru else escape(code)
        )
        parts.append(
            f'<details class="branch"><summary>'
            f'<span class="rank">{rank}</span>'
            f'<span class="bname">{bname}</span>'
            f'<span class="col">{_mix_bar(row)} <b>{row.pct_active}%</b> живых</span>'
            f'<span class="col num">{_num(row.students_last_365d)}<small>учен/год</small></span>'
            f'<span class="col">{_freshness(row.days_since_last)}<small>последняя сдача</small></span>'
            f'<span class="col num">{_num(row.findings)}<small>замечаний</small></span>'
            f'<span class="col num">{row.critmaj}<small>crit+maj</small></span>'
            f'<span class="col num accent">{row.critmaj_on_active}<small>на живых</small></span>'
            f"</summary>{_project_detail(row)}</details>"
        )
    return "".join(parts)


def render_overview_html(overview: Overview) -> str:
    """Автономный HTML-файл отчёта (inline CSS, нативные ``<details>``, без внешних ассетов)."""

    banner = (
        '<section class="banner">'
        f'<div class="metric"><b>{overview.total_branches}</b><span>веток аудировано</span></div>'
        f'<div class="metric"><b>{_num(overview.total_projects)}</b><span>проектов</span></div>'
        f'<div class="metric"><b>{_num(overview.total_findings)}</b><span>замечаний</span></div>'
        f'<div class="metric"><b>{_num(overview.total_critical + overview.total_major)}</b><span>critical+major</span></div>'
        '<div class="sep"></div>'
        f'<div class="metric"><b>{_num(overview.total_students)}</b><span>активных учеников за год</span></div>'
        f'<div class="metric"><b>{_num(overview.active_projects)}</b><span>живых проектов</span></div>'
        f'<div class="src">актуальность по данным School&nbsp;21 DWH · {overview.snapshot_date}</div>'
        "</section>"
    )
    legend = (
        '<div class="legend">Приоритет сверху — ветки с активными пирами. '
        f'{_badge(ACTIVE)} последняя сдача ≤ 90 дн и ≥ 20 учеников за год · '
        f"{_badge(FADING)} сдачи в пределах года, но условие не выполнено · "
        f"{_badge(ARCHIVED)} последняя сдача &gt; 365 дн · "
        f'«{_badge(NO_DATA)}» имя проекта не сматчилось с DWH (пробел в данных, не «мёртвый»). '
        "<b>Crit+Maj на живых</b> — что бьёт по текущим ученикам, чинить первым."
    )
    head = (
        '<tr><th>#</th><th>Ветка</th><th>Актуальность</th><th>Ученики/год</th>'
        "<th>Последняя сдача</th><th>Замеч.</th><th>Crit+Maj</th><th>На живых</th></tr>"
    )
    ranked_html = _branch_rows_html(overview.ranked, 1)
    unknown_block = ""
    if overview.unknown:
        unknown_html = _branch_rows_html(overview.unknown, len(overview.ranked) + 1)
        unknown_block = (
            f'<details class="unknown"><summary>Актуальность неизвестна — {len(overview.unknown)} '
            "веток (имена не сматчились с DWH; пробел в данных, не «мёртвые»)</summary>"
            f'<div class="rows head"><div class="hrow">{head}</div>{unknown_html}</div></details>'
        )
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Аудит контента — обзор всех веток</title>
<style>
 body{{font-family:-apple-system,'Segoe UI',system-ui,sans-serif;margin:0;background:#f6f8f7;color:#1c2b27;padding:28px;}}
 .wrap{{max-width:1180px;margin:0 auto;}}
 h1{{font-size:1.5rem;margin:0 0 2px;}} .sub{{color:#5b6b66;margin:0 0 20px;font-size:.9rem;}}
 .banner{{display:flex;flex-wrap:wrap;gap:22px;align-items:center;background:#fff;border-radius:12px;
   padding:16px 22px;box-shadow:0 1px 3px rgba(0,0,0,.06);margin-bottom:14px;}}
 .banner .metric{{display:flex;flex-direction:column;line-height:1.15;}}
 .banner .metric b{{font-size:1.4rem;}} .banner .metric span{{font-size:.72rem;color:#6b7a75;}}
 .banner .sep{{width:1px;align-self:stretch;background:#eef2f0;}}
 .banner .src{{margin-left:auto;font-size:.72rem;color:#9aa7a2;max-width:190px;text-align:right;}}
 .legend{{font-size:.78rem;color:#5b6b66;background:#fff;border-radius:10px;padding:11px 16px;margin-bottom:16px;
   box-shadow:0 1px 3px rgba(0,0,0,.05);line-height:1.9;}}
 .act{{display:inline-block;padding:1px 8px;border-radius:999px;font-size:.72rem;font-weight:600;white-space:nowrap;}}
 .act-active{{background:#dcfce7;color:#166534;}} .act-fading{{background:#fef3c7;color:#92400e;}}
 .act-archived{{background:#e5e7eb;color:#4b5563;}} .act-nodata{{background:#f1f5f9;color:#94a3b8;}}
 .rows{{background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06);}}
 .hrow th{{text-align:right;padding:10px 12px;font-size:.72rem;color:#3a4a45;background:#f0f4f2;font-weight:600;}}
 .hrow th:nth-child(1),.hrow th:nth-child(2),.hrow th:nth-child(3){{text-align:left;}}
 .hrow{{display:none;}}
 details.branch,details.unknown{{border-bottom:1px solid #eef2f0;}}
 details.branch>summary,details.unknown>summary{{list-style:none;cursor:pointer;}}
 details.branch>summary::-webkit-details-marker{{display:none;}}
 details.branch>summary{{display:grid;grid-template-columns:34px minmax(150px,1.4fr) 1.5fr .8fr 1fr .7fr .7fr .7fr;
   gap:10px;align-items:center;padding:11px 16px;}}
 details.branch>summary:hover{{background:#f0faf6;}}
 details.branch[open]>summary{{background:#eef7f3;}}
 .rank{{font-weight:700;color:#0e8f6f;font-size:.95rem;}}
 .bname{{font-weight:600;word-break:break-word;}}
 .bname em{{display:block;font-style:normal;font-size:.68rem;color:#9aa7a2;font-weight:400;margin-top:1px;}}
 .col{{display:flex;flex-direction:column;font-size:.9rem;line-height:1.15;}}
 .col.num{{text-align:right;align-items:flex-end;font-variant-numeric:tabular-nums;}}
 .col small{{font-size:.64rem;color:#9aa7a2;font-weight:400;}}
 .col.accent b,.col.accent{{color:#b91c1c;font-weight:700;}}
 .mixbar{{display:inline-flex;height:9px;width:100%;max-width:140px;border-radius:5px;overflow:hidden;background:#eef2f0;}}
 .seg{{height:100%;}} .seg-active{{background:#22c55e;}} .seg-fading{{background:#f59e0b;}}
 .seg-archived{{background:#9ca3af;}} .seg-nodata{{background:#e2e8f0;}}
 .proj{{width:100%;border-collapse:collapse;background:#fbfdfc;}}
 .proj th,.proj td{{text-align:left;padding:7px 16px;border-top:1px solid #eef2f0;font-size:.82rem;}}
 .proj th{{color:#6b7a75;font-weight:600;font-size:.72rem;}} .proj td.num,.proj th:nth-child(n+3){{text-align:right;}}
 .num{{font-variant-numeric:tabular-nums;}} .muted{{color:#9aa7a2;}} .accent{{color:#b91c1c;}}
 details.unknown{{margin-top:18px;border:1px solid #eef2f0;border-radius:12px;background:#fff;overflow:hidden;}}
 details.unknown>summary{{padding:13px 18px;font-weight:600;color:#5b6b66;font-size:.9rem;cursor:pointer;}}
</style></head><body><div class="wrap">
<h1>Аудит контента — обзор всех веток</h1>
<p class="sub">Единый верхнеуровневый отчёт по всем проведённым аудитам. Приоритет — по учебной активности (когда идут сдачи). Разверните ветку, чтобы увидеть проекты.</p>
{banner}
{legend}
<div class="rows"><div class="hrow">{head}</div>{ranked_html}</div>
{unknown_block}
</div></body></html>"""
