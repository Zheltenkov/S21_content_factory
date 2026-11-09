/* Isolated UI state. Source text is rendered with textContent, never as HTML. */
const $ = (id) => document.getElementById(id);
const form = $('briefForm');
const state = {curriculum: null, materials: [], run: null, tab: 'concept', timer: null, busy: false, ready: false, dirty: false, uploads: 0};
let storageKey = 's21.project-lab.v1';
const roles = {reference: 'Справочный материал', requirement: 'Обязательное требование', example: 'Пример проекта', starter: 'Данные или заготовка', reviewer: 'Для проверяющего'};
const labels = {given: 'ЗАДАНО В ИСТОЧНИКЕ', proposal: 'ПРЕДЛОЖЕНО МОДЕЛЬЮ', conflict: 'ЕСТЬ ПРОТИВОРЕЧИЕ'};
const fieldLabels = {program:'Описание программы', title:'Название проекта', description:'Описание проекта', outcomes:'Учебные результаты', audience:'Аудитория', hours:'Трудоёмкость', student_result:'Сдаваемый результат', checking:'Проверка работы', notes:'Уточнения методолога', software:'Необходимое ПО', tools:'Инструменты', constraints:'Ограничения'};

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text != null) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function notify(message = '') { $('notice').textContent = message; $('notice').hidden = !message; }
async function api(url, options = {}) {
  const response = await fetch(url, options);
  const result = await response.json();
  if (!response.ok) {
    const detail = result.detail;
    throw new Error(Array.isArray(detail) ? detail.map(e => `${e.loc.slice(1).join('.')}: ${e.msg}`).join('\n') : detail || 'Не удалось выполнить запрос');
  }
  return result;
}
function briefData() {
  const result = {};
  for (const input of form.elements) if (input.name) result[input.name] = input.value;
  for (const key of ['hours', 'tasks_count', 'group_size']) result[key] = result[key] ? Number(result[key]) : null;
  if (result.format !== 'group') result.group_size = null;
  for (const key of ['diagrams','tables','formulas','bonus']) result[key] = form.elements.namedItem(key).checked;
  // The selected curriculum row is the single source of the block context.
  result.block = state.curriculum?.rows.find(row => row.id === $('projectSelect').value)?.block || '';
  return result;
}
function fillBrief(data) {
  for (const [key,value] of Object.entries(data)) {
    const input = form.elements.namedItem(key);
    if (!input) continue;
    if (input.type === 'checkbox') input.checked = Boolean(value);
    else input.value = value ?? '';
  }
  $('groupField').hidden = $('format').value !== 'group';
  $('group_size').required = $('format').value === 'group';
}
function saveDraft() {
  // Persist parsed uploads too, so a reload never silently drops the evidence set.
  try {
    localStorage.setItem(storageKey, JSON.stringify({brief: briefData(), curriculum: state.curriculum, selectedRow: $('projectSelect').value, materials: state.materials, runId: state.run?.id || null}));
    $('saveState').textContent = '✓ Сохранено в этом браузере';
  } catch { $('saveState').textContent = 'Не удалось сохранить в браузере · скачайте результат'; }
}
function changed() {
  if (state.run) {state.dirty = true; $('runBadge').textContent = 'ПАРАМЕТРЫ ИЗМЕНЕНЫ';}
  saveDraft();
  updateApproval();
}
function setBusy(busy) {
  state.busy = busy;
  document.body.classList.toggle('busy', busy);
  $('inputFields').disabled = busy;
  $('clearButton').disabled = busy;
  $('prepareButton').disabled = busy || !state.ready || state.uploads > 0;
  $('prepareButton').replaceChildren(node('span', busy ? 'Подготовка…' : state.run ? 'Разобрать входы новой версии →' : 'Разобрать входные данные →'));
}
function setOptions(select, values) {
  select.replaceChildren(...values.map(([id,label]) => { const option = node('option', label); option.value = id; return option; }));
}
function showCurriculum(selectedId, apply = true) {
  const curriculum = state.curriculum;
  $('curriculumSelectors').hidden = !curriculum;
  $('removeCurriculum').hidden = !curriculum;
  $('curriculumName').textContent = curriculum ? `${curriculum.filename} · ${curriculum.rows.length} проектов` : 'Сохраним контекст всей программы';
  if (!curriculum) return;
  setOptions($('blockSelect'), [...new Set(curriculum.rows.map(r => r.block))].map(b => [b,b]));
  const selected = curriculum.rows.find(r => r.id === selectedId) || curriculum.rows[0];
  $('blockSelect').value = selected.block;
  showProjects(selected.id, apply);
}
function showProjects(selectedId, apply = true) {
  const rows = state.curriculum.rows.filter(r => r.block === $('blockSelect').value);
  setOptions($('projectSelect'), rows.map(r => [r.id, r.title]));
  if (rows.some(r => r.id === selectedId)) $('projectSelect').value = selectedId;
  if (apply) applyProject();
}
function applyProject() {
  const row = state.curriculum.rows.find(r => r.id === $('projectSelect').value);
  if (!row) return;
  // Reset project-specific fields to prevent carryover from the preceding selected row.
  fillBrief({title:row.title,description:row.description,outcomes:row.outcomes,skills:row.skills,audience:row.audience,tools:row.tools,software:row.software,storytelling:row.storytelling,storytelling_type:row.storytelling_type||(row.storytelling?'sjm':'none'),format:/групп|group/i.test(row.format)?'group':'individual',group_size:Number(row.group_size)>1?Number(row.group_size):null,hours:Number(row.hours.replace(',','.'))||null,references:row.materials,student_result:'',checking:'',constraints:'',output_package:'',notes:'',tasks_count:null});
  changed();
}
async function uploadCurriculum(file) {
  if (!file) return;
  state.uploads++;setBusy(state.busy);
  const data = new FormData(); data.append('file', file);
  try { notify(); state.curriculum = await api('api/curriculum', {method:'POST',body:data}); showCurriculum(); }
  catch(error) {notify(error.message);}
  state.uploads--;setBusy(state.busy);
  $('curriculumFile').value = '';
}
function showMaterials() {
  const list = $('materialsList'); list.replaceChildren();
  for (const material of state.materials) {
    const item = node('div', null, 'material-item');
    const top = node('div',null,'file-row'); top.append(node('strong',material.name));
    const remove = node('button','Убрать','text-button'); remove.type='button'; remove.onclick=()=>{state.materials=state.materials.filter(m=>m.id!==material.id);showMaterials();changed();}; top.append(remove);
    const role = node('select'); role.setAttribute('aria-label',`Роль материала ${material.name}`);setOptions(role,Object.entries(roles));role.value=material.role;role.onchange=()=>{material.role=role.value;changed();};
    const preview=node('details'); preview.append(node('summary',`Извлечённый текст · ${material.passages.length} фрагментов`));
    const text=material.passages.map(p=>`${p.location}\n${p.text}`).join('\n\n');preview.append(node('pre',text));
    item.append(top,role,node('p',readLabels[material.read_status] || 'Старый импорт — загрузите повторно','hint'),preview);
    for(const warning of material.warnings)item.append(node('p',warning,'hint'));
    list.append(item);
  }
}
async function uploadMaterials(files) {
  state.uploads++;setBusy(state.busy);
  notify();
  for (const file of files) {
    if(state.materials.length>=12){notify('Можно прикрепить до 12 материалов.');break;}
    const data=new FormData();data.append('file',file);
    try {const material=await api('api/materials',{method:'POST',body:data});if(!state.materials.some(m=>m.id===material.id))state.materials.push(material);showMaterials();changed();}
    catch(error){state.materials.push({id:crypto.randomUUID(),name:file.name,role:'reference',sha256:'',passages:[],warnings:[error.message],read_status:'failed',unread_locations:[]});showMaterials();changed();notify(`${file.name}: ${error.message}`);}
  }
  $('materialFiles').value='';
  state.uploads--;setBusy(state.busy);
}
function section(target,title,text) {target.append(node('h3',title),node('p',text));}
function bullets(target,title,items) {if(!items.length)return;target.append(node('h3',title));const ul=node('ul');items.forEach(t=>ul.append(node('li',t)));target.append(ul);}
function sourceLinks(target,ids) {
  for (const id of ids) {
    const evidence=state.run.evidence.find(e=>e.id===id);
    const button=node('button',evidence?`${evidence.source} · ${fieldLabels[evidence.location]||evidence.location}`:id,'source-button');button.type='button';
    button.onclick=()=>{state.tab='sources';renderTab();const element=document.getElementById(`evidence-${id}`);if(element){element.open=true;element.scrollIntoView({block:'center',behavior:'smooth'});}};
    target.append(button);
  }
}
function renderTab() {
  document.querySelectorAll('[data-tab]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.tab===state.tab)));
  const target=$('tabContent');target.replaceChildren();
  const run=state.run; if(!run)return;
  const c=run.concept;
  if(state.tab==='inputs') {
    renderInputs(target,run);

  } else if(state.tab==='concept') {
    if(!c){target.append(node('p','Здесь появится результат после подготовки замысла. Ход работы показан выше.','pending-copy'));return;}
    section(target,'Контекст программы',c.program_summary);section(target,'Место этого проекта',c.project_role);section(target,'Что создаст слушатель',c.student_result);
    bullets(target,'Предполагаемая подготовка',c.prerequisites);bullets(target,'Где результат понадобится дальше',c.later_use);bullets(target,'Что оставляем за пределами проекта',c.out_of_scope);
    target.append(node('h3','План практической работы'));
    c.tasks.forEach((task,i)=>{const article=node('article',null,'task');article.append(node('span',`${task.optional?'Бонус':`Задание ${i+1}`} · ${task.hours} ч`,'label'),node('h4',task.title),node('p',task.action));const dl=node('dl');for(const [label,value]of [['Результат работы',task.deliverable],['Как проверяет пир',task.peer_check],['Что слушатель решает сам',task.independent_choice],['Ориентиры и материалы',task.support],['Учебные результаты',task.outcome_numbers.join(', ')]])dl.append(node('dt',label),node('dd',value));article.append(dl);sourceLinks(article,task.evidence_ids);target.append(article);});
    bullets(target,'Что подготовить к созданию контента',c.material_gaps);
    if(c.questions.length){target.append(node('h3','Вопросы к методологу'));for(const q of c.questions){const item=node('div',null,'finding warning');item.append(node('strong',q.question),node('p',q.why));target.append(item);}}
  } else if(state.tab==='decisions') {
    target.append(node('p','Краткие обоснования решений с опорой на входные данные. Здесь видно, что задано, что предложено и где есть противоречия.','hint'));
    if(!c){target.append(node('p','Обоснования появятся вместе с замыслом.'));return;}
    for(const decision of c.decisions){const item=node('article',null,'decision');item.append(node('span',labels[decision.kind],`label ${decision.kind}`),node('h4',decision.title),node('p',decision.explanation));sourceLinks(item,decision.evidence_ids);target.append(item);}
  } else if(state.tab==='review') {
    renderGroundedReview(target,run);
    target.append(node('h3','Ход подготовки'));
    for(const event of run.events){const item=node('div',null,'source-item');item.append(node('strong',event.message));const meta=[new Date(event.at).toLocaleTimeString('ru-RU'),event.model,event.elapsed_ms!=null?`${(event.elapsed_ms/1000).toFixed(1)} с`:null,event.input_tokens!=null?`Токены: ${event.input_tokens} вход / ${event.output_tokens} выход`:null,event.schema_repairs?`Исправлений формата: ${event.schema_repairs}`:null].filter(Boolean);item.append(node('p',meta.join(' · '),'hint'));target.append(item);}
  } else {
    showSourceRegistry(target,run);
    target.append(node('p','Фрагменты, переданные модели. Исходный УП сохраняется отдельно в JSON запуска. Ссылки в текстах не означают, что страницы были прочитаны.','hint'));
    bullets(target,'Ограничения чтения и отбора',run.context_warnings);
    for(const evidence of run.evidence){const details=node('details',null,'source-item');details.id=`evidence-${evidence.id}`;details.append(node('summary',`${evidence.source} · ${fieldLabels[evidence.location]||evidence.location}`),node('p',`Роль: ${roles[evidence.role]||evidence.role} · ${evidence.id}`,'hint'),node('pre',evidence.text));target.append(details);}
  }
  if(state.tab==='concept' && c){
    const titles={'Контекст программы':'program_summary','Место этого проекта':'project_role','Что создаст слушатель':'student_result','Предполагаемая подготовка':'prerequisites','Где результат понадобится дальше':'later_use','Что оставляем за пределами проекта':'out_of_scope','План практической работы':'tasks','Что подготовить к созданию контента':'material_gaps','Вопросы к методологу':'questions'};
    target.querySelectorAll('h3').forEach(h=>{if(titles[h.textContent])h.id=`concept-${titles[h.textContent]}`;});
    target.querySelectorAll('article.task').forEach((article,i)=>article.id=`concept-tasks.${i+1}`);
  }
  if(state.tab==='decisions'){target.querySelectorAll('article.decision').forEach((article,i)=>article.id=`concept-decisions.${i+1}`);}
}
function updateApproval() {
  const run=state.run;if(!run)return;
  $('confirmInputsButton').hidden=run.status!=='input_review';
  $('confirmInputsButton').disabled=state.dirty||state.busy;
  $('confirmInputsButton').textContent=!run.input_card?'Продолжить разбор входов':run.input_findings?.length?'Продолжить с предупреждениями':'Подтвердить входы и подготовить замысел';
  $('approveButton').hidden=!run.concept;
  $('approveButton').disabled=run.status!=='review'||!run.concept||!run.review||state.busy||state.dirty;
  $('approveButton').textContent=run.status==='approved'?'✓ Замысел согласован':'Согласовать замысел';
  // Only show an actionable hint when inputs differ from the displayed result.
  $('approvalHint').hidden=!state.dirty;
  $('approvalHint').textContent=state.dirty?(run.status==='input_review'?'Входы изменены. Подготовьте новую карточку.':'Параметры изменены. Подготовьте новую версию: показанный результат относится к предыдущему запуску.') : '';
}
function renderRun(run) {
  if(run.status==='input_review' && (state.run?.id!==run.id || state.run?.status!=='input_review'))state.tab='inputs';
  state.run=run;$('emptyState').hidden=true;$('runView').hidden=false;
  $('resultTitle').textContent=run.concept?.title||run.request.brief.title;
  $('runMeta').textContent=`${new Date(run.created_at).toLocaleString('ru-RU')} · версия правил ${run.prompt_version}`;
  const statusText={running:'В РАБОТЕ',input_review:'ПРОВЕРЬТЕ ВХОДЫ',review:'НА ОБСУЖДЕНИИ',approved:'СОГЛАСОВАНО',failed:'НЕ ЗАВЕРШЕНО',interrupted:'ПРЕРВАНО'};
  if(!state.dirty)$('runBadge').textContent=statusText[run.status];
  $('runBadge').className=`badge ${run.status==='approved'?'success':['failed','interrupted'].includes(run.status)?'warning':''}`;
  const timeline=$('timeline');timeline.replaceChildren();
  for(const [stage,label]of [['context','01 Входы'],['inputs_approval','02 Согласование'],['design','03 Замысел'],['review','04 Проверка']]){const event=run.events.filter(e=>e.stage===stage).at(-1);const item=node('li',label,event?.status||'pending');item.append(node('small',event?{running:'в работе',done:'готово',failed:'не завершено'}[event.status]:'ожидает'));timeline.append(item);}
  if(run.error)notify(run.error);
  setBusy(run.status==='running');renderTab();updateApproval();
  $('resultActions').hidden=false;
  $('downloadMd').href=`api/runs/${run.id}/export/md`;$('downloadJson').href=`api/runs/${run.id}/export/json`;
}
async function poll(runId) {
  clearTimeout(state.timer);
  try {const run=await api(`api/runs/${runId}`);renderRun(run);if(run.status==='running')state.timer=setTimeout(()=>poll(runId),1800);else saveDraft();}
  catch(error){notify(`${error.message}. Можно обновить статус, не запуская подготовку повторно.`);state.timer=setTimeout(()=>poll(runId),6000);}
}
form.addEventListener('submit',async event=>{
  event.preventDefault();if(state.busy||state.uploads||!form.reportValidity())return;
  // Capture before disabling the fieldset (disabled inputs are absent from FormData).
  const request={request_id:crypto.randomUUID(),brief:briefData(),curriculum:state.curriculum,selected_row_id:state.curriculum?$('projectSelect').value:'',materials:state.materials,parent_run_id:state.run?.id||null};
  saveDraft();notify();setBusy(true);
  try {const result=await api('api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});state.dirty=false;const run=await api(`api/runs/${result.id}`);renderRun(run);saveDraft();if(window.innerWidth<780)$('resultPanel').scrollIntoView();poll(result.id);}
  catch(error){
    // A lost start response is not evidence that the server rejected the request.
    try{const run=await api(`api/runs/${request.request_id}`);state.dirty=false;renderRun(run);saveDraft();poll(run.id);}
    catch{notify(error.message);setBusy(false);}
  }
});
form.addEventListener('input',()=>{if(!state.busy)changed();});
$('format').addEventListener('change',()=>{$('groupField').hidden=$('format').value!=='group';$('group_size').required=$('format').value==='group';});
$('curriculumFile').addEventListener('change',event=>uploadCurriculum(event.target.files[0]));
$('materialFiles').addEventListener('change',event=>uploadMaterials(Array.from(event.target.files)));
$('blockSelect').addEventListener('change',()=>showProjects());
$('projectSelect').addEventListener('change',applyProject);
$('exampleButton').onclick=async()=>{try{notify();state.curriculum=await api('api/example');showCurriculum();}catch(error){notify(error.message);}};
$('removeCurriculum').onclick=()=>{state.curriculum=null;showCurriculum();changed();};
$('clearButton').onclick=()=>{clearTimeout(state.timer);form.reset();fillBrief({});state.curriculum=null;state.materials=[];state.run=null;state.dirty=false;showCurriculum();showMaterials();$('groupField').hidden=true;$('runView').hidden=true;$('emptyState').hidden=false;$('runBadge').textContent='ЧЕРНОВИК';notify();saveDraft();setBusy(false);};
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>{state.tab=button.dataset.tab;renderTab();});
$('refreshButton').onclick=()=>{if(state.run)poll(state.run.id);};
$('approveButton').onclick=async()=>{if(state.dirty)return;try{const run=await api(`api/runs/${state.run.id}/approve`,{method:'POST'});renderRun(run);saveDraft();}catch(error){notify(error.message);}};
$('confirmInputsButton').onclick=confirmInputCard;
async function initialize() {
  setBusy(false);
  let runId;
  // Identify the session before loading a browser draft, including account switches.
  try{const config=await api('api/config');storageKey=config.storage_namespace||storageKey;document.querySelector('.brand').href=config.home_url||'./';state.ready=config.model_ready;$('modelState').textContent=config.model_ready?`ИИ · ${config.model}`:'ИИ не подключён';if(!config.model_ready)notify('На сервере не настроен POLZA_AI_API_KEY. Исходные данные можно подготовить и сохранить в браузере.');setBusy(false);}catch(error){notify(error.message);return;}
  try{const saved=JSON.parse(localStorage.getItem(storageKey)||'null');if(saved){state.curriculum=saved.curriculum;state.materials=saved.materials||[];showCurriculum(saved.selectedRow,false);fillBrief(saved.brief);showMaterials();runId=saved.runId;}}catch{notify('Не удалось восстановить локальный черновик. Можно заполнить форму заново.');}
  const linkedRun=new URLSearchParams(location.search).get('run');
  if(linkedRun){try{const run=await api(`api/runs/${encodeURIComponent(linkedRun)}`);state.curriculum=run.request.curriculum;state.materials=run.request.materials;showCurriculum(run.request.selected_row_id,false);fillBrief(run.request.brief);showMaterials();state.dirty=false;renderRun(run);saveDraft();if(run.status==='running')poll(run.id);return;}catch(error){notify(error.message);}}
  if(runId){try{const run=await api(`api/runs/${runId}`);state.dirty=!Object.entries(briefData()).every(([k,v])=>JSON.stringify(v)===JSON.stringify(run.request.brief[k]))||state.curriculum?.sha256!==run.request.curriculum?.sha256||JSON.stringify(state.materials)!==JSON.stringify(run.request.materials);renderRun(run);if(state.dirty)$('runBadge').textContent='ПАРАМЕТРЫ ИЗМЕНЕНЫ';if(run.status==='running')poll(runId);}catch{notify('Сохранённый запуск недоступен. Параметры формы восстановлены.');}}
}
initialize();
