/* Final project report. All model/user text is rendered as text, never as HTML. */
'use strict';
const $ = (selector) => document.querySelector(selector);
const query = new URLSearchParams(location.search);
const run = query.get('run') || '';
const validId = (id) => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);
const endpoint = `api/content/${encodeURIComponent(run)}/evaluation`;
const labels = {pending:'Не проверено',assessed:'Оценено',not_applicable:'Неприменимо',no_data:'Нет исходных данных',unverified:'Не подтверждено',error:'Сбой проверки'};
const methods = {code:'Проверка кодом',model:'Анализ моделью',execution:'Нужно выполнение'};
const recommendations = {insufficient:'Недостаточно подтверждённых данных',rework:'Требуется доработка',targeted_revision:'Нужна адресная доработка',preliminary:'Предварительная оценка',ready_recommended:'Рекомендовано к рассмотрению',stale:'Отчёт относится к прежней версии'};
let data = null, timer = null, selectedQuote = '', busy = false, settingsLoaded = false, renderedIdentity = '';
function el(tag, attributes = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (key === 'text') element.textContent = value;
    else if (key.startsWith('on')) element.addEventListener(key.slice(2).toLowerCase(), value);
    else element.setAttribute(key, String(value));
  }
  for (const child of children.flat(Infinity)) if (child != null) element.append(child);
  return element;
}
function error(message = '') { $('#error').textContent = message; $('#error').hidden = !message; }
async function request(path, options = {}) {
  const response = await fetch(path, {credentials:'same-origin',...options});
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(response.status === 401 ? 'Войдите в приложение, затем снова откройте эту страницу.' : typeof body.detail === 'string' ? body.detail : `Запрос не выполнен (${response.status}).`);
  }
  return response.json();
}
function verdict(number) { return (data.verdicts || []).find((v) => v.criterion === number) || {criterion:number,state:'pending',mark:null,reason:'Проверка ещё не выполнялась.',evidence:[]}; }
function badge(v) {
  if (v.criterion > 40) return el('span', {class:'badge',text:v.state === 'assessed' ? 'Экспертное наблюдение' : labels[v.state] || v.state});
  const tone = v.mark === -1 || v.state === 'error' ? 'bad' : v.mark === 1 ? 'ok' : v.mark === 0 || v.state === 'no_data' ? 'warn' : '';
  return el('span', {class:`badge ${tone}`,text:v.state === 'assessed' ? `${v.mark > 0 ? '+' : ''}${v.mark} · ${v.mark === 1 ? 'Выполнено' : v.mark === 0 ? 'Частично' : 'Не выполнено'}` : labels[v.state] || v.state});
}
function showSource(reference, full = false) {
  const unit = (data.snapshot?.units || []).find((u) => u.id === reference.unit);
  if (!unit) return;
  $('#sourcesDetails').open = true;
  $('#sourceSelect').value = unit.id;
  $('#sourceTitle').textContent = `${unit.title} · ${unit.id}`;
  selectedQuote = reference.quote || '';
  const index = selectedQuote ? unit.text.indexOf(selectedQuote) : -1;
  const begin = !full && index > 1000 ? index - 500 : 0;
  const end = full ? unit.text.length : Math.min(unit.text.length, Math.max(begin + 4000, index + selectedQuote.length + 500));
  const text = unit.text.slice(begin, end), offset = selectedQuote ? text.indexOf(selectedQuote) : -1;
  const content = $('#sourceText'); content.replaceChildren();
  if (begin) content.append('…\n');
  if (offset >= 0) content.append(text.slice(0,offset), el('mark',{text:selectedQuote}), text.slice(offset+selectedQuote.length));
  else content.append(text);
  if (end < unit.text.length) content.append('\n…');
  $('#fullSource').hidden = full || begin === 0 && end === unit.text.length;
  $('#sourceSection').scrollIntoView({block:'start'});
}
function sourceButton(reference) {
  const unit = (data.snapshot?.units || []).find((u) => u.id === reference.unit);
  return el('button', {type:'button',class:'source-button',text:unit?.title || reference.unit,onClick:() => showSource(reference)});
}
function criterionCard(c) {
  const v = verdict(c.number), details = el('details',{class:'criterion','data-number':c.number});
  details.append(el('summary',{},el('span',{class:'number',text:String(c.number).padStart(2,'0')}),
    el('span',{},el('span',{class:'criterion-title',text:c.title}),el('span',{class:'group',text:c.section})),el('span',{class:'result'},badge(v))));
  const body = el('div',{class:'criterion-body'},el('p',{class:'muted',text:`${methods[v.method] || 'Проверка не выполнялась'}${v.reused ? ' · результат повторно использован: зависимости не изменились' : ''}`}),
    el('p',{text:v.reason || 'Проверка ожидает выполнения.'}));
  if (c.number <= 40 && v.confirmed_blocker) body.append(el('p',{class:'badge bad',text:'Критическое замечание по правилам профиля'}));
  if (v.evidence_valid === false) body.append(el('p',{class:'notice warning',text:'Цитаты не подтвердились. Оценка модели не принята.'}));
  for (const e of v.evidence || []) body.append(el('div',{class:'evidence'},sourceButton(e),el('blockquote',{text:e.quote})));
  if (v.suggestion) body.append(el('h3',{text:'Что изменить'}),el('p',{text:v.suggestion}));
  body.append(el('details',{class:'rubric'},el('summary',{text:c.number <= 40 ? 'Основания оценки по документу' : 'Ориентиры для обсуждения'}),
    el('p',{text:`${c.number <= 40 ? '+1' : 'Ориентир качества'}: ${c.fulfilled}`}),el('p',{text:`${c.number <= 40 ? '0' : 'Что стоит уточнить'}: ${c.partial}`}),el('p',{text:`${c.number <= 40 ? '−1' : 'Возможная проблема'}: ${c.failed}`}),
    c.requires_confirmation ? el('p',{text:`Нужно дополнительное подтверждение: ${c.requires_confirmation}.`}) : null));
  details.append(body); return details;
}
function renderCriteria() {
  if (!data) return;
  const open = new Set([...document.querySelectorAll('.criterion[open]')].map((x) => Number(x.dataset.number)));
  const filter = $('#statusFilter').value, section = $('#sectionFilter').value, search = $('#search').value.trim().toLocaleLowerCase('ru');
  const list = data.profile.criteria.filter((c) => {
    const v = verdict(c.number);
    return (section === 'all' || c.section === section) && (!search || `${c.number} ${c.title}`.toLocaleLowerCase('ru').includes(search)) &&
      (filter === 'all' || filter === v.state || filter === 'blockers' && c.number <= 40 && v.confirmed_blocker || filter === 'problems' && (c.number > 40 && v.state === 'assessed' || c.number <= 40 && (v.mark === -1 || v.mark === 0) || ['error','no_data','unverified'].includes(v.state)));
  });
  $('#criteria').replaceChildren();
  // Keep expert opinions visibly separate from the scored acceptance checks, also under filters.
  for (const [title, hint, group] of [
    ['Проверка требований · 1–40','Определяет итоговую рекомендацию',list.filter((c) => c.number <= 40)],
    ['Экспертные замечания · 41–56','Предварительные наблюдения о качестве. Без баллов и влияния на итог.',list.filter((c) => c.number > 40)]
  ]) if (group.length) $('#criteria').append(el('div',{class:'criteria-group'},el('h3',{text:title}),el('p',{class:'muted',text:hint})),...group.map((c) => { const node = criterionCard(c); node.open = open.has(c.number); return node; }));
  $('#shown').textContent = `${list.length} из ${data.profile.criteria.length}`;
  if (!list.length) $('#criteria').append(el('p',{class:'muted',text:'По выбранным условиям критериев нет.'}));
}
function metric(label, value, hint) { return el('article',{class:'metric'},el('span',{class:'muted',text:label}),el('strong',{class:'value',text:value}),el('small',{text:hint})); }
function openCriterion(number) {
  $('#statusFilter').value = 'all'; $('#sectionFilter').value = 'all'; $('#search').value = '';
  renderCriteria();
  const card = document.querySelector(`.criterion[data-number="${Number(number)}"]`);
  if (card) { card.open = true; card.scrollIntoView({block:'start'}); }
}
function findingCard(finding) {
  const card = el('article',{class:'finding'});
  card.append(el('h3',{text:finding.title}),el('span',{class:`badge ${finding.level === 'critical' ? 'bad' : 'warn'}`,text:{critical:'Критический дефект',defect:'Дефект',partial:'Частично выполненное требование'}[finding.level]}),
    el('p',{text:finding.reason}),el('div',{class:'finding-links'},el('span',{text:'Связанные критерии: '}),
    ...(finding.criteria || []).map((n) => el('button',{type:'button',class:'source-button',text:String(n),onClick:() => openCriterion(n)}))));
  if (finding.suggestion) card.append(el('p',{text:finding.suggestion}));
  if (finding.basis === 'criterion') card.append(el('p',{class:'muted',text:'Отдельная запись: общая причина с другими замечаниями не подтверждена.'}));
  for (const e of finding.evidence || []) card.append(finding.basis === 'missing_dependency' ?
    el('p',{class:'muted'},'Основание: ',sourceButton(e)) : el('div',{class:'evidence'},sourceButton(e),el('blockquote',{text:e.quote})));
  return card;
}
function renderFindings(summary) {
  $('#findings').replaceChildren(...((summary.findings || []).length ? summary.findings.map(findingCard) :
    [el('p',{class:'muted',text:summary.incomplete ? 'Подтверждённых замечаний пока нет. Проверка неполная; это не подтверждение готовности.' : 'Замечаний к исправлению не осталось.'})]));
  const queue = summary.review_queue || [];
  $('#reviewQueue').replaceChildren(...(queue.length ? [el('h3',{text:'Нужно проверить основания замечаний'}),...queue.map((v) => el('article',{class:'finding'},
    el('button',{type:'button',class:'source-button',text:`Критерий ${v.criterion}`,onClick:() => openCriterion(v.criterion)}),el('p',{text:v.reason}),
    el('p',{class:'muted',text:'Цитата не подтверждена. Замечание сохранено для разбора и не считается доказанным дефектом.'})))] : []));
}
function render() {
  const identity = data.exists ? `${data.id}:${data.snapshot.digest}` : '';
  if (identity !== renderedIdentity) {
    // A source or chain from another frozen report must never survive a report switch.
    $('#sourceText').replaceChildren(); $('#sourceTitle').textContent = '';
    $('#sourcesDetails').open = false; $('#fullSource').hidden = true;
    selectedQuote = '';
    renderedIdentity = identity;
  }
  const running = data.exists && data.state === 'running';
  if (!settingsLoaded) {
    if (data.exists) { $('#approvedFiles').value = (data.snapshot.approved_files || []).join('\n'); $('#useModel').checked = data.use_model; }
    settingsLoaded = true;
  }
  $('#start').disabled = running || busy;
  $('#start').textContent = !data.exists ? 'Проверить проект' : ['partial','interrupted'].includes(data.state) ? 'Продолжить проверку' : 'Проверить изменения';
  $('#stop').hidden = !running;
  $('#useModel').disabled = running || !data.model_ready;
  if (!data.model_ready) $('#useModel').checked = false;
  $('#approvedFiles').disabled = running;
  $('#force').disabled = running;
  $('#state').textContent = !data.exists ? 'Не запускалась' : ({running:'Выполняется',complete:'Проверка завершена',partial:'Завершена частично',interrupted:'Прервана'}[data.state] || data.state);
  $('#progressRow').hidden = !running;
  $('#progress').value = data.progress || 0;
  $('#progressText').textContent = `${data.progress || 0} / ${data.total || 56}`;
  if (!$('#sectionFilter').dataset.ready) {
    [...new Set(data.profile.criteria.map((c) => c.section))].forEach((section) => $('#sectionFilter').append(el('option',{value:section,text:section})));
    $('#sectionFilter').dataset.ready = '1';
  }
  const s = data.summary;
  if (s && !s.incomplete && ['partial','interrupted'].includes(data.state)) $('#state').textContent = 'Требования проверены · обзор не завершён';
  if (s) {
    $('#title').textContent = data.snapshot.title;
    $('#subtitle').textContent = `Проверяемый комплект от ${new Date(data.created_at).toLocaleString('ru-RU')}`;
    const notice = $('#recommendation'); notice.hidden = false;
    notice.className = `notice ${s.recommendation === 'rework' ? 'danger' : s.recommendation === 'ready_recommended' ? 'success' : 'warning'}`;
    notice.replaceChildren(el('h2',{text:recommendations[s.recommendation]}),el('p',{text:s.stale ? 'Текст, входные данные или правила изменились. Повторите проверку текущей версии; прежние оценки сохранены для старого снимка.' : s.blockers.length ? `Есть критический дефект; затронуты критерии: ${s.blockers.join(', ')}. Подробности и связанные требования — ниже. В демонстрационном режиме продолжать работу можно.` : s.incomplete ? 'Часть условий ещё не подтверждена. Подтверждённые замечания и полнота проверки показаны отдельно. Продолжать работу с проектом можно.' : 'Это экспериментальная рекомендация для методолога. Автоматическое согласование проекта не выполняется.'}));
    $('#metrics').replaceChildren(metric('Проверка требований · 1–40',`${s.assessed} / ${s.applicable}`,`Неприменимо: ${s.counts.not_applicable || 0} · осталось проверить: ${s.unverified}`),
      metric('Подтверждено выполнено',String(s.fulfilled || 0),`Не выполнено требований: ${s.problems} · частично: ${s.partial}`),
      metric('Группы замечаний',String(s.finding_count || 0),`Критических: ${s.finding_counts?.critical || 0} · прочих дефектов: ${s.finding_counts?.defect || 0} · частичных: ${s.finding_counts?.partial || 0}`),
      metric('Экспертный обзор · 41–56',`${s.reviewed} / ${s.review_total}`,'Рассмотрено · без баллов и влияния на итог'));
    renderFindings(s);
    $('#meta').textContent = `Правила итога ${s.policy_version || '—'} · профиль ${data.profile.version} · ${data.model || 'Проверки кодом'} · вызовов в этом запуске: ${data.calls} · повторно использовано критериев: ${(data.verdicts || []).filter((v) => v.reused).length}`;
    $('#exports').hidden = false;
    $('#exportMd').href = `${endpoint}/export/md`; $('#exportJson').href = `${endpoint}/export/json`;
    const limitations = [...(data.snapshot.limitations || []),...(data.notes || []),...(data.history_warnings || [])];
    $('#limits').hidden = !limitations.length;
    $('#limitList').replaceChildren(...limitations.map((text) => el('li',{text})));
    $('#outcomeList').replaceChildren(...(data.outcomes.length ? data.outcomes.map((o) => el('article',{class:'outcome'},
      el('div',{class:'outcome-head'},el('h3',{text:`ОР ${o.outcome}. ${data.snapshot.outcomes[o.outcome-1] || ''}`}),el('span',{class:`badge ${o.status === 'confirmed' ? 'ok' : 'warn'}`,text:{confirmed:'Цепочка подтверждена',gap:'Есть разрыв',uncertain:'Нужна проверка'}[o.status]})),
      el('p',{class:'muted',text:o.reason}),el('div',{class:'chain'},[['Действие',o.action],['Результат',o.result],['P2P',o.peer]].map(([title,refs]) => el('div',{},el('h3',{text:title}),refs.length ? refs.map((e) => el('div',{},sourceButton(e),el('p',{class:'muted',text:e.quote}))) : el('p',{class:'muted',text:'Свидетельство не предъявлено'})))))) : [el('p',{class:'muted',text:'Цепочки ОР в этом отчёте ещё не проверены. Это не подтверждение достижения ОР.'})]));
    const selected = $('#sourceSelect').value;
    $('#sourceSelect').replaceChildren(el('option',{value:'',text:'Выберите фрагмент'}),...data.snapshot.units.map((u) => el('option',{value:u.id,text:u.title})));
    if (data.snapshot.units.some((u) => u.id === selected)) $('#sourceSelect').value = selected;
  } else {
    $('#title').textContent = 'Проверка проекта';
    $('#recommendation').hidden = true; $('#exports').hidden = true; $('#meta').textContent = '';
    $('#outcomeList').replaceChildren(el('p',{class:'muted',text:'Связи появятся после содержательной проверки.'}));
    $('#sourceSelect').replaceChildren();
    $('#limits').hidden = !(data.history_warnings || []).length;
    $('#limitList').replaceChildren(...(data.history_warnings || []).map((text) => el('li',{text})));
    $('#subtitle').textContent = 'Проверка ещё не запускалась. Все 56 критериев доступны ниже.';
    $('#metrics').replaceChildren(metric('Проверка требований · 1–40','0 / 40','Определяет итоговую рекомендацию'),metric('Подтверждено выполнено','—','После проверки требований'),metric('Группы замечаний','—','По подтверждённым причинам'),metric('Экспертный обзор · 41–56','0 / 16','Без баллов и влияния на итог'));
    $('#findings').replaceChildren(el('p',{class:'muted',text:'Замечания появятся после проверки.'})); $('#reviewQueue').replaceChildren();
  }
  renderCriteria();
}
async function refresh() {
  clearTimeout(timer);
  try {
    data = await request(endpoint); error(); render();
    if (data.state === 'running') timer = setTimeout(refresh,1500);
  } catch (e) { error(e.message); if (data?.state === 'running') timer = setTimeout(refresh,5000); }
}
$('#start').addEventListener('click',async () => {
  if (busy) return;
  busy = true; $('#start').disabled = true; error();
  try {
    const text = $('#approvedFiles').value.trim();
    data = await request(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({use_model:$('#useModel').checked,force:$('#force').checked,approved_files:text ? text.split('\n').map((p) => p.trim()).filter(Boolean) : null})});
    render(); timer = setTimeout(refresh,500);
  } catch (e) { error(e.message); } finally { busy = false; if (data) render(); }
});
$('#stop').addEventListener('click',async () => { try { data = await request(`${endpoint}/stop`,{method:'POST'}); render(); } catch(e) { error(e.message); } });
for (const id of ['#statusFilter','#sectionFilter','#search']) $(id).addEventListener('input',renderCriteria);
$('#sourceSelect').addEventListener('change',() => showSource({unit:$('#sourceSelect').value,quote:''}));
$('#fullSource').addEventListener('click',() => showSource({unit:$('#sourceSelect').value,quote:selectedQuote},true));
window.addEventListener('beforeunload',() => clearTimeout(timer));
if (validId(query.get('thread') || '')) $('#back').href = `./?thread=${encodeURIComponent(query.get('thread'))}`;
if (validId(run)) refresh(); else { error('Откройте проверку из проекта с подготовленным контентом.'); $('#state').textContent = 'Нет проекта'; }
