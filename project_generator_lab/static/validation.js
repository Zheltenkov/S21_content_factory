/* Evidence-bound review UI. Every source/quote is inserted as text, never HTML. */
const reviewAreas = {outcomes:'Образовательные результаты', audience:'Аудитория и подготовка', constraints:'Условия и ограничения', program:'Место в программе', materials:'Материалы', independence:'Самостоятельность и P2P'};
const checkLabels = {confirmed:'Подтверждено на уровне замысла', problem:'Есть проблема', needs_information:'Нужны данные', deferred:'Проверка на следующем этапе', not_applicable:'Неприменимо'};
const readLabels = {text_extracted:'Текст извлечён', partial:'Есть непрочитанные части', failed:'Не удалось прочитать', legacy_unknown:'Старый импорт — охват не подтверждён'};

function latestDecision(run, index) {
  return (run.finding_decisions || []).findLast(d => d.finding_index === index);
}
function remainingBlockers(run) {
  return (run.review?.findings || []).filter((f,i) => f.severity === 'blocker' && (f.origin === 'system' || latestDecision(run,i)?.verdict !== 'dismissed'));
}
function showSourceRegistry(target, run) {
  if (!run.source_registry?.length) return;
  target.append(node('h3','Что прочитано и передано модели'));
  for (const source of run.source_registry) {
    const item = node('details',null,'source-item');
    item.append(node('summary',`${source.name} · ${readLabels[source.read_status] || source.read_status}`));
    item.append(node('p',`${roles[source.role] || source.role} · передано ${source.selected_count} из ${source.extracted_count} фрагментов или записей`));
    bullets(item,'Непрочитанные части',source.unread_locations || []);
    bullets(item,'Ограничения',source.limitations || []);
    item.append(node('p',`SHA-256: ${source.sha256 || 'Не получен — загрузка не завершена'}`,'hint'));
    target.append(item);
  }
}
function renderInputs(target, run) {
  const card = run.input_card;
  if(!card && run.prompt_version==='0.1'){target.append(node('p','Этот запуск создан предыдущей версией без отдельной карточки входов. Для её подготовки создайте новую версию.'));return;}
  if (!card) target.append(node('p',run.input_findings?.length ? 'Можно продолжить разбор по доступным данным. Непрочитанные источники останутся отмечены в реестре.' : 'Карточка понимания входов появится после разбора источников.'));
  if (card) {
    section(target,'Как поняты входные данные',card.summary);
    target.append(node('p','Проверьте факты и предложения. Уточнения внесите в форму слева и подготовьте новую версию. Если карточка верна, подтвердите входы кнопкой ниже.','hint'));
    for (const area of ['outcomes','audience','constraints','program','materials']) {
      target.append(node('h3',reviewAreas[area]));
      for (const claim of card.claims.filter(c => c.area === area)) {
        const item=node('article',null,'decision');
        item.append(node('span',labels[claim.kind] || 'НЕИЗВЕСТНО',`label ${claim.kind}`),node('p',claim.text));
        sourceLinks(item,claim.evidence_ids.filter(id=>(run.evidence || []).some(e=>e.id===id)));target.append(item);
      }
    }
    bullets(target,'Вопросы методологу',card.questions.map(q => `${q.blocking ? 'Требует внимания: ' : ''}${q.question} — ${q.why}`));
  }
  for (const f of run.input_findings || []) {
    const item=node('article',null,'finding warning');
    item.append(node('span','ПРЕДУПРЕЖДЕНИЕ','label'),node('strong',f.message),node('p',f.suggestion));target.append(item);
  }
  showSourceRegistry(target,run);
  if (run.input_approved_at) target.append(node('p',`Карточка согласована ${new Date(run.input_approved_at).toLocaleString('ru-RU')}`,'hint'));
}
function conceptLinks(target, locations) {
  for (const address of locations || []) {
    const button=node('button',`В замысле: ${address}`,'source-button');button.type='button';
    button.onclick=()=>{state.tab=address.startsWith('decisions')?'decisions':'concept';renderTab();const el=document.getElementById(`concept-${address}`);if(el)el.scrollIntoView({block:'center',behavior:'smooth'});else $('tabContent').scrollIntoView({block:'start',behavior:'smooth'});};
    target.append(button);
  }
}
function renderGroundedReview(target, run) {
  const review=run.review;
  if (!review) {target.append(node('p',run.status === 'failed' ? 'Проверка не завершена; частичный результат сохранён.' : 'Проверка начнётся после согласования входов и подготовки замысла.'));return;}
  // Keep the saved conclusion intact; omit repeated workflow boilerplate in the preview.
  const summary=review.summary.replace('В демонстрационном режиме они не блокируют согласование.','').trim();
  section(target,'Заключение проверяющего',summary);
  if (review.outcomes?.length) {
    target.append(node('h3','ОР → действие → сдача → P2P'));
    const outcomes=run.request.brief.outcomes.split('\n').filter(s=>s.trim());
    for (const check of review.outcomes) {
      const item=node('article',null,'task');
      item.append(node('h4',`ОР ${check.outcome_number}. ${outcomes[check.outcome_number-1] || ''}`),node('span',checkLabels[check.status],'label'),node('p',check.explanation));
      if(check.required_action){const dl=node('dl');for(const [label,text] of [['Что требует ОР',check.required_action],['Что запланировано',check.planned_action],['Что наблюдает пир',check.observable_evidence],['Что ещё нужно',check.gap]]){if(text)dl.append(node('dt',label),node('dd',text));}item.append(dl);}
      for (const n of check.task_numbers) {
        const task=run.concept?.tasks[n-1];if(!task)continue;
        const dl=node('dl');
        for(const [label,text] of [[`Задание ${n}`,task.action],['Что сдаёт',task.deliverable],['Что видит пир',task.peer_check]])dl.append(node('dt',label),node('dd',text));
        item.append(dl);conceptLinks(item,[`tasks.${n}`]);
      }
      sourceLinks(item,check.evidence_ids);target.append(item);
    }
  }
  for (const check of review.checks || []) {
    const item=node('article',null,'decision');
    item.append(node('h4',reviewAreas[check.rule_id]),node('span',checkLabels[check.status],'label'),node('p',check.explanation));
    sourceLinks(item,check.evidence_ids);conceptLinks(item,check.concept_locations);target.append(item);
  }
  target.append(node('h3','Замечания'));
  if (!review.findings.length) target.append(node('p','Замечаний не найдено. Решение о согласовании принимает методолог.'));
  review.findings.forEach(finding=>{
    const item=node('article',null,'finding warning');
    item.append(node('span','РЕКОМЕНДАЦИЯ','label'),node('strong',finding.message));
    if(finding.consequence)section(item,'Почему это важно',finding.consequence);
    section(item,'Что изменить или уточнить',finding.suggestion);
    sourceLinks(item,finding.evidence_ids || []);conceptLinks(item,finding.concept_locations);
    // Feedback records remain in the saved run; this preview only displays findings.
    target.append(item);
  });
}
function renderVersions(target, run) {
  if (!run.request.parent_run_id) {target.append(node('p','Первая версия. Следующая подготовка сохранит этот запуск и покажет изменения.'));return;}
  const link=node('a','Открыть предыдущую версию','source-button');link.href=`?run=${encodeURIComponent(run.request.parent_run_id)}`;link.target='_blank';link.rel='noopener';target.append(link);
  if(run.baseline_approved_at)target.append(node('p',`Предыдущий замысел согласован ${new Date(run.baseline_approved_at).toLocaleString('ru-RU')}. Изменения требуют нового решения.`));
  if (!run.changes?.length) target.append(node('p','Входные данные не менялись. Замысел ещё не подготовлен либо совпадает с предыдущим.'));
  for(const change of run.changes || []) {
    const item=node('details',null,'source-item');item.append(node('summary',`Изменено: ${fieldLabels[change.location.replace('brief.','')] || change.location}`));
    section(item,'Было',change.before);section(item,'Стало',change.after);target.append(item);
  }
  target.append(node('p','Новая версия проверяется целиком: изменения могут влиять на связанные результаты и ограничения.','hint'));
}
async function confirmInputCard() {
  if(state.dirty || state.busy)return;
  const button=$('confirmInputsButton');button.disabled=true;
  try{const run=await api(`api/runs/${state.run.id}/confirm-inputs`,{method:'POST'});notify();state.tab='concept';renderRun(run);saveDraft();poll(run.id);}catch(error){notify(error.message);updateApproval();}
}
