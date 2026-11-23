/* Chat workspace for project preparation. Every source text is inserted as text, never as HTML. */
'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const ICONS = {
  plus: ['M12 5v14M5 12h14'],
  clip: ['M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48'],
  send: ['M12 19V5M5 12l7-7 7 7'],
  chevron: ['M6 9l6 6 6-6'],
  check: ['M20 6L9 17l-5-5'],
  pencil: ['M12 20h9', 'M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z'],
  x: ['M18 6L6 18M6 6l12 12'],
  menu: ['M4 6h16M4 12h16M4 18h16'],
  file: ['M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z', 'M14 2v6h6'],
  arrow: ['M5 12h14M13 5l7 7-7 7'],
};
const LEVELS = {'': 'Не указан', zero: 'Нулевой: не знакомы с инструментами', basic: 'Базовый', intermediate: 'Средний', advanced: 'Продвинутый'};
const LEVEL_SHORT = {'': 'не указан', zero: 'нулевой', basic: 'базовый', intermediate: 'средний', advanced: 'продвинутый'};
// How a project is told (content/styles/): the methodologist may choose in the form; the assistant says which it takes.
const STYLE = {'': 'Предложит ассистент', quest: 'Квест, как в бассейне', legend: 'Рабочая легенда'};
const STORY = {none: 'Без сторителлинга', sjm: 'Практический / SJM', case: 'Рабочий кейс', role_play: 'Ролевая ситуация', project_scenario: 'Сценарий проекта', story_arc: 'Сквозная история'};
const ROLES = {reference: 'Справочно — ИИ отберёт нужное', requirement: 'Обязательное — передать целиком', customer: 'Бриф заказчика — передать целиком', example: 'Пример проекта', starter: 'Данные или заготовка', reviewer: 'Для проверяющего'};
const READ = {text_extracted: 'текст извлечён', partial: 'есть непрочитанные части', failed: 'не прочитан', legacy_unknown: 'старый импорт'};
const KIND = {given: ['Задано', 'ok'], proposal: ['Предложено', 'info'], conflict: ['Противоречие', 'del'], unknown: ['Неизвестно', 'grey']};
const AREAS = {outcomes: 'Образовательные результаты', audience: 'Аудитория', constraints: 'Ограничения', program: 'Программа', materials: 'Материалы'};
const CHECK = {confirmed: 'подтверждено', problem: 'есть проблема', needs_information: 'нужны данные', deferred: 'на следующем этапе', not_applicable: 'неприменимо'};
const FRESH = {current: ['Актуально', 'ok'], outdated: ['Устарело', 'del'], unclear: ['Неясно', 'warn']};
const STATUS = {running: 'идёт подготовка', input_review: 'уточнение входов', review: 'на обсуждении', approved: 'согласовано', failed: 'не завершено', interrupted: 'остановлено'};
const COLUMN_LABELS = {block: 'Блок', goals: 'Цели блока', order: '№', code: 'Код проекта', title: 'Название', description: 'Описание', cases: 'Кейсы (в описании)', outcomes: 'ОР', skills: 'Навыки', audience: 'Уровень аудитории', tools: 'Инструменты', software: 'ПО (в поле инструментов)', storytelling: 'Сторителлинг', storytelling_type: 'Тип сторителлинга', repository: 'GitLab', format: 'Формат', group_size: 'Размер группы', hours: 'Трудоёмкость', hours_academic: 'Трудоёмкость (ак. часы, пересчитаны)', checking: 'Форма контроля', materials: 'Материалы'};
const TEXT_FIELDS = ['title', 'description', 'audience', 'start_skills', 'learner_term', 'storytelling', 'series_story', 'program_outcomes', 'customer_requirements', 'tools', 'skills', 'student_result', 'checking', 'output_package', 'constraints', 'repository', 'references', 'notes'];
const MORE_FIELDS = ['program_outcomes', 'customer_requirements', 'learner_term', 'tools', 'skills', 'student_result', 'checking', 'output_package', 'constraints', 'repository', 'references', 'notes'];
const NUMBER_FIELDS = new Set(['hours', 'tasks_count', 'group_size']);
const FLAG_FIELDS = new Set(['diagrams', 'tables', 'formulas', 'bonus']);
const GROUPS = [
  ['Проект', ['title', 'description', 'outcomes']],
  ['Аудитория', ['audience', 'start_level', 'start_skills', 'learner_term']],
  ['Формат и объём', ['format', 'group_size', 'hours', 'tasks_count']],
  ['Содержание', ['storytelling_type', 'storytelling', 'skills', 'tools']],
  ['Результат и проверка', ['student_result', 'checking', 'output_package']],
  ['Программа и заказчик', ['program_outcomes', 'customer_requirements']],
  ['Замечания к замыслу', ['concept_feedback']],
  ['Прочее', ['constraints', 'references', 'notes', 'excluded_sources', 'diagrams', 'tables', 'formulas', 'bonus']],
];

const S = {
  config: null, ns: 's21.project-lab.v1', thread: null, runs: {}, curriculum: null, planFile: null, planTurn: 0, rowId: '', materials: [],
  outcomes: [''], legacy: {direction: '', program: ''}, uploads: 0, poll: null, tab: 'fields', sending: false,
  pendingText: '', pendingNote: '', editing: null, fieldEdit: null, budget: null, open: new Map(), workloadTimer: null,
  content: null,  // the content draft of the approved version (static/content.js), or its start step
};

/* ---------- small helpers ---------- */
function icon(name) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  for (const [key, value] of Object.entries({viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', class: 'icon'})) svg.setAttribute(key, value);
  for (const d of ICONS[name] || []) { const path = document.createElementNS(ns, 'path'); path.setAttribute('d', d); svg.append(path); }
  return svg;
}
function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'text') el.textContent = value;
    else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (['hidden', 'disabled', 'checked', 'value', 'selected'].includes(key)) el[key] = value;
    else el.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of children.flat(Infinity)) if (child !== null && child !== undefined && child !== false) el.append(child instanceof Node ? child : String(child));
  return el;
}
const num = (value) => String(Math.round(value * 100) / 100).replace('.', ',');
const isEmpty = (value) => value === null || value === undefined || value === '';
const outcomesOf = (text) => (text || '').split('\n').map((s) => s.trim()).filter(Boolean);
/* Methodologists name outcomes by their wording, not by number: numbers stay in the data, people see the text. */
const outcomeTexts = (run) => outcomesOf(run && run.request.brief.outcomes);
function outcomeName(run, n) { const text = outcomeTexts(run)[n - 1]; return text ? `«${text}»` : `ОР ${n}`; }
function expandOutcomes(run, text) {
  const list = outcomeTexts(run);
  return String(text || '').replace(/ОР\s*№?\s*(\d+(?:\s*(?:,|и)\s*\d+)*)(?![\d–-]|\.\d)/g, (match, numbers) => {
    const ns = numbers.split(/\s*(?:,|и)\s*/).map(Number);
    return ns.every((n) => n >= 1 && n <= list.length) ? `ОР ${ns.map((n) => `«${list[n - 1]}»`).join(', ')}` : match;
  });
}
const time = (iso) => new Date(iso).toLocaleTimeString('ru-RU', {hour: '2-digit', minute: '2-digit'});
const dateTime = (iso) => new Date(iso).toLocaleString('ru-RU', {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'});
function plural(n, one, few, many) {
  const mod10 = n % 10, mod100 = n % 100;
  return mod10 === 1 && mod100 !== 11 ? one : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14) ? few : many;
}
function fmt(field, value) {
  if (isEmpty(value)) return '';
  if (typeof value === 'boolean') return value ? 'да' : 'нет';
  if (field === 'start_level') return LEVELS[value] || value;
  if (field === 'format') return value === 'group' ? 'Групповой' : 'Индивидуальный';
  if (field === 'storytelling_type') return STORY[value] || value;
  if (typeof value === 'number') return num(value);
  return String(value);
}
const store = {
  get(key, fallback) { try { const value = localStorage.getItem(`${S.ns}.${key}`); return value ? JSON.parse(value) : fallback; } catch { return fallback; } },
  set(key, value) { try { if (value === null) localStorage.removeItem(`${S.ns}.${key}`); else localStorage.setItem(`${S.ns}.${key}`, JSON.stringify(value)); } catch { /* browser storage is optional */ } },
};
async function api(url, options = {}) {
  const response = await fetch(url, options);
  let result = null;
  try { result = await response.json(); } catch { /* empty or non-JSON body */ }
  if (!response.ok) {
    const detail = result && result.detail;
    throw new Error(Array.isArray(detail) ? detail.map((e) => `${(e.loc || []).slice(1).join('.')}: ${e.msg}`).join('\n') : detail || `Ошибка ${response.status}`);
  }
  return result;
}
function post(url, body) {
  return api(url, body === undefined ? {method: 'POST'} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
}
function notify(message) { const box = $('#intakeNotice'); box.textContent = message; box.hidden = !message; }
function notifyConv(message) { const box = $('#convNotice'); box.textContent = message; box.hidden = !message; }

/* An exam is a control point of the programme, not a project: it is not counted among the projects and cannot be chosen. */
const isProject = (row) => row.kind !== 'exam';
function programPosition(curriculum, rowId) {
  const rows = ((curriculum && curriculum.rows) || []).filter(isProject);
  const index = rows.findIndex((row) => row.id === rowId);
  if (index < 0) return null;
  const blocks = [...new Set(rows.map((row) => row.block))];
  const total = rows.length;
  const stage = index === 0 ? 'первый проект программы' : index === total - 1 ? 'последний проект программы'
    : index < total / 3 ? 'начало программы' : index < 2 * total / 3 ? 'середина программы' : 'конец программы';
  return {position: index + 1, total, stage, blockPosition: blocks.indexOf(rows[index].block) + 1, blocksTotal: blocks.length,
    previous: index ? rows[index - 1].title : '', next: index + 1 < total ? rows[index + 1].title : ''};
}
function positionText(p) {
  if (!p) return '';
  return `Проект ${p.position} из ${p.total} · ${p.stage} · блок ${p.blockPosition} из ${p.blocksTotal}`
    + (p.previous ? ` · до него: «${p.previous}»` : '') + (p.next ? ` · после: «${p.next}»` : '');
}
function budgetText(b) {
  if (!b) return '';
  if (!b.total_hours) return b.notes.join(' ') || 'Трудоёмкость не задана.';
  const parts = [`${num(b.total_hours)} ч: ${b.tasks_min}–${b.tasks_max} основных заданий (ориентир ${b.tasks_recommended}) по ${num(b.min_task_hours)}–${num(b.max_task_hours)} ч`,
    `на чтение, настройку, ошибки и сдачу — от ${Math.round(b.overhead_min_share * 100)}% времени`];
  if (b.needs_setup) parts.push(`настройка в первом задании — от ${num(b.first_task_setup_min)} ч`);
  return `${parts.join('; ')}.${b.notes.length ? ` ${b.notes.join(' ')}` : ''} Нормы черновые (config/workload.json).`;
}

/* ---------- intake form ---------- */
const form = () => $('#briefForm');
const field = (name) => form().elements.namedItem(name);

function renderOutcomes() {
  const box = $('#outcomeRows');
  box.replaceChildren();
  S.outcomes.forEach((text, i) => {
    const input = h('input', {value: text, 'aria-label': `Образовательный результат ${i + 1}`, placeholder: i ? '' : 'Что участник сможет сделать'});
    input.addEventListener('input', (e) => { S.outcomes[i] = e.target.value; saveIntake(); });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); S.outcomes.splice(i + 1, 0, ''); renderOutcomes(); focusOutcome(i + 1); }
      else if (e.key === 'Backspace' && !e.target.value && S.outcomes.length > 1) { e.preventDefault(); S.outcomes.splice(i, 1); renderOutcomes(); focusOutcome(Math.max(0, i - 1)); saveIntake(); }
    });
    input.addEventListener('paste', (e) => {
      const pasted = e.clipboardData.getData('text');
      if (!pasted.includes('\n')) return;
      e.preventDefault();
      const lines = pasted.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
      S.outcomes.splice(i, 1, ...(S.outcomes[i].trim() ? [S.outcomes[i], ...lines] : lines));
      renderOutcomes(); saveIntake();
    });
    const remove = S.outcomes.length > 1 ? h('button', {type: 'button', class: 'icon-button', 'aria-label': `Удалить результат ${i + 1}`,
      onClick: () => { S.outcomes.splice(i, 1); renderOutcomes(); saveIntake(); }}, icon('x')) : null;
    box.append(h('div', {class: 'outcome-row'}, h('span', {text: String(i + 1)}), input, remove));
  });
  box.append(h('button', {type: 'button', class: 'add-row', onClick: () => { S.outcomes.push(''); renderOutcomes(); focusOutcome(S.outcomes.length - 1); }}, icon('plus'), 'Добавить результат'));
}
const focusOutcome = (i) => { const inputs = $('#outcomeRows').querySelectorAll('input'); if (inputs[i]) inputs[i].focus(); };

function readBrief() {
  const brief = {direction: S.legacy.direction || '', program: S.legacy.program || ''};
  for (const name of TEXT_FIELDS) brief[name] = field(name).value.trim();
  brief.outcomes = S.outcomes.map((s) => s.trim()).filter(Boolean).join('\n');
  for (const name of ['format', 'start_level', 'storytelling_type', 'style_profile']) brief[name] = field(name).value;
  for (const name of NUMBER_FIELDS) { const value = field(name).value; brief[name] = value === '' ? null : Number(value); }
  if (brief.format !== 'group') brief.group_size = null;
  for (const name of FLAG_FIELDS) brief[name] = field(name).checked;
  const row = selectedRow();
  brief.block = row ? row.block : '';
  brief.collaboration_mode = field('collaboration_mode').value;
  brief.curriculum_policy = field('curriculum_policy').value;
  brief.generation = {variety: field('writing_variety').value};
  const sampler = field('sampling_mode').value;
  for (const name of ['temperature', 'top_p', 'frequency_penalty', 'presence_penalty']) {
    const raw = field(`writing_${name}`).value;
    if (raw !== '' && (!['temperature', 'top_p'].includes(name) || name === sampler)) brief.generation[name] = Number(raw);
  }
  return brief;
}
function fillBrief(brief) {
  if (brief.generation) {
    const g = brief.generation;
    field('writing_variety').value = g.variety || 'balanced';
    field('sampling_mode').value = g.temperature != null ? 'temperature' : g.top_p != null ? 'top_p' : 'default';
    for (const name of ['temperature', 'top_p', 'frequency_penalty', 'presence_penalty']) {
      field(`writing_${name}`).value = g[name] ?? (name === 'temperature' ? 0.8 : name === 'top_p' ? 0.95 : '');
    }
  }
  for (const [key, value] of Object.entries(brief)) {
    const el = field(key);
    if (!el || !el.tagName) continue;
    if (el.type === 'checkbox') el.checked = Boolean(value); else el.value = value === null || value === undefined ? '' : value;
  }
  // Older drafts kept software separately; the form now has one field for tools and software.
  if (brief.software) field('tools').value = joinUnique(field('tools').value, brief.software);
  if ('outcomes' in brief) { S.outcomes = outcomesOf(brief.outcomes); if (!S.outcomes.length) S.outcomes = ['']; }
  S.legacy = {direction: brief.direction || S.legacy.direction || '', program: brief.program || S.legacy.program || ''};
  renderOutcomes(); syncGroup(); syncSampling(); countMore();
}
function syncGroup() { $('#groupField').hidden = field('format').value !== 'group'; }
function syncSampling() {
  const mode = field('sampling_mode').value;
  field('writing_temperature').disabled = mode !== 'temperature';
  field('writing_top_p').disabled = mode !== 'top_p';
}
function countMore() {
  const filled = MORE_FIELDS.filter((name) => field(name).value.trim()).length;
  $('#moreCount').textContent = `заполнено ${filled} из ${MORE_FIELDS.length}`;
}
function saveIntake() {
  store.set('chat-draft', {brief: readBrief(), curriculum: S.curriculum, rowId: S.rowId, materials: S.materials});
}
function restoreIntake() {
  const saved = store.get('chat-draft', null);
  if (!saved) { renderOutcomes(); countMore(); return; }
  S.curriculum = saved.curriculum || null; S.rowId = saved.rowId || ''; S.materials = saved.materials || [];
  renderCurriculum(false); fillBrief(saved.brief || {}); renderMaterials(); refreshIntakeWorkload();
}
function clearIntake() {
  form().reset(); S.curriculum = null; S.planFile = null; S.rowId = ''; S.materials = []; S.outcomes = ['']; S.legacy = {direction: '', program: ''};
  $('#levelSource').hidden = true; renderCurriculum(false); renderMaterials(); renderOutcomes(); syncGroup(); syncSampling(); countMore();
  notify(''); $('#intakeWorkload').hidden = true; store.set('chat-draft', null);
}
function selectedRow() { return S.curriculum ? S.curriculum.rows.find((row) => row.id === S.rowId) : null; }
function setOptions(select, pairs) { select.replaceChildren(...pairs.map(([value, label, off]) => h('option', {value, text: label, disabled: Boolean(off)}))); }
function planCount(rows) {
  const exams = rows.filter((row) => !isProject(row)).length, projects = rows.length - exams;
  return `${projects} ${plural(projects, 'проект', 'проекта', 'проектов')}${exams ? ` и ${exams} ${plural(exams, 'экзамен', 'экзамена', 'экзаменов')}` : ''}`;
}
/* The projects of a block as options; its exams are shown in their place, but cannot be chosen. */
function projectOptions(rows, block) {
  return rows.filter((row) => row.block === block).map((row) => isProject(row) ? [row.id, row.title] : [row.id, `${row.title} — экзамен`, true]);
}
/* The plan's theses and its approved cases together say what the project consists of. */
function rowDescription(row) {
  return [row.description, row.cases ? `Кейсы проекта:\n${row.cases}` : ''].filter(Boolean).join('\n\n');
}

async function loadCurriculum(promise, file = null) {
  // The plan on the page is the one asked for last, whichever answer comes last.
  const turn = ++S.planTurn;
  S.uploads++; updateStart(); notify('');
  // The file is remembered only with the plan read from it: a refused file must not be what a later choice sends.
  try {
    const plan = await promise;
    if (turn === S.planTurn) { S.curriculum = plan; S.planFile = file; S.rowId = ''; renderCurriculum(true); saveIntake(); }
  } catch (error) { if (turn === S.planTurn) notify(error.message); }
  finally { S.uploads--; updateStart(); $('#curriculumFile').value = ''; }
}
/* A passport can hold several skills tabs for the same projects (the current one and an older one). Which one is
   right is the methodologist's to say: the workbook is sent again with the choice, and only the skills change — what
   has been typed into the form stays. The file is kept in memory only: after the page is reloaded it must be loaded again. */
function skillsTabChoice(c) {
  if (!c.skill_tabs || c.skill_tabs.length < 2) return null;
  const select = h('select', {'aria-label': 'Вкладка развесовки навыков', disabled: !S.planFile || Boolean(S.choosingTab)},
    [['', 'не выбрана'], ...c.skill_tabs.map((tab) => [tab, tab])].map(([value, label]) => h('option', {value, text: label})));
  select.value = c.skills_tab || '';
  select.addEventListener('change', () => chooseSkillsTab(select.value));
  return h('label', {class: 'skills-tab'}, h('span', {class: 'hint', text: 'Вкладка развесовки навыков:'}), select,
    S.planFile ? null : h('small', {class: 'hint', text: 'Чтобы выбрать вкладку, загрузите книгу снова.'}));
}
async function chooseSkillsTab(tab) {
  const file = S.planFile;
  // One choice at a time: two answers arriving in the other order would leave the page on the tab chosen first.
  if (!file || S.choosingTab) return;
  const data = new FormData(); data.append('file', file); data.append('skills_tab', tab);
  S.choosingTab = true; S.uploads++; updateStart(); notify('');
  renderCurriculum(false);   // the list is closed while the answer is on its way
  try {
    const plan = await api('api/curriculum', {method: 'POST', body: data});
    // The plan was removed or replaced while the answer was on its way: the answer is about a plan that is gone.
    if (S.planFile !== file) return;
    S.curriculum = plan;
    const row = selectedRow();
    // «Не выбрана» takes no skills from anywhere: what stands in the form is left alone.
    if (row && plan.skills_tab) { field('skills').value = row.skills || ''; if (row.bonus) field('bonus').checked = true; countMore(); }
    saveIntake();
  } catch (error) { if (S.planFile === file) notify(error.message); }
  finally {
    S.choosingTab = false; S.uploads--; updateStart();
    // Drawn again in every case: after a refusal the choice shows the tab the plan on the page was read from.
    renderCurriculum(false);
  }
}
function renderCurriculum(apply) {
  const c = S.curriculum;
  $('#curriculumCard').hidden = !c; $('#curriculumSelectors').hidden = !c; $('#curriculumDrop').hidden = Boolean(c);
  if (!c) { $('#positionLine').hidden = true; return; }
  const recognized = Object.keys(c.columns || {}).filter((key) => COLUMN_LABELS[key]).map((key) => COLUMN_LABELS[key]);
  const missing = c.missing_fields || [], unmapped = c.unmapped_columns || [];
  $('#curriculumCard').className = 'file-card';
  $('#curriculumCard').replaceChildren(
    h('div', {class: 'file-card-head'}, h('span', {class: 'dz-icon'}, icon('file')),
      h('div', {}, h('strong', {text: c.filename}), h('small', {class: 'hint', text: `${planCount(c.rows)} · контекст всей программы сохранится`})),
      h('button', {type: 'button', class: 'button small', text: 'Заменить', onClick: () => $('#curriculumFile').click()}),
      h('button', {type: 'button', class: 'icon-button', 'aria-label': 'Убрать учебный план', onClick: () => { S.curriculum = null; S.planFile = null; S.rowId = ''; renderCurriculum(false); saveIntake(); }}, icon('x'))),
    h('div', {class: 'column-report'},
      recognized.length ? h('div', {class: 'tags'}, h('span', {class: 'hint', text: 'Распознано:'}), recognized.map((label) => h('span', {class: 'tag ok', text: label}))) : null,
      missing.length ? h('div', {class: 'tags'}, h('span', {class: 'hint', text: 'Не найдено:'}), missing.map((text) => h('span', {class: 'tag warn', text}))) : null,
      unmapped.length ? h('div', {class: 'tags'}, h('span', {class: 'hint', text: 'Не распознаны (сохранены как исходные данные строки):'}), unmapped.map((text) => h('span', {class: 'tag grey', text}))) : null,
      skillsTabChoice(c),
      [...(c.warnings || []), ...(c.skill_notes || [])].map((text) => h('p', {class: 'hint', text}))));
  const projects = c.rows.filter(isProject);
  const blocks = [...new Set(projects.map((row) => row.block))];
  setOptions($('#blockSelect'), blocks.map((block) => [block, block]));
  const row = projects.find((r) => r.id === S.rowId) || projects[0];
  $('#blockSelect').value = row.block;
  showProjects(row.id, apply);
}
function showProjects(rowId, apply) {
  const options = projectOptions(S.curriculum.rows, $('#blockSelect').value);
  setOptions($('#projectSelect'), options);
  const open = options.filter(([, , off]) => !off).map(([id]) => id);
  // Set explicitly: a select left to itself may stay on a disabled first option.
  $('#projectSelect').value = open.includes(rowId) ? rowId : open[0] || '';
  S.rowId = $('#projectSelect').value;
  if (apply) applyRow();
  const position = programPosition(S.curriculum, S.rowId);
  $('#positionLine').hidden = !position;
  $('#positionLine').textContent = positionText(position);
}
function inferLevel(text) {
  // Only unambiguous words map to a level; anything else stays a question for the methodologist.
  if (/нулев|с нуля|без опыта/i.test(text)) return 'zero';
  if (/базов/i.test(text)) return 'basic';
  if (/средн/i.test(text)) return 'intermediate';
  if (/продвинут/i.test(text)) return 'advanced';
  return '';
}
function joinUnique(...values) {
  const seen = new Set();
  return values.flatMap((v) => String(v || '').split(/[,;\n]/)).map((v) => v.trim()).filter((v) => v && !seen.has(v.toLowerCase()) && seen.add(v.toLowerCase())).join(', ');
}
function applyRow() {
  const row = selectedRow();
  if (!row) return;
  const level = inferLevel(row.audience || '');
  // bonus: the skills tab of the passport gives the project a bonus part, so the plan expects bonus tasks. A plan read
  // without a skills tab knows nothing of a bonus — the box stays as the methodologist left it.
  const known = S.curriculum && S.curriculum.skills_tab ? {bonus: Boolean(row.bonus)} : {};
  fillBrief({title: row.title, description: rowDescription(row), outcomes: row.outcomes, skills: row.skills, ...known, audience: row.audience,
    start_level: level, start_skills: '', tools: joinUnique(row.tools, row.software), storytelling: row.storytelling,
    storytelling_type: row.storytelling_type || (row.storytelling ? 'sjm' : 'none'), format: /групп|group/i.test(row.format) ? 'group' : 'individual',
    group_size: Number(row.group_size) > 1 ? Number(row.group_size) : '', hours: Number(String(row.hours).replace(',', '.')) || '',
    references: row.materials, repository: row.repository || '', student_result: '', checking: row.checking || '', constraints: '', output_package: '', notes: '', tasks_count: ''});
  $('#levelSource').hidden = !row.audience;
  $('#levelSource').textContent = row.audience ? `В УП: «${row.audience}» — ${level ? 'проверьте выбор' : 'выберите уровень'}` : '';
  saveIntake(); refreshIntakeWorkload();
}
function renderMaterials() {
  $('#materialsList').replaceChildren(...S.materials.map((material) => {
    const role = h('select', {'aria-label': `Роль материала ${material.name}`}, Object.entries(ROLES).map(([value, label]) => h('option', {value, text: label})));
    role.value = material.role;
    role.addEventListener('change', () => { material.role = role.value; saveIntake(); });
    return h('div', {class: 'material'}, h('strong', {text: material.name}), role,
      h('button', {type: 'button', class: 'icon-button', 'aria-label': `Убрать ${material.name}`, onClick: () => { S.materials = S.materials.filter((m) => m !== material); renderMaterials(); saveIntake(); }}, icon('x')),
      h('small', {text: [READ[material.read_status] || 'охват не подтверждён', `${material.passages.length} фрагментов`, ...material.warnings].join(' · ')}));
  }));
}
async function uploadMaterials(files) {
  S.uploads++; updateStart(); notify('');
  for (const file of files) {
    if (S.materials.length >= 12) { notify('Можно прикрепить до 12 материалов.'); break; }
    const data = new FormData(); data.append('file', file);
    try { const material = await api('api/materials', {method: 'POST', body: data}); if (!S.materials.some((m) => m.id === material.id)) S.materials.push(material); }
    catch (error) { notify(`${file.name}: ${error.message}`); }
    renderMaterials();
  }
  S.uploads--; updateStart(); $('#materialFiles').value = ''; saveIntake();
}
function workloadQuery(source) {
  const hours = Number(source.hours), tasks = Number(source.tasks_count);
  return {hours: hours > 0 ? hours : null, tasks_count: tasks >= 1 && tasks <= 20 ? Math.round(tasks) : null,
    start_level: source.start_level || '', tools: source.tools || '', software: source.software || ''};
}
function refreshIntakeWorkload() {
  clearTimeout(S.workloadTimer);
  S.workloadTimer = setTimeout(async () => {
    const query = workloadQuery(readBrief());
    const hint = $('#intakeWorkload');
    if (!query.hours) { hint.hidden = true; return; }
    try { const budget = await post('api/workload', query); hint.textContent = budgetText(budget); hint.className = `hint workload-hint${budget.notes.length ? ' warn' : ''}`; hint.hidden = false; }
    catch { hint.hidden = true; }
  }, 300);
}
function updateStart() {
  const ready = !S.config || S.config.model_ready;
  $('#startButton').disabled = S.uploads > 0 || !ready;
  $('#startButton').title = ready ? '' : 'ИИ не подключён на сервере';
}
async function startRun(event) {
  event.preventDefault();
  if (S.uploads) return;
  const brief = readBrief();
  const problems = [];
  if (!brief.title) problems.push('Укажите название проекта.');
  if (brief.description.length < 10) problems.push('Опишите проект хотя бы одним предложением (от 10 символов).');
  if (brief.outcomes.length < 5) problems.push('Добавьте хотя бы один образовательный результат.');
  if (brief.format === 'group' && !brief.group_size) problems.push('Укажите размер группы.');
  if (problems.length) { notify(problems.join('\n')); return; }
  const request = {request_id: crypto.randomUUID(), brief, curriculum: S.curriculum, selected_row_id: S.curriculum ? S.rowId : '', materials: S.materials, parent_run_id: null};
  const button = $('#startButton');
  button.disabled = true; notify('');
  try {
    const result = await post('api/runs', request);
    const thread = await post('api/threads', {run_id: result.id});
    store.set('chat-draft', null);
    await openThread(thread.id, thread);
  } catch (error) { notify(error.message); }
  finally { updateStart(); }
}

/* ---------- conversation ---------- */
const latest = () => (S.thread ? S.runs[S.thread.runs[S.thread.runs.length - 1]] : null);
const lastEvent = (run, stage, status) => [...run.events].reverse().find((e) => e.stage === stage && (!status || e.status === status));
function isOpen(key, fallback) { return S.open.has(key) ? S.open.get(key) : fallback; }
function toggle(key, current) { S.open.set(key, !current); render(); }

function showIntake() {
  stopPolling(); S.thread = null; S.editing = null; S.fieldEdit = null;
  $('#app').classList.add('intake-mode'); $('#app').classList.remove('panel-open');
  $('#intake').hidden = false; $('#conversation').hidden = true; $('#panel').hidden = true;
  history.replaceState(null, '', location.pathname);
  renderRecent(); closeDrawers();
}
function showConversation() {
  $('#app').classList.remove('intake-mode');
  $('#intake').hidden = true; $('#conversation').hidden = false; $('#panel').hidden = false;
  closeDrawers();
}
async function openThread(id, preloaded) {
  stopPolling();
  S.thread = preloaded || await api(`api/threads/${encodeURIComponent(id)}`);
  S.editing = null; S.fieldEdit = null; S.open = new Map(); notifyConv('');
  history.replaceState(null, '', `${location.pathname}?thread=${encodeURIComponent(S.thread.id)}`);
  await refreshRuns(true);
  await Content.refresh();
  remember();
  showConversation();
  render(true);
  refreshBudget();
  schedulePoll();
}
async function refreshRuns(all) {
  const ids = S.thread.runs;
  await Promise.all(ids.map(async (id, index) => {
    const known = S.runs[id];
    if (!all && known && known.status !== 'running' && index < ids.length - 1) return;
    S.runs[id] = await api(`api/runs/${encodeURIComponent(id)}`);
  }));
}
function stopPolling() { clearTimeout(S.poll); S.poll = null; }
function schedulePoll() {
  stopPolling();
  const run = latest();
  if (run && run.status === 'running') S.poll = setTimeout(pollOnce, 2000);
  else if (Content.busy()) S.poll = setTimeout(pollContent, 1500);
}
async function pollContent() {
  try { await Content.poll(); } catch { /* transient network errors: keep polling */ }
  schedulePoll();
}
async function pollOnce() {
  const before = latest();
  const signature = (run) => (run ? `${run.status}:${run.events.length}` : '');
  const old = signature(before);
  try {
    await refreshRuns(false);
    // The next step depends on the run state, so the thread view is refreshed with it.
    if (signature(latest()) !== old) { S.thread = await api(`api/threads/${S.thread.id}`); render(); remember(); }
  }
  catch { /* transient network errors: keep polling */ }
  schedulePoll();
}

function timeline() {
  const items = [];
  S.thread.runs.forEach((id, index) => {
    const run = S.runs[id];
    if (!run) return;
    const v = index + 1;
    items.push({at: run.created_at, order: 0, node: () => inputsBubble(run, v)});
    // The place in the curriculum is shown once, right after the first inputs: it does not change between versions.
    if (index === 0 && S.thread.program_map) items.push({at: run.created_at, order: 0.5, node: () => programMapMessage(S.thread.program_map)});
    if (run.freshness) items.push({at: run.freshness.checked_at, order: 3.5, node: () => freshnessMessage(run)});
    if (run.input_card) items.push({at: (lastEvent(run, 'context', 'done') || run).at || run.created_at, order: 1, node: () => cardMessage(run, v)});
    if (run.concept) items.push({at: (lastEvent(run, 'design', 'done') || {}).at || run.created_at, order: 2, node: () => conceptMessage(run, v)});
    if (run.review) items.push({at: (lastEvent(run, 'review', 'done') || {}).at || run.created_at, order: 3, node: () => reviewMessage(run, v)});
    if (run.approved_at) items.push({at: run.approved_at, order: 4, node: () => h('div', {class: 'divider', text: `версия ${v} · замысел согласован`})});
    if (['failed', 'interrupted'].includes(run.status)) items.push({at: (run.events[run.events.length - 1] || run).at || run.created_at, order: 5, node: () => errorMessage(run, v, index === S.thread.runs.length - 1)});
  });
  for (const message of S.thread.messages) items.push({at: message.at, order: 1.5, node: () => messageNode(message)});
  items.push(...Content.items());
  items.sort((a, b) => (Date.parse(a.at) - Date.parse(b.at)) || (a.order - b.order));
  const run = latest();
  if (S.sending) {
    items.push({node: () => h('div', {class: 'bubble', text: S.pendingText})});
    items.push({node: () => h('div', {class: 'progress'}, h('span', {class: 'spinner', 'aria-hidden': 'true'}), S.pendingNote || 'Ассистент разбирает сообщение…')});
  }
  if (run && run.status === 'running') items.push({node: () => progressMessage(run)});
  // After approval the route continues with the content stage in the same chat.
  else if (run && !S.sending && S.content) items.push({node: () => Content.nextStep()});
  else if (run && !S.sending && S.thread.next_step) items.push({node: () => nextStepMessage(run, S.thread.next_step)});
  return items;
}
function assistant(meta, ...body) {
  return h('div', {class: 'assistant'}, h('span', {class: 'avatar', 'aria-hidden': 'true', text: '21'}),
    h('div', {class: 'assistant-body'}, h('div', {class: 'meta'}, h('b', {text: 'Ассистент методолога'}), ` · ${meta}`), ...body));
}
function inputsBubble(run, v) {
  const b = run.request.brief;
  if (v > 1) {
    const changed = (run.changes || []).filter((c) => c.location.startsWith('brief.')).map((c) => S.thread.fields[c.location.slice(6)] || c.location.slice(6));
    return h('div', {class: 'bubble'}, h('span', {class: 'cap', text: `Входные данные · версия ${v}`}), changed.length ? `Изменено: ${changed.join(', ')}` : 'Повторный разбор без изменений задания');
  }
  const chips = [b.block, `${outcomesOf(b.outcomes).length} ОР`, b.hours ? `${num(b.hours)} ч` : 'часы не заданы',
    b.format === 'group' ? `группа${b.group_size ? ` · ${b.group_size}` : ''}` : 'индивидуальный', `уровень: ${LEVEL_SHORT[b.start_level || '']}`,
    b.storytelling_type && b.storytelling_type !== 'none' ? STORY[b.storytelling_type] : null,
    b.style_profile ? `стиль: ${STYLE[b.style_profile].toLowerCase()}` : null,
    run.request.materials.length ? `материалов: ${run.request.materials.length}` : 'материалов нет'].filter(Boolean);
  return h('div', {class: 'bubble'}, h('span', {class: 'cap', text: 'Входные данные'}), h('span', {class: 'title', text: b.title}), h('div', {class: 'chips'}, chips.map((text) => h('span', {class: 'chip', text}))));
}
function kindTag(kind) { const [label, tone] = KIND[kind] || ['Неизвестно', 'grey']; return h('span', {class: `tag ${tone}`, text: label}); }
function questionsOf(run) {
  const original = ((run.input_attempts || [])[(run.input_attempts || []).length - 1] || {}).questions || [];
  const list = [];
  ((run.input_card && run.input_card.questions) || []).forEach((q, i) => list.push({...q, number: list.length + 1, stage: 'входы', important: Boolean(original[i] && original[i].blocking)}));
  ((run.concept && run.concept.questions) || []).forEach((q) => list.push({...q, number: list.length + 1, stage: 'замысел', important: false}));
  return list;
}
function answers(run) {
  const applied = new Set(), pending = new Set();
  for (const message of S.thread.messages) {
    if (message.run_id !== run.id) continue;
    const target = message.status === 'applied' ? applied : message.status === 'pending' ? pending : null;
    if (target) for (const patch of message.patches) for (const n of patch.question_numbers) target.add(n);
    if (message.status !== 'rejected') for (const answer of message.answers || []) (target || applied).add(answer.number);
  }
  return {applied, pending};
}
function questionsBlock(run, questions) {
  const {applied, pending} = answers(run);
  const current = run.id === latest().id;
  return h('div', {class: 'stack'}, h('div', {class: 'section-label', text: `Вопросы · ${questions.length}`}),
    questions.map((q) => h('div', {class: 'question'},
      h('div', {class: 'question-head'}, h('span', {class: 'num', text: String(q.number)}),
        h('div', {class: 'question-text'}, h('strong', {text: expandOutcomes(run, q.question)}), h('span', {text: expandOutcomes(run, q.why)})),
        q.important ? h('span', {class: 'tag warn', text: 'Важно'}) : null,
        applied.has(q.number) ? h('span', {class: 'tag ok', text: 'Отвечено'}) : pending.has(q.number) ? h('span', {class: 'tag info', text: 'Ответ ждёт решения'}) : null),
      current && !applied.has(q.number) ? h('div', {class: 'actions'}, h('button', {type: 'button', class: 'reply', text: 'Ответить', onClick: () => replyTo(q.number)})) : null)));
}
/* Вопросы по одному — над полем ввода.
   Методолог жаловался: выдаёт три вопроса, ответил на первый, и за вторым надо листать ленту
   вверх, за третьим ещё выше. Вопросы и так лежат готовым списком, модель их не переспрашивает,
   поэтому показать текущий рядом с полем — это только отрисовка.
   Список целиком остаётся в ленте: каждое сообщение чата стоит вызова модели, и ответить на
   три вопроса разом дешевле, чем по очереди. */
function unansweredQuestions(run) {
  const {applied, pending} = answers(run);
  return questionsOf(run).filter((q) => !applied.has(q.number) && !pending.has(q.number));
}
function askNowBlock(run) {
  const waiting = unansweredQuestions(run);
  if (!waiting.length) return null;
  const q = waiting[0];
  const total = questionsOf(run).length;
  return h('div', {class: 'ask-now-body'},
    h('div', {class: 'ask-now-head'}, h('span', {class: 'num', text: String(q.number)}),
      h('span', {class: 'section-label', text: `Вопрос ${q.number} из ${total}`}),
      q.important ? h('span', {class: 'tag warn', text: 'Важно'}) : null,
      waiting.length > 1 ? h('span', {class: 'hint', text: `ещё ${waiting.length - 1}`}) : null),
    h('strong', {class: 'ask-now-text', text: expandOutcomes(run, q.question)}),
    q.why ? h('span', {class: 'hint', text: expandOutcomes(run, q.why)}) : null,
    waiting.length > 1 ? h('details', {}, h('summary', {text: 'Все вопросы — можно ответить одним сообщением'}),
      waiting.slice(1).map((item) => h('p', {text: `${item.number}. ${expandOutcomes(run, item.question)}`}))) : null);
}
/* Номер вопроса в сообщении — чтобы ответ не сопоставлялся догадкой: свободный ответ сервер
   тоже разбирает, но привязывает его к вопросу сам и может ошибиться номером.
   Начатое сообщение не трогаем: человек уже пишет своё. */
function askNowPrefill(run, current) {
  if (current.trim()) return null;
  const waiting = unansweredQuestions(run);
  return waiting.length ? `Вопрос ${waiting[0].number}: ` : null;
}
function collapsible(key, title, hint, fallbackOpen, bodyNodes) {
  const open = isOpen(key, fallbackOpen);
  return h('div', {class: 'card'},
    h('button', {type: 'button', class: 'card-head', 'aria-expanded': String(open), onClick: () => toggle(key, open)}, h('span', {class: 'title', text: title}), hint ? h('span', {class: 'hint', text: hint}) : null, icon('chevron')),
    open ? bodyNodes() : null);
}
function offTopicBlock(run, current) {
  // What the interpreter found unrelated to the project. Nothing is removed until the methodologist says so in the chat.
  const items = (run.input_card && run.input_card.off_topic) || [];
  if (!items.length) return null;
  const place = (id) => { const e = (run.evidence || []).find((x) => x.id === id); return e ? `${e.source}, ${e.location}` : id; };
  return h('div', {class: 'card'}, h('div', {class: 'card-head'}, h('span', {class: 'title', text: `Похоже, не относится к проекту · ${items.length}`})),
    items.map((x) => h('div', {class: 'card-row finding'},
      h('div', {class: 'chips'}, h('span', {class: 'tag warn', text: 'шум?'}), h('span', {class: 'hint', text: place(x.evidence_id)})),
      h('strong', {text: `«${x.quote}»`}), h('p', {text: x.why}),
      current ? h('div', {class: 'actions'},
        h('button', {type: 'button', class: 'reply', text: 'Не учитывать', onClick: () => addToComposer(`Не учитывать «${x.quote}» (${place(x.evidence_id)}).`)}),
        h('button', {type: 'button', class: 'reply', text: 'Это нужно', onClick: () => addToComposer(`Фрагмент «${x.quote}» учитывать: `)})) : null)),
    h('div', {class: 'legend', text: 'Фрагмент файла, который вы согласились не учитывать, код убирает из всего, что читают модели, — и из контента тоже. Посторонний текст в полях задания убирается правкой поля.'}));
}
function cardMessage(run, v) {
  const card = run.input_card;
  const questions = questionsOf(run).filter((q) => q.stage === 'входы');
  const current = run.id === latest().id;
  return assistant(`разбор входов · версия ${v}`,
    h('p', {class: 'prose', text: expandOutcomes(run, card.summary)}),
    collapsible(`claims-${run.id}`, 'Как я понял входные данные', `${card.claims.length} ${plural(card.claims.length, 'утверждение', 'утверждения', 'утверждений')}`, current, () => [
      ...Object.entries(AREAS).map(([area, label]) => {
        const claims = card.claims.filter((c) => c.area === area);
        return h('div', {class: 'card-row area'}, h('div', {class: 'area-name', text: label}),
          h('div', {class: 'stack tight'}, claims.length ? claims.map((c) => h('div', {class: 'claim'}, kindTag(c.kind), h('span', {text: expandOutcomes(run, c.text)}))) : h('span', {class: 'hint', text: 'Нет утверждений'})));
      }),
      h('div', {class: 'legend', text: 'Задано — есть в форме или УП · Предложено — вариант ИИ, нужно согласие · Неизвестно — данных нет · Противоречие — источники расходятся'})]),
    run.input_findings && run.input_findings.length ? collapsible(`inputs-${run.id}`, `Замечания к входам · ${run.input_findings.length}`, 'проверка кодом', false,
      () => run.input_findings.map((f) => h('div', {class: 'card-row finding'}, h('strong', {text: expandOutcomes(run, f.message)}), f.suggestion ? h('p', {text: expandOutcomes(run, f.suggestion)}) : null))) : null,
    offTopicBlock(run, current),
    questions.length ? questionsBlock(run, questions) : h('p', {class: 'hint', text: 'Вопросов к входам нет.'}));
}
function detailsRows(pairs) {
  return pairs.filter(([, value]) => value && (!Array.isArray(value) || value.length)).map(([label, value]) =>
    h('div', {class: 'card-row'}, h('span', {class: 'section-label', text: label}), h('div', {class: 'task-sub', text: Array.isArray(value) ? value.map((item) => `• ${item}`).join('\n') : value})));
}
const percent = (share) => `${num(share * 100)}%`;
/* The skills of the project divided between the tasks, as code counted them (run.skill_block): for each task of the
   concept, the part of every skill it gets. Main tasks and bonus ones are counted apart, each in the order of the concept. */
function taskShares(run) {
  const block = run.skill_block, at = {main: 0, bonus: 0};
  return run.concept.tasks.map((task) => {
    const part = block && block[task.optional ? 'bonus' : 'main'];
    const line = part && part.tasks[at[task.optional ? 'bonus' : 'main']++];
    return line ? line.shares : {};
  });
}
/* The block as the passport's tab has it — a line for the project, a line for each task — in percents. A task is
   called as the page calls it, by its number in the concept and its title: the findings of code use the same numbers. */
function skillsTable(run) {
  const block = run.skill_block, shares = taskShares(run);
  const tasks = run.concept.tasks.map((task, i) => ({task, number: i + 1, shares: shares[i]}));
  const parts = [[block.main, false, 'Основная часть — доля навыка в проекте'], [block.bonus, true, 'Бонусная часть — доля навыка']]
    .map(([part, bonus, title]) => [part, tasks.filter((t) => Boolean(t.task.optional) === bonus), title])
    .filter(([part, own]) => own.length || Object.keys(part.shares).length);
  const names = [...new Set(parts.flatMap(([part, own]) => [...Object.keys(part.shares), ...own.flatMap((t) => Object.keys(t.shares))]))];
  const line = (label, given, strong) => h('tr', {class: strong ? 'total' : null}, h('th', {scope: 'row', text: label}), names.map((name) => h('td', {text: name in given ? percent(given[name]) : ''})));
  return h('div', {class: 'skills-table'},
    h('table', {}, h('thead', {}, h('tr', {}, h('th', {text: 'Задание'}), names.map((name) => h('th', {scope: 'col', text: name})))),
      h('tbody', {}, parts.map(([part, own, title]) => [line(title, part.shares, true), own.map((t) => line(`${t.number}. ${t.task.title}`, t.shares, false))]))),
    h('p', {class: 'hint', text: 'В строке задания — какая часть баллов навыка приходится на это задание; по каждому навыку строки заданий в сумме дают 100%. Доли посчитал код: из веса, который замысел дал навыку в задании, и часов задания.'}),
    h('p', {class: 'hint'}, h('a', {href: `api/runs/${run.id}/export/skills`, text: 'Скачать блок для паспорта (CSV)'}), ' — строки вкладки развесовки: вставляются в паспорт без пересчёта.'));
}
function taskRow(run, task, number, shares = {}) {
  const w = task.workload;
  const details = [['Действие', task.action], ['Как проверяет пир', task.peer_check], ['Что участник решает сам', task.independent_choice], ['Ориентиры и материалы', task.support]]
    .map(([label, value]) => [label, expandOutcomes(run, value)]);
  if (task.program_link) details.unshift(['Связь с программой', expandOutcomes(run, task.program_link)]);
  const goalTexts = new Map(((S.thread.program_map || {}).block_goal_items || []).map((g) => [g.number, g.text]));
  if ((task.block_goal_numbers || []).length) details.unshift(['Цели блока', task.block_goal_numbers.map((n) => `${n}. ${goalTexts.get(n) || ''}`).join('\n')]);
  const earned = Object.entries(shares);
  if (earned.length) details.push(['Навыки: часть баллов навыка за это задание', earned.map(([name, share]) => `${name} — ${percent(share)}`).join('\n')]);
  if (w) details.push(['Разбивка часов', `чтение ${num(w.reading)} · настройка ${num(w.setup)} · выполнение ${num(w.doing)} · ошибки и повторы ${num(w.debugging)} · сдача ${num(w.submission)}`]);
  return h('div', {class: 'card-row task'}, h('span', {class: 'num', text: String(number)}),
    h('div', {class: 'stack tight'}, h('div', {class: 'task-title', text: task.title}), h('div', {class: 'task-sub', text: expandOutcomes(run, task.deliverable)}),
      task.optional || earned.length ? h('div', {class: 'chips'}, task.optional ? h('span', {class: 'tag info', text: 'Бонус'}) : null,
        earned.map(([name]) => h('span', {class: 'tag grey', text: name}))) : null,
      h('ul', {class: 'or-list', 'aria-label': 'Образовательные результаты задания'}, task.outcome_numbers.map((n) => h('li', {text: outcomeName(run, n).replace(/^«|»$/g, '')}))),
      h('details', {}, h('summary', {text: 'Подробнее'}), h('dl', {}, details.map(([label, value]) => [h('dt', {text: label}), h('dd', {text: value})]))),
      h('div', {class: 'task-actions'}, h('button', {type: 'button', class: 'button small', text: 'Заменить или изменить', onClick: () => reviseTask(number, task.title)}))),
    h('span', {class: 'task-hours', text: `${num(task.hours)} ч`}));
}
function conceptMessage(run, v) {
  const c = run.concept, brief = run.request.brief;
  const main = c.tasks.filter((t) => !t.optional);
  const hours = main.reduce((sum, t) => sum + t.hours, 0);
  const required = outcomesOf(brief.outcomes).length;
  const covered = new Set(main.flatMap((t) => t.outcome_numbers));
  const missing = Array.from({length: required}, (_, i) => i + 1).filter((n) => !covered.has(n));
  const questions = questionsOf(run).filter((q) => q.stage === 'замысел');
  const budget = brief.hours ? `${num(hours)} из ${num(brief.hours)} ч` : `${num(hours)} ч`;
  const coverage = missing.length
    ? h('div', {class: 'status-line warn', text: `Не покрыты ОР: ${missing.map((n) => outcomeName(run, n)).join(', ')} · ${budget}`})
    : h('div', {class: 'status-line ok'}, icon('check'), `Все ОР (${required}) покрыты основными заданиями · ${budget}`);
  const mineGoals = ((run.input_card && run.input_card.block_goals) || []).filter((g) => g.owner === 'this').map((g) => g.number);
  const goalsCovered = new Set(main.flatMap((t) => t.block_goal_numbers || []));
  const goalsMissing = mineGoals.filter((n) => !goalsCovered.has(n));
  const goalLine = mineGoals.length ? (goalsMissing.length
    ? h('div', {class: 'status-line warn', text: `Не закрыты цели блока этого проекта: ${goalsMissing.map((n) => {
      const g = ((S.thread.program_map || {}).block_goal_items || []).find((x) => x.number === n);
      return g ? `${n}. «${g.text}»` : String(n);
    }).join(', ')}`})
    : h('div', {class: 'status-line ok'}, icon('check'), `Цели блока этого проекта (${mineGoals.length}) закрыты заданиями`)) : null;
  const shares = taskShares(run);
  return assistant(`замысел · версия ${v}`,
    h('p', {class: 'prose', text: expandOutcomes(run, c.student_result)}),
    h('div', {class: 'card'}, h('div', {class: 'card-head'}, h('span', {class: 'title', text: `Замысел · ${main.length} ${plural(main.length, 'задание', 'задания', 'заданий')} · ${num(hours)} ч`}),
      h('a', {href: `api/runs/${run.id}/export/md`, text: 'Скачать текст'})), c.tasks.map((task, i) => taskRow(run, task, i + 1, shares[i])), coverage, goalLine),
    run.skill_block ? collapsible(`skills-${run.id}`, 'Развесовка навыков по заданиям', 'как на вкладке паспорта', false, () => skillsTable(run)) : null,
    collapsible(`place-${run.id}`, 'Связь с блоком и программой, границы проекта', null, run.id === latest().id, () => detailsRows([
      ['Контекст программы', c.program_summary], ['Роль проекта', c.project_role], ['Предполагаемая подготовка', c.prerequisites],
      ['Где пригодится дальше', c.later_use], ['За пределами проекта', c.out_of_scope], ['Что подготовить для генерации', c.material_gaps]]
      .map(([label, value]) => [label, Array.isArray(value) ? value.map((v) => expandOutcomes(run, v)) : expandOutcomes(run, value)]))),
    collapsible(`decisions-${run.id}`, `Обоснования · ${c.decisions.length}`, null, false, () => c.decisions.map((d) =>
      h('div', {class: 'card-row'}, h('div', {class: 'chips'}, kindTag(d.kind), h('strong', {text: d.title})), h('div', {class: 'task-sub', text: expandOutcomes(run, d.explanation)})))),
    questions.length ? questionsBlock(run, questions) : null);
}
const locationLabel = (loc) => (loc.startsWith('tasks.') ? `задание ${loc.slice(6)}` : loc.startsWith('decisions.') ? `обоснование ${loc.slice(10)}` : loc === 'tasks' ? 'задания' : loc);
function reviewMessage(run, v) {
  const review = run.review;
  const done = lastEvent(run, 'review', 'done');
  const summary = expandOutcomes(run, review.summary.replace('В демонстрационном режиме они не блокируют согласование.', '').trim());
  return assistant(`проверка · версия ${v}${done && done.model ? ` · ${done.model}` : ''}`,
    h('p', {class: 'prose', text: summary}),
    review.findings.length ? h('div', {class: 'card'}, h('div', {class: 'card-head'}, h('span', {class: 'title', text: `Замечания · ${review.findings.length}`})),
      review.findings.map((f) => h('div', {class: 'card-row finding'},
        h('div', {class: 'chips'}, h('span', {class: f.origin === 'system' ? 'tag grey' : 'tag warn', text: f.origin === 'system' ? 'Проверка кодом' : 'Проверяющий ИИ'}),
          (f.concept_locations || []).length ? h('span', {class: 'hint', text: f.concept_locations.map(locationLabel).join(', ')}) : null),
        h('strong', {text: expandOutcomes(run, f.message)}), f.suggestion ? h('p', {text: expandOutcomes(run, f.suggestion)}) : null)))
      : h('p', {class: 'hint', text: 'Замечаний нет. Решение о согласовании за методологом.'}),
    (review.outcomes || []).length ? collapsible(`outcomes-${run.id}`, `Результаты → действие → сдача → P2P · ${review.outcomes.length}`, null, false, () => review.outcomes.map((o) =>
      h('div', {class: 'card-row'}, h('div', {class: 'chips'}, h('strong', {text: outcomeName(run, o.outcome_number).replace(/^«|»$/g, '')}), h('span', {class: o.status === 'confirmed' ? 'tag ok' : 'tag warn', text: CHECK[o.status] || o.status})),
        h('div', {class: 'task-sub', text: expandOutcomes(run, [o.explanation, o.gap ? `Пробел: ${o.gap}` : ''].filter(Boolean).join('\n'))})))) : null);
}
const OWNER = {previous: ['предыдущий', 'grey'], this: ['этот проект', 'ok'], later: ['следующий', 'info'], shared: ['сквозная', 'info'], unclear: ['не видно', 'warn']};
function learnerPath(learner, path, run) {
  // The participant's path by the plan: facts collected by code, the summary and the links written by the interpreter.
  if (!learner) return null;
  const rows = [];
  const hoursBefore = learner.before.reduce((sum, p) => sum + (p.hours || 0), 0);
  const examsBefore = learner.before.filter((p) => p.kind === 'exam').length, projectsBefore = learner.before.length - examsBefore;
  const counted = `${projectsBefore} ${plural(projectsBefore, 'проект', 'проекта', 'проектов')}${examsBefore ? ` и ${examsBefore} ${plural(examsBefore, 'экзамен', 'экзамена', 'экзаменов')}` : ''}`;
  const tools = learner.tools_before.map((t) => `${t.tool} · ${t.projects.length}`);
  rows.push(h('div', {class: 'card-row'}, h('span', {class: 'section-label', text: `Что участник прошёл до проекта · ${counted}${hoursBefore ? `, ${num(hoursBefore)} ч` : ''}`}),
    path ? h('p', {class: 'prose', text: expandOutcomes(run, path.before)}) : null,
    tools.length ? h('div', {class: 'link-line'}, 'Инструменты по УП (в скольких проектах): ', h('span', {class: 'chips'}, tools.map((t) => h('span', {class: 'tag grey', text: t})))) : null,
    learner.tools_here.length ? h('div', {class: 'link-line'}, 'Инструменты этого проекта: ', h('span', {class: 'chips'}, learner.tools_here.map((t) =>
      h('span', {class: `tag ${t.before ? 'ok' : 'warn'}`, text: t.before ? `${t.tool} · встречался по УП` : `${t.tool} · впервые по УП`})))) : null,
    learner.before.length ? h('details', {class: 'goals'}, h('summary', {text: examsBefore ? 'Проекты и экзамены до этого: ОР, навыки, инструменты по УП' : 'Проекты до этого: ОР, навыки, инструменты по УП'}),
      h('ol', {class: 'path-list'}, learner.before.map((p) => h('li', {},
        h('strong', {text: p.title}), p.kind === 'exam' ? h('span', {class: 'tag grey', text: 'экзамен'}) : null,
        h('span', {class: 'hint', text: [p.block, p.hours ? `${num(p.hours)} ч` : ''].filter(Boolean).join(' · ')}),
        p.outcomes.length ? h('ul', {}, p.outcomes.map((o) => h('li', {text: o}))) : null,
        h('span', {class: 'hint', text: [p.skills.length ? `Навыки: ${p.skills.join(', ')}` : '', p.tools.length ? `Инструменты: ${p.tools.join(', ')}` : ''].filter(Boolean).join(' · ')}))))) : null));
  if (path && path.builds_on.length) {
    rows.push(h('div', {class: 'card-row'}, h('span', {class: 'section-label', text: 'Опирается на прошлые проекты — заново не учим'}),
      h('ul', {class: 'plain-list'}, path.builds_on.map((x) => h('li', {text: expandOutcomes(run, x)})))));
  }
  if (path && path.next_needs.length) {
    rows.push(h('div', {class: 'card-row'}, h('span', {class: 'section-label', text: 'Что понадобится в следующих проектах — чему научить здесь'}),
      path.next_needs.map((n) => h('div', {class: 'reasoning'}, h('strong', {text: `«${n.project}»`}),
        h('div', {class: 'task-sub', text: `Там: ${n.needs}`}),
        h('div', {class: 'task-sub', text: `Здесь: ${expandOutcomes(run, n.teach_here)}`}),
        h('div', {class: 'hint', text: n.outcome_numbers.length ? `ОР: ${n.outcome_numbers.map((x) => outcomeName(run, x)).join(', ')}` : 'Ни один ОР этого проекта это не закрывает — решите, нужно ли'})))));
  }
  if (learner.tools_ahead.length) rows.push(h('div', {class: 'card-row'}, h('span', {class: 'hint', text: `Новые инструменты впереди: ${learner.tools_ahead.join('; ')}`})));
  if (path && run.id === latest().id) rows.push(h('div', {class: 'actions'}, h('button', {type: 'button', class: 'reply', text: 'Поправить путь участника', onClick: () => addToComposer('Путь участника: ')})));
  return rows;
}
function goalPlacement(goals, placed, run) {
  // Block goals are written for the whole block: which project closes each one is the interpreter's proposal.
  const mine = goals.filter((g) => (placed.get(g.number) || {}).owner === 'this').length;
  return h('div', {class: 'card-row'}, h('span', {class: 'section-label', text: `Цели блока — кто их закрывает · этому проекту ${mine} из ${goals.length}`}),
    h('ol', {class: 'goal-list'}, goals.map((g) => {
      const p = placed.get(g.number);
      const [label, tone] = p ? OWNER[p.owner] : ['не разложена', 'warn'];
      return h('li', {class: p && p.owner === 'this' ? 'mine' : ''}, h('span', {class: 'num', text: String(g.number)}),
        h('div', {class: 'stack tight'}, h('span', {text: g.text}), p ? h('span', {class: 'hint', text: [p.project ? `«${p.project}»` : '', p.why].filter(Boolean).join(' · ')}) : null),
        h('span', {class: `tag ${tone}`, text: label}));
    })),
    run.id === latest().id ? h('div', {class: 'actions'}, h('button', {type: 'button', class: 'reply', text: 'Поправить раскладку', onClick: () => addToComposer('Цели блока: ')})) : null);
}
function mineGoals(goals, placed, run) {
  // The goals this project closes stay open; the placement of the others is only for checking, so it is folded.
  const mine = goals.filter((g) => (placed.get(g.number) || {}).owner === 'this');
  return h('div', {class: 'card-row'}, h('span', {class: 'section-label', text: `Цели блока, которые закрывает этот проект · ${mine.length} из ${goals.length}`}),
    mine.length ? h('ol', {class: 'goal-list'}, mine.map((g) => h('li', {class: 'mine'}, h('span', {class: 'num', text: String(g.number)}), h('span', {text: g.text}), h('span', {class: 'tag ok', text: 'этот проект'}))))
      : h('p', {class: 'hint', text: 'Ни одной цели блока этому проекту не отдано — проверьте раскладку.'}),
    h('details', {class: 'goals'}, h('summary', {text: 'Раскладка всех целей блока по проектам'}), goalPlacement(goals, placed, run)));
}
function programMapMessage(m) {
  const where = `Проект ${m.position_in_block} из ${m.block_projects.length} в блоке · блок ${m.block_position} из ${m.blocks_total} · ${m.position} из ${m.total} в учебном плане`
    + (m.program ? ` «${m.program}»` : '');
  const neighbour = (label, p) => p ? h('div', {class: 'link-line'}, `${label}: `, h('b', {text: `«${p.title}»`}), p.block !== m.block ? ` · ${p.block}` : ' · тот же блок') : null;
  const skills = [];
  if (m.skills_practised_before.length) skills.push(['Уже встречались раньше', m.skills_practised_before.join(', ')]);
  if (m.skills_new.length) skills.push(['Впервые в программе', m.skills_new.join(', ')]);
  if (m.skills_used_later.length) skills.push(['Понадобятся дальше', m.skills_used_later.map((x) => `${x.skill} — ${x.count} ${plural(x.count, 'проект', 'проекта', 'проектов')}, ближайший «${x.projects[0]}»`).join('\n')]);
  const run = S.runs[S.thread.runs[S.thread.runs.length - 1]];
  const card = run && run.input_card;
  const claims = card ? card.claims.filter((c) => c.area === 'program') : [];
  const placed = new Map(((card && card.block_goals) || []).map((g) => [g.number, g]));
  const goals = m.block_goal_items || [];
  return assistant('место в учебном плане',
    h('p', {class: 'prose', text: where}),
    h('div', {class: 'card'},
      h('div', {class: 'card-head'}, h('span', {class: 'title', text: m.block}), m.block_hours ? h('span', {class: 'hint', text: `${num(m.block_hours)} ч в блоке`}) : null),
      h('div', {class: 'card-row'}, h('ol', {class: 'block-list'}, m.block_projects.map((p, i) => h('li', {class: p.current ? 'current' : ''},
        h('span', {class: 'num', text: String(i + 1)}), h('span', {text: p.title}), h('span', {class: 'hours', text: p.current ? 'этот проект' : p.hours ? `${num(p.hours)} ч` : ''}))))),
      h('div', {class: 'card-row'}, neighbour('До него', m.previous_project), neighbour('После него', m.next_project),
        m.hours_before !== null && m.program_hours ? h('div', {class: 'link-line', text: `До этого проекта в плане ${num(m.hours_before)} ч из ${num(m.program_hours)} ч`}) : null),
      learnerPath(m.learner, card && card.learner_path, run),
      skills.length ? h('div', {class: 'card-row'}, h('details', {class: 'goals'}, h('summary', {text: 'Навыки из колонки УП'}), detailsRows(skills.map(([label, text]) => [label, text])))) : null,
      goals.length && placed.size ? mineGoals(goals, placed, run)
        : m.block_goals ? h('div', {class: 'card-row'}, h('details', {class: 'goals'}, h('summary', {text: 'Цели блока (на все его проекты)'}), h('p', {text: m.block_goals}))) : null,
      card && !card.learner_path && (card.program_reasoning || []).length ? h('div', {class: 'card-row'}, h('span', {class: 'section-label', text: 'Как я учитываю место в программе — проверьте'}),
        card.program_reasoning.map((p) => h('div', {class: 'reasoning'}, h('div', {class: 'chips'}, kindTag(p.kind), h('span', {text: p.fact})),
          h('div', {class: 'task-sub', text: `→ ${expandOutcomes(run, p.so_what)}`})))) : claims.length && !(card && card.learner_path) ? h('div', {class: 'card-row'}, h('span', {class: 'section-label', text: 'Как ИИ понял связь — проверьте'}),
        claims.map((c) => h('div', {class: 'claim'}, kindTag(c.kind), h('span', {text: expandOutcomes(run, c.text)})))) : null,
      card && (card.doubtful_skills || []).length ? h('div', {class: 'card-row finding'}, h('span', {class: 'section-label', text: 'Похоже на ошибку в УП — навыки не по теме'}),
        card.doubtful_skills.map((d) => h('div', {class: 'claim'}, h('span', {class: 'tag warn', text: d.skill}), h('span', {text: d.why}))),
        run.id === latest().id ? h('div', {class: 'actions'}, h('button', {type: 'button', class: 'reply', text: 'Ответить', onClick: () => addToComposer('Навыки из УП: ')})) : null) : null,
      h('div', {class: 'legend', text: 'Проекты, часы, ОР, навыки и инструменты собраны кодом из учебного плана; выводы о пути участника — ИИ, проверьте. Порядок плана — не доказательство, что навык освоен.'})));
}
function prefill(text) { addToComposer(text); }
function askForEdits(hint) {
  const box = $('#composerText');
  box.placeholder = hint; box.focus();
  box.addEventListener('blur', () => { if (!box.value) box.placeholder = 'Ответьте на вопрос или попросите изменить задание…'; }, {once: true});
}
function chip(label, onClick, kind = '') {
  return h('button', {type: 'button', class: `chip-action${kind ? ` ${kind}` : ''}`, text: label, disabled: S.sending, onClick});
}
function nextStepMessage(run, step) {
  // Suggestions, as in a chat assistant: the stage action first, then shortcuts. A click is a reply in the dialogue.
  const chips = [];
  if (step.action) chips.push(chip(step.button, () => advance(step.action, step.button), 'primary'));
  // The other path at the inputs stage: tell what to change; the assistant turns it into edits of the brief.
  if (step.edit_hint) chips.push(chip('Изменить или дополнить требования', () => askForEdits(step.edit_hint)));
  if (run.status === 'input_review' && !S.thread.draft_changes.length) {
    const {applied} = answers(run);
    questionsOf(run).filter((q) => q.stage === 'входы' && !applied.has(q.number)).slice(0, 3)
      .forEach((q) => chips.push(chip(`Ответить на вопрос ${q.number}`, () => replyTo(q.number))));
  }
  if (run.concept && run.status === 'review') chips.push(chip('Изменить задание…', () => prefill('Задание ')));
  if (run.concept && ['review', 'approved'].includes(run.status)) {
    const label = run.freshness ? 'Проверить актуальность ещё раз' : 'Проверить актуальность в интернете';
    chips.push(chip(label, () => checkFreshness(label)));
  }
  if (run.concept) chips.push(h('a', {class: 'chip-action ghost', href: `api/runs/${run.id}/export/md`, text: 'Скачать замысел'}));
  const checks = step.checks || [];
  return assistant('что дальше',
    h('p', {class: 'prose'}, h('b', {text: `${step.title}. `}), step.text),
    checks.length ? h('div', {class: 'stack tight'}, h('span', {class: 'section-label', text: 'Что проверить в первую очередь'}),
      checks.map((c) => h('div', {class: 'claim'}, kindTag(c.kind), h('span', {text: `${c.area}: ${expandOutcomes(run, c.text)}`})))) : null,
    chips.length ? h('div', {class: 'suggestions', role: 'group', 'aria-label': 'Что можно сделать дальше'}, chips) : null,
    h('small', {class: 'route', text: step.route}));
}
function sourceLinks(urls) {
  if (!urls.length) return null;
  return h('div', {class: 'sources'}, urls.map((url) => {
    let host = url;
    try { host = new URL(url).hostname.replace(/^www\./, ''); } catch { /* keep the raw url */ }
    return h('a', {href: url, target: '_blank', rel: 'noopener noreferrer', text: host, title: url});
  }));
}
function freshnessMessage(run) {
  const f = run.freshness;
  const attention = f.items.filter((x) => x.status !== 'current').length;
  return assistant(`актуальность · поиск в интернете · ${f.model}`,
    h('p', {class: 'prose', text: f.summary}),
    f.items.length ? h('div', {class: 'card'}, h('div', {class: 'card-head'}, h('span', {class: 'title', text: `Что проверили · ${f.items.length}`}),
      h('span', {class: 'hint', text: attention ? `требуют внимания: ${attention}` : 'всё актуально'})),
      f.items.map((x, i) => {
        const [label, tone] = FRESH[x.status] || FRESH.unclear;
        return h('div', {class: 'card-row finding'}, h('div', {class: 'chips'}, h('span', {class: `tag ${tone}`, text: label}), x.where ? h('span', {class: 'hint', text: x.where}) : null),
          h('strong', {text: `${i + 1}. ${x.topic}`}), h('p', {text: x.finding}), x.suggestion ? h('p', {text: `Что сделать: ${x.suggestion}`}) : null,
          sourceLinks(x.sources) || h('p', {class: 'hint', text: 'Источник не подтверждён'}));
      })) : null,
    f.ideas.length ? h('div', {class: 'card'}, h('div', {class: 'card-head'}, h('span', {class: 'title', text: 'Актуальные примеры для кейса'})),
      f.ideas.map((x) => h('div', {class: 'card-row finding'}, h('strong', {text: x.idea}), x.why ? h('p', {text: x.why}) : null, sourceLinks(x.sources)))) : null,
    unreachableBlock(f),
    h('p', {class: 'hint', text: 'Задание от этого не меняется. Чтобы учесть пункт, напишите в чат, например: «учти пункт 2 проверки актуальности».'
      + (f.dropped_references ? ` Ссылок без подтверждения поиском отброшено: ${f.dropped_references}.` : '')}));
}
/* Ссылки, которые код не смог открыть. Не выбрасываем: отчёт рекомендательный, а выброшенная
   ссылка унесла бы с собой и основание вывода. Проверка идёт с сервера, и об этом сказано:
   страница, закрытая в корпоративной сети методолога, отсюда может открыться, поэтому пустой
   список — не обещание, что откроется у всех. */
function unreachableBlock(f) {
  const links = f.unreachable || [];
  if (!links.length) return null;
  return h('div', {class: 'card'},
    h('div', {class: 'card-head'}, h('span', {class: 'title', text: `Не открылись · ${links.length}`}),
      h('span', {class: 'hint', text: 'проверено с сервера'})),
    links.map((url) => h('div', {class: 'card-row'}, h('a', {href: url, target: '_blank', rel: 'noopener noreferrer', text: url}))),
    h('div', {class: 'legend', text: 'Код открыл остальные ссылки, эти — нет. Проверка идёт с сервера: страница, закрытая в вашей сети, отсюда может открыться, поэтому пустой список не обещает, что откроется у всех.'}));
}
function progressMessage(run) {
  const event = [...run.events].reverse().find((e) => e.status === 'running');
  return assistant('работаю', h('div', {class: 'progress'}, h('span', {class: 'spinner', 'aria-hidden': 'true'}), h('span', {text: event ? event.message : 'Готовлю…'})),
    h('div', {}, h('button', {type: 'button', class: 'button small danger', text: 'Остановить', onClick: stopRun})));
}
function errorMessage(run, v, current) {
  return h('div', {class: 'card error-card'}, h('div', {class: 'card-head'}, h('span', {class: 'title', text: run.status === 'interrupted' ? `Версия ${v} остановлена` : `Версия ${v} не завершена`})),
    h('div', {class: 'card-row'}, h('span', {text: run.error || 'Подготовка прервана.'})),
    current ? h('div', {class: 'card-foot'}, h('span', {class: 'hint', text: '«Продолжить» — в подсказках ниже. Готовые этапы не повторяются.'})) : null);
}
// How the chat understood the message: shown so a misread question or request is visible at once. Both chats use it;
// its keys are the server's MessageKind (tests/test_js.py).
const KIND_LABEL = {request: 'просьба изменить', answer: 'ответ на вопрос', suggestion: 'совет', question: 'вопрос', off_topic: 'не по теме'};
function messageNode(message) {
  if (message.role === 'methodologist') return h('div', {class: 'bubble', text: message.text});
  if (message.role === 'system') {
    return message.patches.length
      ? h('div', {class: 'system-note'}, icon('pencil'), h('span', {text: `${message.text} · ${time(message.at)}`}))
      : h('div', {class: 'divider', text: message.text});
  }
  const understood = KIND_LABEL[message.kind];
  return assistant(understood ? `понял как: ${understood}` : 'правки задания', h('p', {class: 'prose', text: expandOutcomes(S.runs[message.run_id] || latest(), message.text)}), message.patches.length ? patchCard(message) : null,
    message.notes.length ? h('ul', {class: 'note-list'}, message.notes.map((text) => h('li', {text}))) : null);
}
function originLabel(patch) {
  if (patch.origin === 'answer') return patch.question_numbers.length ? `Ответ на вопрос ${patch.question_numbers.join(', ')}` : 'Ответ на вопрос';
  return {request: 'Просьба методолога', proposal: 'Предложение ИИ', manual: 'Вручную'}[patch.origin] || patch.origin;
}
function editorFor(key, value, id) {
  if (FLAG_FIELDS.has(key)) {
    const select = h('select', {id, 'data-field': key}, h('option', {value: 'true', text: 'Да'}), h('option', {value: 'false', text: 'Нет'}));
    select.value = value === true ? 'true' : 'false';
    return select;
  }
  const choices = key === 'start_level' ? LEVELS : key === 'format' ? {individual: 'Индивидуальный', group: 'Групповой'} : key === 'storytelling_type' ? STORY : null;
  if (choices) {
    const select = h('select', {id, 'data-field': key}, Object.entries(choices).map(([v, label]) => h('option', {value: v, text: label})));
    select.value = value || (key === 'format' ? 'individual' : key === 'storytelling_type' ? 'none' : '');
    return select;
  }
  if (NUMBER_FIELDS.has(key)) return h('input', {id, 'data-field': key, type: 'number', min: key === 'hours' ? '0.5' : '1', step: key === 'hours' ? '0.5' : '1', value: isEmpty(value) ? '' : String(value)});
  return h('textarea', {id, 'data-field': key, class: 'patch-edit', rows: String(Math.min(8, Math.max(2, String(value || '').split('\n').length + 1))), value: isEmpty(value) ? '' : String(value)});
}
function parseValue(key, raw) {
  if (FLAG_FIELDS.has(key)) return raw === 'true';
  if (NUMBER_FIELDS.has(key)) return raw === '' ? null : Number(raw);
  return raw;
}
function patchCard(message) {
  const editing = S.editing === message.id && message.status === 'pending';
  const [statusText, tone] = {pending: ['Ожидает решения', 'warn'], applied: ['Применено', 'ok'], rejected: ['Отклонено', 'grey']}[message.status] || ['', 'grey'];
  const rows = message.patches.map((patch, i) => h('div', {class: 'card-row'},
    h('div', {class: 'patch-head'}, h('span', {class: 'label', text: patch.label}), h('code', {text: patch.field}), h('span', {class: 'grow'}),
      h('span', {class: `tag ${patch.origin === 'proposal' ? 'info' : 'ok'}`, text: originLabel(patch)}), patch.edited ? h('span', {class: 'tag grey', text: 'исправлено методологом'}) : null),
    isEmpty(patch.before) ? h('div', {class: 'was-empty', text: 'было: не указано'}) : h('del', {class: 'diff del'}, h('span', {'aria-hidden': 'true', text: '−'}), h('span', {text: fmt(patch.field, patch.before)})),
    editing ? editorFor(patch.field, patch.after, `patch-${message.id}-${i}`)
      : h('ins', {class: 'diff ins'}, h('span', {'aria-hidden': 'true', text: '+'}), h('span', {text: isEmpty(patch.after) ? 'не указано' : fmt(patch.field, patch.after)})),
    patch.reason ? h('div', {class: 'reason', text: patch.reason}) : null));
  const count = message.patches.length;
  let foot;
  if (editing) {
    foot = h('div', {class: 'card-foot'}, h('button', {type: 'button', class: 'button primary small', text: 'Применить с исправлениями', onClick: () => decide(message, 'apply', collectEdits(message))}),
      h('button', {type: 'button', class: 'button small', text: 'Отмена', onClick: () => { S.editing = null; render(); }}));
  } else if (message.status === 'pending') {
    foot = h('div', {class: 'card-foot'}, h('button', {type: 'button', class: 'button primary small', text: `Применить ${count} ${plural(count, 'правку', 'правки', 'правок')}`, onClick: () => decide(message, 'apply')}),
      h('button', {type: 'button', class: 'button small', text: 'Изменить', onClick: () => { S.editing = message.id; render(); }}),
      h('button', {type: 'button', class: 'button small ghost', text: 'Отклонить', onClick: () => decide(message, 'reject')}));
  } else if (message.status === 'applied') {
    foot = h('div', {class: 'card-foot ok'}, icon('check'), h('span', {class: 'grow', text: 'Задание обновлено. Дальше — подсказки внизу чата или напишите «дальше».'}),
      h('button', {type: 'button', class: 'link-button', text: 'Отменить', onClick: () => decide(message, 'revert')}));
  } else {
    foot = h('div', {class: 'card-foot'}, h('span', {class: 'grow hint', text: 'Правки отклонены — задание не изменилось.'}),
      h('button', {type: 'button', class: 'link-button', text: 'Вернуть', onClick: () => decide(message, 'revert')}));
  }
  return h('div', {class: 'card'}, h('div', {class: 'card-head'}, h('span', {class: 'title', text: `Правки задания · ${count}`}), statusText ? h('span', {class: `tag ${tone}`, text: statusText}) : null), rows, foot);
}
function collectEdits(message) {
  const values = {};
  message.patches.forEach((patch, i) => {
    const el = document.getElementById(`patch-${message.id}-${i}`);
    if (!el) return;
    const value = parseValue(patch.field, el.value);
    if (JSON.stringify(value) !== JSON.stringify(patch.after)) values[i] = value;
  });
  return values;
}
function addToComposer(prefix) {
  // Several answers can go in one message: each template starts its own line after what is already written.
  const box = $('#composerText');
  const label = prefix.trim();
  let at = box.value.indexOf(label);
  if (at < 0) {
    const current = box.value.replace(/\s+$/, '');
    box.value = current ? `${current}\n${prefix}` : prefix;
    at = box.value.indexOf(label, current.length);
  }
  const lineEnd = box.value.indexOf('\n', at);
  const cursor = lineEnd < 0 ? box.value.length : lineEnd;
  box.focus(); box.setSelectionRange(cursor, cursor); autoGrow();
}
function replyTo(number) { addToComposer(`Вопрос ${number}: `); }
function reviseTask(number, title) {
  // The verdict lands in concept_feedback, which the designer reads in the next version; the chat itself is not passed on.
  addToComposer(`Задание ${number} «${title}»: `);
}
function autoGrow() { const text = $('#composerText'); text.style.height = 'auto'; text.style.height = `${Math.min(text.scrollHeight, 240)}px`; }
function renderHeader(run) {
  $('#convTitle').textContent = S.thread.draft.title;
  $('#convSub').textContent = [S.thread.draft.block, run.request.curriculum ? run.request.curriculum.filename : 'без учебного плана'].filter(Boolean).join(' · ');
  const runningEvent = run.status === 'running' ? [...run.events].reverse().find((e) => e.status === 'running') : null;
  const stage = runningEvent ? runningEvent.stage : null;
  let steps = [['Входы', Boolean(run.input_card)], ['Замысел', Boolean(run.concept)], ['Проверка', Boolean(run.review)], ['Согласование', run.status === 'approved']];
  let current = stage === 'context' || stage === 'inputs_approval' ? 0 : stage === 'design' ? 1 : stage === 'review' ? 2
    : run.status === 'input_review' ? 0 : run.status === 'review' ? 3 : run.status === 'approved' ? -1 : steps.findIndex(([, done]) => !done);
  const content = Content.steps();
  if (content) ({list: steps, current} = content);
  $('#stepper').replaceChildren(...steps.map(([label, done], i) => h('li', {class: i === current ? 'current' : done ? 'done' : '', 'aria-current': i === current ? 'step' : null},
    done && i !== current ? icon('check') : h('b', {text: `0${i + 1}`}), h('span', {text: label}))));
}
function render(toBottom = false) {
  const run = latest();
  if (!run) return;
  const box = $('#messages');
  const previous = box.scrollTop;
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  renderHeader(run);
  $('#messageColumn').replaceChildren(...timeline().map((item) => item.node()));
  renderPanel(); renderRecent(); renderAskNow(run); updateComposer();
  box.scrollTop = toBottom || nearBottom ? box.scrollHeight : previous;
}
function renderAskNow(run) {
  const place = $('#askNow');
  if (!place) return;
  // В чате контента свои вопросы к тексту; вопросы разбора входов туда не относятся.
  const node = Content.active() ? null : askNowBlock(run);
  place.hidden = !node;
  place.replaceChildren(...(node ? [node] : []));
  if (!node) return;
  const box = $('#composerText');
  const prefill = askNowPrefill(run, box.value);
  if (prefill) { box.value = prefill; autoGrow(); }
}
function updateComposer() {
  const ready = !S.config || S.config.model_ready;
  $('#sendButton').disabled = S.sending || !ready;
  $('#composerText').disabled = S.sending;
  const box = $('#composerText');
  if (!box.value && document.activeElement !== box) {
    box.placeholder = Content.active() ? 'Что поправить в тексте? Или «пойдёт» / «дальше»…' : 'Ответьте на вопрос или попросите изменить задание…';
  }
  $('#composerNote').textContent = Content.active()
    ? 'Просьбы становятся правками текста: вы применяете их на карточке. Согласованное ИИ не переписывает; README и чек-лист собирает код.'
    : 'Ответы и просьбы становятся правками задания, замысел строится только по нему. Задание целиком — кнопка «Задание» вверху.';
}
async function send(event) {
  event.preventDefault();
  const input = $('#composerText');
  const text = input.value.trim();
  if (!text || S.sending || !S.thread) return;
  S.sending = true; S.pendingText = text; input.value = ''; autoGrow(); notifyConv('');
  render(true);
  try {
    if (Content.active()) await Content.send(text);
    else {
      S.thread = await post(`api/threads/${S.thread.id}/messages`, {text});
      // «Дальше» may have started a new stage or version: pick it up and follow it.
      await refreshRuns(false); await Content.refresh(); remember();
    }
    schedulePoll();
  } catch (error) { notifyConv(error.message); input.value = text; autoGrow(); }
  finally { S.sending = false; S.pendingText = ''; render(true); input.focus(); }
}
async function act(action) {
  notifyConv('');
  try { await action(); } catch (error) { notifyConv(error.message); render(); }
}
function decide(message, verdict, values) {
  return act(async () => {
    S.thread = await post(`api/threads/${S.thread.id}/messages/${message.id}/decision`, {verdict, values: values || {}});
    S.editing = null; render(); refreshBudget();
  });
}
function waiting(label, note) { S.sending = true; S.pendingText = label; S.pendingNote = note; render(true); }
function done() { S.sending = false; S.pendingText = ''; S.pendingNote = ''; }
function advance(action, label) {
  // Shown at once as the methodologist's reply; the server stores the same words in the history.
  waiting(label, 'Запускаю…');
  return act(async () => {
    try {
      S.thread = await post(`api/threads/${S.thread.id}/advance`, {action});
      await refreshRuns(false);
      await Content.refresh();
    } finally { done(); }
    render(true); remember(); schedulePoll(); refreshBudget();
  }).finally(() => { if (S.sending) { done(); render(); } });
}
function checkFreshness(label) {
  const run = latest();
  waiting(label, 'Ищу в интернете: сервисы, тарифы, версии, свежие примеры…');
  return act(async () => {
    try { S.runs[run.id] = await post(`api/runs/${run.id}/freshness`); } finally { done(); }
    S.thread = await api(`api/threads/${S.thread.id}`); render(true);
  }).finally(() => { if (S.sending) { done(); render(); } });
}
const stopRun = () => act(async () => { const run = latest(); stopPolling(); S.runs[run.id] = await post(`api/runs/${run.id}/cancel`); render(true); remember(); });

/* ---------- brief panel ---------- */
async function refreshBudget() {
  if (!S.thread) return;
  try { S.budget = await post('api/workload', workloadQuery(S.thread.draft)); } catch { S.budget = null; }
  renderPanel();
}
function fieldNode(key) {
  const t = S.thread, value = t.draft[key], label = t.fields[key];
  const pending = t.pending_fields.includes(key), changed = t.draft_changes.includes(key);
  const editing = S.fieldEdit === key;
  let content;
  if (editing) {
    const editor = h('form', {}, editorFor(key, value, `field-${key}`),
      h('div', {class: 'row'}, h('button', {type: 'button', class: 'button small', text: 'Отмена', onClick: () => { S.fieldEdit = null; renderPanel(); }}),
        h('button', {type: 'submit', class: 'button primary small', text: 'Сохранить'})));
    editor.addEventListener('submit', (e) => { e.preventDefault(); saveField(key); });
    content = editor;
  } else if (isEmpty(value)) {
    content = h('div', {class: 'empty', text: key === 'start_level' ? 'не указан — ИИ спросит' : 'не указано'});
  } else {
    content = h('div', {class: 'value', text: key === 'outcomes' ? outcomesOf(value).map((o, i) => `${i + 1}. ${o}`).join('\n') : fmt(key, value)});
  }
  return h('div', {class: `brief-field${pending ? ' pending' : changed ? ' changed' : ''}`},
    h('div', {class: 'brief-field-head'}, h('span', {class: 'label', text: label}),
      pending ? h('span', {class: 'tag warn', text: 'ожидает правки'}) : changed ? h('span', {class: 'tag ok', text: 'изменено в черновике'}) : null,
      h('span', {class: 'grow'}),
      editing ? null : h('button', {type: 'button', class: 'icon-button edit', 'aria-label': `Изменить: ${label}`, onClick: () => { S.fieldEdit = key; renderPanel(); const el = document.getElementById(`field-${key}`); if (el) el.focus(); }}, icon('pencil'))),
    content);
}
function staticField(label, text, empty) {
  return h('div', {class: 'brief-field'}, h('div', {class: 'brief-field-head'}, h('span', {class: 'label', text: label})), h('div', {class: empty ? 'empty' : 'value', text}));
}
async function saveField(key) {
  const el = document.getElementById(`field-${key}`);
  if (!el) return;
  await act(async () => {
    S.thread = await post(`api/threads/${S.thread.id}/fields`, {field: key, value: parseValue(key, el.value)});
    S.fieldEdit = null; render(); refreshBudget();
  });
}
function fieldsTab(run) {
  const nodes = [];
  const position = programPosition(run.request.curriculum, run.request.selected_row_id);
  nodes.push(staticField('Место в программе', position ? positionText(position) : 'УП не загружен — место проекта неизвестно', !position));
  for (const [group, keys] of GROUPS) {
    nodes.push(h('div', {class: 'brief-group', text: group}));
    for (const key of keys) {
      if (key === 'group_size' && S.thread.draft.format !== 'group') continue;
      nodes.push(fieldNode(key));
      if (key === 'tasks_count' && S.budget) nodes.push(h('p', {class: `hint workload-hint${S.budget.notes.length ? ' warn' : ''}`, text: budgetText(S.budget)}));
    }
  }
  // How the text is told is talked over when the content begins, not here: the concept does not read it.
  nodes.push(h('div', {class: 'brief-group', text: 'Подача текста · обсуждается на этапе контента'}));
  nodes.push(staticField('Стиль подачи', STYLE[S.thread.draft.style_profile || '']));
  nodes.push(staticField('Сквозной сюжет серии', S.thread.draft.series_story || 'Не задан', !S.thread.draft.series_story));
  // Where the project will live: the content stage checks by it that the files of the project are named by their paths.
  nodes.push(staticField('Репозиторий проекта в GitLab', S.thread.draft.repository || 'Не задан', !S.thread.draft.repository));
  if (S.thread.draft.program) nodes.push(staticField('Контекст программы (старая форма)', S.thread.draft.program));
  if (S.thread.draft.software) nodes.push(staticField('Необходимое ПО (старая форма, читается вместе с инструментами)', S.thread.draft.software));
  nodes.push(staticField('Материалы', run.request.materials.length ? run.request.materials.map((m) => `${m.name} · ${ROLES[m.role] || m.role}`).join('\n') : 'Файлов нет', !run.request.materials.length));
  return nodes;
}
function historyTab() {
  const t = S.thread, nodes = [];
  if (t.draft_changes.length) {
    nodes.push(h('div', {class: 'version draft'}, h('div', {class: 'version-head'}, h('strong', {text: `Черновик версии ${t.runs.length + 1}`}), h('small', {text: 'не разобран'})),
      h('p', {text: `Изменено: ${t.draft_changes.map((k) => t.fields[k]).join(', ')}`})));
  }
  [...t.runs].reverse().forEach((id, index) => {
    const run = S.runs[id];
    if (!run) return;
    const v = t.runs.length - index;
    const changed = (run.changes || []).filter((c) => c.location.startsWith('brief.')).map((c) => t.fields[c.location.slice(6)] || c.location.slice(6));
    nodes.push(h('div', {class: `version${index === 0 ? ' current' : ''}`},
      h('div', {class: 'version-head'}, h('strong', {text: `Версия ${v}${index === 0 ? ' · текущая' : ''}`}), h('small', {text: dateTime(run.created_at)})),
      h('p', {text: `${STATUS[run.status] || run.status} · правила ${run.prompt_version}`}),
      h('p', {text: v === 1 ? 'Исходные данные: форма и строка УП.' : changed.length ? `Изменено: ${changed.join(', ')}` : 'Задание не менялось'}),
      h('div', {class: 'links'}, h('a', {href: `api/runs/${run.id}/export/md`, text: 'Текст'}), h('a', {href: `api/runs/${run.id}/export/json`, text: 'JSON'}))));
  });
  return nodes;
}
function contextTab(run) {
  const stageNames = {context: 'Разбор входов', design: 'Замысел', review: 'Проверка'};
  const calls = run.events.filter((e) => e.status === 'done' && e.model);
  const nodes = [
    h('div', {class: 'ctx-block'}, h('h3', {text: 'Что получают модели'}),
      h('p', {class: 'hint', text: 'Разбор, замысел и проверка получают задание, строку и карту УП, отобранные фрагменты материалов и расчёты кода: место в программе и бюджет часов. Переписку чата они не получают — только задание, которое она меняет.'})),
    h('div', {class: 'ctx-block'}, h('h3', {text: `Вызовы моделей · правила ${run.prompt_version}`}),
      calls.length ? calls.map((e) => h('div', {class: 'ctx-item'}, h('strong', {text: `${stageNames[e.stage] || e.stage} · ${e.model}`}),
        h('small', {text: [e.elapsed_ms !== null && e.elapsed_ms !== undefined ? `${(e.elapsed_ms / 1000).toFixed(1)} с` : null,
          e.input_tokens !== null && e.input_tokens !== undefined ? `${e.input_tokens} → ${e.output_tokens} токенов` : null,
          e.schema_repairs ? `исправлений формата: ${e.schema_repairs}` : null].filter(Boolean).join(' · ')}))) : h('p', {class: 'hint', text: 'Вызовов ещё не было.'})),
  ];
  if (S.config && S.config.reviewer_model && S.config.reviewer_model !== S.config.model) {
    nodes.push(h('p', {class: 'hint ctx-block', text: `Проверяющий работает на отдельной модели: ${S.config.reviewer_model}.`}));
  }
  if (run.workload_budget) nodes.push(h('div', {class: 'ctx-block'}, h('h3', {text: 'Бюджет часов — расчёт кода'}), h('p', {class: 'hint', text: budgetText(run.workload_budget)})));
  nodes.push(h('div', {class: 'ctx-block'}, h('h3', {text: 'Источники'}), (run.source_registry || []).map((s) => h('div', {class: 'ctx-item'}, h('strong', {text: s.name}),
    h('small', {text: `${ROLES[s.role] || s.role} · ${READ[s.read_status] || s.read_status} · передано ${s.selected_count} из ${s.extracted_count}`}),
    [...(s.unread_locations || []), ...(s.limitations || [])].map((text) => h('small', {text}))))));
  if ((run.context_warnings || []).length) nodes.push(h('div', {class: 'ctx-block'}, h('h3', {text: 'Ограничения отбора'}), run.context_warnings.map((text) => h('p', {class: 'hint', text}))));
  return nodes;
}
function renderFooter(run) {
  // Stage actions live in the chat («Что дальше»); the brief only offers the files.
  const step = S.thread.next_step || {};
  $('#panelFoot').replaceChildren(h('p', {class: 'hint', text: step.title ? `${step.title}. Следующий шаг — в подсказках внизу чата.` : ''}),
    h('div', {class: 'buttons'}, h('a', {class: 'button', href: `api/runs/${run.id}/export/md`, text: 'Скачать текст'}),
      h('a', {class: 'button', href: `api/runs/${run.id}/export/json`, text: 'JSON'})));
}
function renderPanel() {
  const t = S.thread, run = latest();
  if (!t || !run) return;
  const pendingCount = t.messages.filter((m) => m.status === 'pending').reduce((sum, m) => sum + m.patches.length, 0);
  $('#versionLine').textContent = [`Версия ${t.runs.length}`, t.draft_changes.length ? `в черновике изменено: ${t.draft_changes.length}` : null,
    pendingCount ? `ждут решения: ${pendingCount}` : null].filter(Boolean).join(' · ');
  const badge = pendingCount || t.draft_changes.length;
  $('#panelCount').hidden = !badge; $('#panelCount').textContent = String(badge);
  document.querySelectorAll('.tabs [data-tab]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === S.tab)));
  const body = $('#panelBody');
  const scroll = body.scrollTop;
  body.replaceChildren(...(S.tab === 'fields' ? fieldsTab(run) : S.tab === 'history' ? historyTab() : contextTab(run)));
  body.scrollTop = scroll;
  renderFooter(run);
}

/* ---------- sidebar ---------- */
function remember() {
  if (!S.thread) return;
  const run = latest();
  const list = store.get('threads', []).filter((item) => item.id !== S.thread.id);
  list.unshift({id: S.thread.id, title: S.thread.draft.title, status: run ? run.status : '', at: new Date().toISOString()});
  store.set('threads', list.slice(0, 15));
  renderRecent();
}
async function pruneRecent() {
  // The list lives in this browser; a project deleted on the server must not stay in it as a dead link.
  const list = store.get('threads', []);
  const alive = await Promise.all(list.map((item) => fetch(`api/threads/${encodeURIComponent(item.id)}`).then((r) => r.status !== 404).catch(() => true)));
  const kept = list.filter((_, i) => alive[i]);
  if (kept.length !== list.length) { store.set('threads', kept); renderRecent(); }
}
function renderRecent() {
  const list = store.get('threads', []);
  const nav = $('#recentList');
  if (!list.length) { nav.replaceChildren(h('span', {class: 'empty', text: 'Пока нет проектов'})); return; }
  nav.replaceChildren(...list.map((item) => {
    const link = h('a', {href: `?thread=${encodeURIComponent(item.id)}`, 'aria-current': S.thread && S.thread.id === item.id ? 'page' : null},
      h('span', {text: item.title}), h('small', {text: STATUS[item.status] || 'открыть'}));
    link.addEventListener('click', (e) => { e.preventDefault(); openThread(item.id).catch((error) => { showIntake(); notify(`Диалог недоступен: ${error.message}`); }); });
    return link;
  }));
}
function closeDrawers() { $('#app').classList.remove('side-open', 'panel-open'); $('#backdrop').hidden = true; }
function toggleDrawer(kind) {
  const app = $('#app');
  const cls = kind === 'side' ? 'side-open' : 'panel-open';
  const open = !app.classList.contains(cls);
  app.classList.remove('side-open', 'panel-open');
  if (open) app.classList.add(cls);
  // The brief covers the chat on every screen, so the backdrop closes it; the sidebar is a drawer only when narrow.
  $('#backdrop').hidden = !open || (kind === 'side' && window.matchMedia('(min-width: 761px)').matches);
}

/* ---------- start ---------- */
async function init() {
  document.querySelectorAll('[data-icon]').forEach((el) => el.replaceWith(icon(el.dataset.icon)));
  form().addEventListener('submit', startRun);
  form().addEventListener('input', (e) => {
    if (e.target.closest('#outcomeRows') || e.target.type === 'file') return;
    saveIntake(); countMore();
    if (['hours', 'tasks_count', 'start_level', 'tools', 'software'].includes(e.target.name)) refreshIntakeWorkload();
  });
  field('format').addEventListener('change', syncGroup);
  field('sampling_mode').addEventListener('change', syncSampling);
  syncSampling();
  $('#curriculumFile').addEventListener('change', (e) => { const file = e.target.files[0]; if (file) { const data = new FormData(); data.append('file', file); loadCurriculum(api('api/curriculum', {method: 'POST', body: data}), file); } });
  $('#exampleButton').addEventListener('click', () => loadCurriculum(api('api/example')));
  $('#blockSelect').addEventListener('change', () => showProjects('', true));
  $('#projectSelect').addEventListener('change', () => showProjects($('#projectSelect').value, true));
  $('#materialFiles').addEventListener('change', (e) => uploadMaterials(Array.from(e.target.files)));
  $('#clearButton').addEventListener('click', clearIntake);
  $('#newProject').addEventListener('click', () => { showIntake(); restoreIntake(); });
  $('#composer').addEventListener('submit', send);
  $('#composerText').addEventListener('input', autoGrow);
  $('#composerText').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('#composer').requestSubmit(); } });
  document.querySelectorAll('.tabs [data-tab]').forEach((b) => b.addEventListener('click', () => { S.tab = b.dataset.tab; S.fieldEdit = null; renderPanel(); }));
  document.querySelectorAll('[data-toggle]').forEach((b) => b.addEventListener('click', () => toggleDrawer(b.dataset.toggle)));
  $('#backdrop').addEventListener('click', closeDrawers);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawers(); });
  try {
    S.config = await api('api/config');
    S.ns = S.config.storage_namespace || S.ns;
    const label = S.config.model_ready ? `ИИ · ${S.config.model}` : 'ИИ не подключён';
    $('#modelPill').textContent = label; $('#modelPill').classList.toggle('ready', S.config.model_ready);
    $('#modelState').textContent = `${label}${S.config.reviewer_model && S.config.reviewer_model !== S.config.model ? ` · проверка: ${S.config.reviewer_model}` : ''} · правила ${S.config.version}`;
    if (S.config.home_url && S.config.home_url !== './') { $('#platformLabel').hidden = false; $('#platformLink').hidden = false; $('#platformLink').href = S.config.home_url; }
    if (!S.config.model_ready) notify('На сервере не настроен ключ модели. Данные можно подготовить, разбор станет доступен после настройки.');
  } catch (error) { notify(error.message); }
  updateStart();
  renderRecent();
  pruneRecent();
  const id = new URLSearchParams(location.search).get('thread');
  if (id) {
    try { await openThread(id); return; } catch (error) { notify(`Диалог недоступен: ${error.message}`); }
  }
  showIntake();
  restoreIntake();
}
init();
