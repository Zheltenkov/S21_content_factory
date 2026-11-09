/* Content stage in the same chat: what the generator received, the style, title, tasks with reasons, theory, introduction,
   materials, the whole text, the critics and the README. Uses the chat helpers (h, assistant, chip, api, post, S,
   render…) at call time; loaded before chat.js. Every text is inserted as text, never as HTML. */
'use strict';

const Content = (() => {
  const PART_STATUS = {draft: ['Черновик', 'warn'], approved: ['Согласовано', 'ok'], planned: ['Запланирован', 'grey']};
  const MATERIAL_KIND = {data: 'данные', template: 'заготовка', sample: 'образец формата', reference: 'справка'};
  // The two ways a project is told (content/styles/) and who named the one in use.
  const STYLE_NAME = {quest: 'Квест', legend: 'Рабочая легенда'};
  const CHOSEN_BY = {form: 'выбран в форме', chat: 'выбран вами в чате', assistant: 'предложен ассистентом'};
  // How the theory is given and «да / нет», by the names the server sends (content/models.py); the keys are what is stored.
  const presentation = (key) => (S.content.presentations || {})[key] || key;
  const yesNo = (key) => (S.content.yes_no || {})[key] || key;
  const PLAN_BY = {chat: 'выбрано вами', assistant: 'предложено ассистентом'};
  // What a field of a task is called in the README of the draft's style: the server names them (render.FIELD_WORDS), so
  // the card, a proposed edit and the whole text use one word for one field. These are the words of a draft begun before the styles.
  const CLASSIC_WORDS = {context: 'Ситуация и данные', steps: 'Что нужно сделать', expected: 'Что должно получиться', examples: 'Примеры «ввод → вывод»',
    constraints: 'Ограничения и условия', result: 'Формат сдачи', check: 'Проверка', transition: 'Переход к следующему заданию'};
  const taskWords = () => S.content.task_words || CLASSIC_WORDS;
  const layout = () => (S.content.style ? S.content.style.profile : 'classic');
  // A section of the theory and the frame, as the README of the style has them.
  const topicName = (n) => ({quest: `List ${n}`, legend: `Раздел ${n}`}[layout()] || `Раздел 2.${n}`);
  const FRAME_TITLE = {quest: 'Сцена, правила проекта и развязка', legend: 'Легенда, правила проекта и итог', classic: 'Глава 1 и заключение'};
  const FRAME_WORDS = {quest: ['Сцена · Chapter I', 'Развязка · Chapter IV'], legend: ['Легенда · до содержания', 'Итог'], classic: ['Введение', 'Заключение']};
  const taskHeading = (t) => (S.content.task_headings || {})[t.number] || `Задание ${t.number}. ${t.title}`;
  const texts = {};  // fetched documents (whole text, README preview), keyed by url and content version
  const url = (path = '') => `api/content/${encodeURIComponent(S.content.id)}${path}`;
  // A readable page for a document; the raw endpoints stay for downloads. «v» is not read by the page: it makes the
  // address one no browser has kept a copy of — until 07.10.2026 the page was served without «no-cache», and a
  // browser that opened it before goes on showing its old copy at the old address.
  const view = (doc) => `document?run=${encodeURIComponent(S.content.id)}&doc=${doc}&v=7`;
  const active = () => Boolean(S.content && S.content.exists);
  const busy = () => Boolean(S.content && S.content.busy);

  async function load(runId) {
    try { return await api(`api/content/${encodeURIComponent(runId)}`); } catch { return null; }
  }
  function tag([label, tone]) { return h('span', {class: `tag ${tone}`, text: label}); }
  function planned(number) { return S.content.handoff.tasks.find((t) => t.number === number); }
  function outcomeTexts(numbers) { return numbers.map((n) => S.content.handoff.outcomes[n - 1]).filter(Boolean); }
  function rows(pairs) {
    return pairs.filter(([, value]) => value && (!Array.isArray(value) || value.length)).map(([label, value, numbered]) =>
      h('div', {class: 'card-row'}, h('span', {class: 'section-label', text: label}),
        h('div', {class: 'task-sub', text: Array.isArray(value) ? value.map((item, i) => `${numbered ? `${i + 1}.` : '•'} ${item}`).join('\n') : value})));
  }
  function editButton(prefix) { return h('button', {type: 'button', class: 'button small', text: 'Поправить…', onClick: () => addToComposer(prefix)}); }
  function head(title, status, ...extra) {
    return h('div', {class: 'card-head'}, h('span', {class: 'title', text: title}), ...extra, status ? tag(PART_STATUS[status]) : null);
  }
  function linkList(links) {
    return h('ul', {class: 'link-list'}, links.map((link) => h('li', {},
      h('a', {href: link.url, target: '_blank', rel: 'noopener noreferrer', text: link.title}), link.why ? ` — ${link.why}` : '')));
  }

  /* ---------- parts ---------- */
  function handoffCard() {
    const list = S.content.handoff_card || [];
    return h('div', {class: 'card'}, head('Что получил генератор из замысла'),
      list.map((row) => h('div', {class: 'card-row handoff-row'},
        h('div', {class: 'chips'}, h('strong', {text: row.label}), h('span', {class: row.ok ? 'tag ok' : 'tag warn', text: row.value})),
        (row.items || []).length ? h('div', {class: 'task-sub', text: row.items.map((item) => `• ${item}`).join('\n')}) : null)),
      h('div', {class: 'legend', text: 'Посчитано кодом по согласованной версии. Файлы «для проверяющего» видит только критик. Переписка чата замысла не передаётся — она уже внесена в задание.'}));
  }
  function styleCard(st) {
    const stale = Boolean(st.written_for) && st.written_for !== st.profile;
    return h('div', {class: 'card'},
      head(`Стиль проекта: ${STYLE_NAME[st.profile] || st.profile}`, st.status, h('span', {class: 'hint', text: CHOSEN_BY[st.chosen_by] || ''})),
      // What the assistant wrote is shown as the reasons for this style only while it was written for it.
      rows([stale ? ['Стиль сменили', `Обоснование и сюжет ниже написаны для стиля «${(STYLE_NAME[st.written_for] || st.written_for).toLowerCase()}». Ассистент может написать их заново — или поправьте сюжет в чате.`]
        : ['Почему этот стиль', st.reason || 'Стиль и сюжет приняты вами; ассистент этот стиль не обосновывал.'],
      [stale ? 'Сюжет прежнего стиля' : 'Сюжет — по нему пишутся все части', st.story], ['Вопросы по сюжету — ответьте в чате', st.questions, true]]),
      h('div', {class: 'card-foot'}, editButton('Стиль и сюжет: '),
        h('span', {class: 'hint', text: 'Квест — сцена, персонаж и квесты, как в бассейне. Рабочая легенда — одна ситуация в начале и задания без сюжета.'})));
  }
  function summaryCard(s) {
    // The titles the assistant offered, while the title is not agreed: the one in force is the head of the card.
    const offered = s.status === 'draft' ? (s.title_options || []) : [];
    return h('div', {class: 'card'}, head(s.title, s.status),
      rows([...(offered.length ? [['Варианты названия — выбрать кнопкой под чатом', offered, true]] : []), ['Аннотация', s.annotation]]),
      h('div', {class: 'card-foot'}, editButton('Название и аннотация: ')));
  }
  function planCard(pl) {
    // The reasons and the sample are shown as said of this presentation only while they were written for it.
    const stale = Boolean(pl.written_for) && pl.written_for !== pl.presentation;
    const sample = pl.sample ? h('div', {class: 'card-row'},
      h('span', {class: 'section-label', text: stale ? `Пример — для варианта «${presentation(pl.written_for)}»` : 'Как это будет выглядеть — пример одного раздела'}),
      h('pre', {class: 'material-text', text: pl.sample})) : null;
    return h('div', {class: 'card'},
      head(`Подача теории: ${presentation(pl.presentation)}`, pl.status, h('span', {class: 'hint', text: PLAN_BY[pl.chosen_by] || ''})),
      rows([stale ? ['Вариант сменили', `Обоснование и пример ниже написаны для варианта «${presentation(pl.written_for)}».${pl.status === 'draft' ? ' Пример для выбранного — кнопкой под чатом.' : ''}`]
        : ['Почему так', pl.reason || 'Вариант выбран вами; ассистент его не обосновывал.']]),
      sample,
      rows([['Интернет у участников во время проекта', pl.internet === 'no' ? 'нет — ссылки не ищем и не показываем' : 'есть — к разделам ищем ссылки'],
        ['Выдержка из первоисточника на английском', pl.english === 'yes' ? 'да — её найдёт поиск, код проверит, что она стоит на странице дословно' : 'нет'],
        ['Вопросы — ответьте в чате', pl.questions, true]]),
      h('div', {class: 'card-foot'}, editButton('Подача теории: '),
        h('span', {class: 'hint', text: 'Короткая глава — справка и ссылки. Конспект — текст, по которому можно работать без поиска: в README или в файле материалов.'})));
  }
  function rationaleNode(r, open) {
    if (!r) return null;
    const items = [['Замысел и ОР', r.concept], ['Почему сдача доказывает ОР', r.evidence], ['Что участник решает сам', r.choice],
      ['Как проверит пир', r.peer], ['Связь с другими заданиями', r.links], ['Допущения', r.assumptions]].filter(([, v]) => v);
    return h('div', {class: 'card-row'}, h('details', {class: 'rationale', open},
      h('summary', {text: 'Почему такое задание — пояснение модели, не доказательство'}),
      h('dl', {}, items.map(([label, value]) => [h('dt', {text: label}), h('dd', {text: value})]))));
  }
  function taskCard(t) {
    const plan = planned(t.number) || {hours: 0, outcome_numbers: [], optional: false};
    const materials = S.content.materials.filter((m) => m.tasks.includes(t.number)).map((m) => m.path);
    const w = taskWords();
    const goal = layout() === 'classic' ? 'Цель' : 'Цель — для методолога и критика, в README не выводится';
    // A quest's statement is one paragraph after the words the code puts; a task written in the other style stands apart.
    const steps = layout() === 'quest' ? `${w.steps} · после «Получен Quest N.»` : w.steps;
    const apart = t.written_in && S.content.style && t.written_in !== S.content.style.profile
      ? h('span', {class: 'tag warn', text: `написано в стиле «${(STYLE_NAME[t.written_in] || t.written_in).toLowerCase()}»`}) : null;
    return h('div', {class: 'card'},
      head(taskHeading(t), t.status, apart, plan.optional ? h('span', {class: 'tag info', text: 'Бонус'}) : null, h('span', {class: 'hint', text: `${num(plan.hours)} ч`})),
      h('div', {class: 'card-row'}, h('ul', {class: 'or-list', 'aria-label': 'Образовательные результаты задания'}, outcomeTexts(plan.outcome_numbers).map((text) => h('li', {text})))),
      rows([[w.context, t.context], [goal, t.goal], [steps, t.steps, layout() !== 'quest'], [w.expected, t.expected],
        [`${w.examples} · ⏎ — новая строка`, t.examples || []],
        [w.constraints, t.constraints], [w.result, t.result], [w.check, t.check], [w.transition, t.transition],
        ['Материалы', materials.join(', ')], ['Пункты для пира · check-list.yml', t.peer_checks],
        ['Что понадобится понять — из этого соберётся теория', t.needs]]),
      rationaleNode(t.rationale, t.status === 'draft'),
      h('div', {class: 'card-foot'}, editButton(`Задание ${t.number}: `)));
  }
  function theoryCard(th) {
    const plan = S.content.theory_plan;
    const offline = Boolean(plan && plan.internet === 'no');
    const quoted = th.excerpt && !th.excerpt.removed ? th.excerpt : null;
    // Where the summaries lie and which text the README shows, by the plan of the theory.
    const where = plan && plan.presentation !== 'chapter'
      ? h('span', {class: 'hint', text: `подача: ${presentation(plan.presentation)}${S.content.theory_file ? ` · materials/${S.content.theory_file}` : ''}`}) : null;
    return h('div', {class: 'card'}, head(`Теоретический блок · ${th.topics.length} ${plural(th.topics.length, 'раздел', 'раздела', 'разделов')}`, th.status, where),
      th.topics.map((t) => h('div', {class: 'card-row'},
        h('div', {class: 'chips'}, h('strong', {text: `${layout() === 'classic' ? `2.${t.number}` : topicName(t.number)}. ${t.title}`}), h('span', {class: 'hint', text: `задания ${t.tasks.join(', ')}`})),
        h('div', {class: 'task-sub', text: t.text}),
        t.notes ? h('details', {class: 'goals'}, h('summary', {text: 'Конспект'}), h('div', {class: 'task-sub', text: t.notes})) : null,
        t.links.length ? linkList(t.links) : offline ? null : h('p', {class: 'hint', text: `Ссылок нет. Ключевые слова: ${t.keywords.join(', ')}`}),
        quoted && quoted.topic === t.number ? h('div', {class: 'stack tight'},
          h('span', {class: 'section-label', text: 'Первоисточник (на английском) — дословно со страницы'}),
          h('blockquote', {class: 'task-sub', lang: 'en', text: quoted.quote}),
          h('a', {href: quoted.url, target: '_blank', rel: 'noopener noreferrer', text: quoted.title})) : null,
        t.question ? h('p', {class: 'task-sub', text: `Вопрос к практике: ${t.question}`}) : null)),
      th.note ? h('div', {class: 'card-row'}, h('p', {class: 'hint', text: th.note})) : null,
      h('div', {class: 'card-foot'}, editButton(layout() === 'classic' ? 'Раздел 2.' : `${topicName('').trim()} `),
        h('span', {class: 'hint', text: offline ? 'У участников нет интернета: ссылки не искали.' : 'Ссылки — только со страниц, которые открылись, или присланные вами.'})));
  }
  function frameCard(f) {
    const [opening, closing] = FRAME_WORDS[layout()];
    return h('div', {class: 'card'}, head(FRAME_TITLE[layout()], f.status),
      rows([[opening, f.introduction], ['Инструкция (правила школы код добавит сам)', f.instruction], [closing, f.conclusion]]),
      h('div', {class: 'card-foot'}, editButton('Введение: ')));
  }
  function checklistCard(cl) {
    const tasks = [...S.content.tasks].sort((a, b) => a.number - b.number);
    return h('div', {class: 'card'},
      head('Чек-лист проверки · check-list.yml', null, cl.disclaimer ? h('span', {class: 'tag ok', text: 'дисклеймер есть'}) : h('span', {class: 'tag warn', text: 'нет дисклеймера'}),
        h('a', {href: view('checklist'), target: '_blank', rel: 'noopener', text: 'Открыть файл'})),
      rows([['Дисклеймер', cl.disclaimer || 'Не задан. Пришлите текст в чат: «Дисклеймер чек-листа: …»']]),
      h('div', {class: 'card-row'}, h('span', {class: 'section-label', text: 'Вопросы — пункты для пира из заданий'}),
        tasks.map((t) => h('div', {class: 'task-sub', text: `${taskHeading(t)}\n${t.peer_checks.map((item) => `• ${item}`).join('\n')}`}))),
      h('div', {class: 'card-row'}, h('details', {class: 'goals'}, h('summary', {text: 'Вступление и правила проверки — стандарт школы'}),
        h('pre', {class: 'material-text', text: `${cl.introduction}\n\n${cl.guidelines}`}))),
      h('div', {class: 'card-foot'}, editButton('Чек-лист: ')));
  }
  function materialCard(m) {
    const lines = m.content.split('\n');
    const body = m.content ? (lines.length > 14
      ? h('details', {class: 'goals'}, h('summary', {text: `Содержимое · ${lines.length} строк`}), h('pre', {class: 'material-text', text: m.content}))
      : h('pre', {class: 'material-text', text: m.content})) : h('p', {class: 'hint', text: 'Ещё не подготовлен.'});
    return h('div', {class: 'card'}, head(`materials/${m.path}`, m.status, h('span', {class: 'hint', text: MATERIAL_KIND[m.kind] || m.kind})),
      rows([['Зачем участнику', m.purpose], ['Задания', m.tasks.map((n) => `задание ${n}`).join(', ')], ['Чего внутри быть не должно', m.not_contains], ['Заметка', m.note]]),
      h('div', {class: 'card-row'}, body),
      h('div', {class: 'card-foot'}, editButton(`${m.path}: `)));
  }
  function documentCard(title, path, note) {
    const key = `${path}:${S.content.messages.length}:${S.content.busy}`;
    if (!(key in texts)) {
      texts[key] = null;
      fetch(url(path)).then((r) => r.text()).then((text) => { texts[key] = text; render(); }).catch(() => { texts[key] = 'Не удалось загрузить текст.'; render(); });
    }
    return h('div', {class: 'card'}, head(title, null, h('a', {href: view(path === '/text' ? 'text' : 'preview'), target: '_blank', rel: 'noopener', text: 'Открыть отдельно'})),
      note ? h('div', {class: 'card-row'}, h('p', {class: 'hint', text: note})) : null,
      h('div', {class: 'card-row'}, texts[key] === null ? h('span', {class: 'hint', text: 'Загружаю…'})
        : h('details', {class: 'goals', open: true}, h('summary', {text: 'Текст'}), h('pre', {class: 'document-text', text: texts[key]}))));
  }
  function reviewCard() {
    const review = S.content.review;
    if (!review) return null;
    const important = review.findings.filter((f) => f.severity === 'important');
    const takeAll = important.length > 1 ? h('button', {type: 'button', class: 'button small', text: `Учесть все важные (${important.length})`,
      onClick: () => submit(`Учти замечания ${important.map((f) => f.number).join(', ')}.`)}) : null;
    return h('div', {class: 'card'}, head(`Проверка критиков · ${review.findings.length}`, null, S.content.review_stale ? h('span', {class: 'tag warn', text: 'текст менялся после проверки'}) : null),
      h('div', {class: 'card-row'}, h('p', {class: 'task-sub', text: [review.summary, review.facts_summary].filter(Boolean).join('\n')}),
        h('p', {class: 'hint', text: `Модели: ${review.models.join(', ')}. Баллов нет, замечания — советы; решаете вы.`})),
      review.findings.map((f) => h('div', {class: 'card-row finding'},
        h('div', {class: 'chips'}, h('strong', {text: `${f.number}.`}), tag(f.severity === 'important' ? ['Важно', 'del'] : ['Совет', 'info']),
          h('span', {class: 'tag grey', text: f.source === 'facts' ? 'факты · поиск' : 'методика'}), f.part ? h('span', {class: 'hint', text: partLabel(f.part)}) : null),
        h('strong', {text: f.message}), f.suggestion ? h('p', {text: `Что сделать: ${f.suggestion}`}) : null,
        f.sources.length ? sourceLinks(f.sources) : null,
        h('div', {class: 'actions'}, h('button', {type: 'button', class: 'reply', text: 'Учесть', onClick: () => submit(`Учти замечание ${f.number}: ${f.message}`)})))),
      review.notes.map((text) => h('div', {class: 'card-row'}, h('p', {class: 'hint', text}))),
      takeAll ? h('div', {class: 'card-foot'}, takeAll) : null);
  }
  function partLabel(part) {
    const [kind, key] = [part.split(':')[0], part.slice(part.indexOf(':') + 1)];
    return {style: 'стиль проекта', summary: 'название и аннотация', task: `${layout() === 'quest' ? 'Quest' : 'задание'} ${key}`, theory: 'теория', theory_plan: 'подача теории', topic: topicName(key).toLowerCase(), frame: 'введение, инструкция, заключение', material: `материал ${key}`, checklist: 'чек-лист'}[kind] || part;
  }
  function partNode(part) {
    const [kind, key] = [part.split(':')[0], part.slice(part.indexOf(':') + 1)];
    const c = S.content;
    if (kind === 'handoff') return handoffCard();
    if (kind === 'style') return c.style ? styleCard(c.style) : null;
    if (kind === 'summary') return c.summary ? summaryCard(c.summary) : null;
    if (kind === 'task') { const t = c.tasks.find((x) => x.number === Number(key)); return t ? taskCard(t) : null; }
    if (kind === 'theory_plan') return c.theory_plan ? planCard(c.theory_plan) : null;
    if (kind === 'theory') return c.theory ? theoryCard(c.theory) : null;
    if (kind === 'frame') return c.frame ? frameCard(c.frame) : null;
    if (kind === 'checklist') return c.checklist ? checklistCard(c.checklist) : null;
    if (kind === 'full') return documentCard('Весь материал целиком', '/text', 'Так его увидят критики: с пояснениями к заданиям и служебными полями материалов. Участник этого текста не получает.');
    if (kind === 'review') return reviewCard();
    if (kind === 'readme') return documentCard('README.md', '/preview', c.style ? `Собран кодом в раскладке стиля «${STYLE_NAME[c.style.profile].toLowerCase()}» из согласованного текста.` : 'Собран кодом по шаблону школы из согласованного текста.');
    const m = c.materials.find((x) => x.path === key);
    return m ? materialCard(m) : h('p', {class: 'hint', text: `Материал ${key} убран.`});
  }

  /* ---------- messages ---------- */
  function patchValue(value) { return Array.isArray(value) ? value.map((item, i) => `${i + 1}. ${item}`).join('\n') : String(value || ''); }
  // A style is stored by its key; the methodologist reads its name.
  const NAMED = new Set(['profile', 'written_for', 'written_in', 'presentation', 'internet', 'english']);
  function shown(patch, value) {
    if (patch.field === 'presentation') return presentation(value);
    if (patch.field === 'internet' || patch.field === 'english') return yesNo(value);
    return NAMED.has(patch.field) ? (STYLE_NAME[value] || value) : patchValue(value);
  }
  function patchCard(message) {
    const editing = S.editing === message.id && message.status === 'pending';
    const [statusText, tone] = {pending: ['Ожидает решения', 'warn'], applied: ['Применено', 'ok'], rejected: ['Отклонено', 'grey']}[message.status] || ['', 'grey'];
    const list = message.patches.map((patch, i) => h('div', {class: 'card-row'},
      h('div', {class: 'patch-head'}, h('span', {class: 'label', text: patch.label}), h('span', {class: 'grow'}),
        patch.edited ? h('span', {class: 'tag grey', text: 'исправлено методологом'}) : null),
      patch.before === '' || (Array.isArray(patch.before) && !patch.before.length) ? h('div', {class: 'was-empty', text: 'было: пусто'})
        : h('del', {class: 'diff del'}, h('span', {'aria-hidden': 'true', text: '−'}), h('span', {text: shown(patch, patch.before)})),
      // A quotation is not typed over: its change (taken out, put back) is shown and has no box, whatever else is being edited.
      editing && patch.field !== 'excerpt' ? h('textarea', {id: `cpatch-${message.id}-${i}`, class: 'patch-edit', rows: String(Math.min(12, patchValue(patch.after).split('\n').length + 1)),
        value: Array.isArray(patch.after) ? patch.after.join('\n') : NAMED.has(patch.field) ? shown(patch, patch.after) : patch.after})
        : h('ins', {class: 'diff ins'}, h('span', {'aria-hidden': 'true', text: '+'}), h('span', {text: patch.after === '' ? 'убрать' : shown(patch, patch.after)})),
      patch.reason ? h('div', {class: 'reason', text: patch.reason}) : null));
    const count = message.patches.length;
    let foot;
    if (editing) {
      foot = h('div', {class: 'card-foot'}, h('button', {type: 'button', class: 'button primary small', text: 'Применить с исправлениями', onClick: () => decide(message, 'apply', collect(message))}),
        h('button', {type: 'button', class: 'button small', text: 'Отмена', onClick: () => { S.editing = null; render(); }}));
    } else if (message.status === 'pending') {
      foot = h('div', {class: 'card-foot'}, h('button', {type: 'button', class: 'button primary small', text: `Применить ${count} ${plural(count, 'правку', 'правки', 'правок')}`, onClick: () => decide(message, 'apply')}),
        h('button', {type: 'button', class: 'button small', text: 'Изменить', onClick: () => { S.editing = message.id; render(); }}),
        h('button', {type: 'button', class: 'button small ghost', text: 'Отклонить', onClick: () => decide(message, 'reject')}));
    } else if (message.status === 'applied') {
      foot = h('div', {class: 'card-foot ok'}, icon('check'), h('span', {class: 'grow', text: 'Текст обновлён. Дальше — подсказки внизу чата.'}),
        h('button', {type: 'button', class: 'link-button', text: 'Отменить', onClick: () => decide(message, 'revert')}));
    } else {
      foot = h('div', {class: 'card-foot'}, h('span', {class: 'grow hint', text: 'Правки отклонены — текст не изменился.'}),
        h('button', {type: 'button', class: 'link-button', text: 'Вернуть', onClick: () => decide(message, 'revert')}));
    }
    return h('div', {class: 'card'}, h('div', {class: 'card-head'}, h('span', {class: 'title', text: `Правки текста · ${count}`}), statusText ? tag([statusText, tone]) : null), list, foot);
  }
  function collect(message) {
    const values = {};
    message.patches.forEach((patch, i) => {
      const el = document.getElementById(`cpatch-${message.id}-${i}`);
      if (!el) return;
      const value = Array.isArray(patch.after) ? el.value.split('\n').map((s) => s.trim()).filter(Boolean) : el.value;
      // A style is shown by its name in the box: left as it was, it is no edit (the server reads either name).
      const before = NAMED.has(patch.field) ? shown(patch, patch.after) : patch.after;
      if (JSON.stringify(value) !== JSON.stringify(before)) values[i] = value;
    });
    return values;
  }
  function messageNode(message, latestOfPart) {
    if (message.role === 'methodologist') return h('div', {class: 'bubble', text: message.text});
    if (message.role === 'system') return h('div', {class: 'divider', text: message.text});
    // How the chat understood the message, in the same words as the concept chat (KIND_LABEL of chat.js).
    const understood = KIND_LABEL[message.kind] ? ` · понял как: ${KIND_LABEL[message.kind]}` : '';
    const meta = (message.model ? `контент · ${message.model}` : 'контент') + understood;
    const extras = [message.patches.length ? patchCard(message) : null,
      message.notes.length ? h('ul', {class: 'note-list'}, message.notes.map((text) => h('li', {text}))) : null];
    if (message.part) {
      // A part is shown in full where it was last written; earlier versions stay as a line in the history.
      return latestOfPart ? assistant(meta, h('p', {class: 'prose', text: message.text}), partNode(message.part), ...extras)
        : assistant(meta, h('p', {class: 'hint', text: `${message.text} Новая версия — ниже.`}));
    }
    return assistant(meta, h('p', {class: 'prose', text: message.text}), ...extras);
  }
  function items() {
    if (!active()) return [];
    const last = new Map();
    S.content.messages.forEach((m) => { if (m.part) last.set(m.part, m.id); });
    return S.content.messages.map((m) => ({at: m.at, order: 6, node: () => messageNode(m, last.get(m.part) === m.id)}));
  }

  /* ---------- next step ---------- */
  function downloads() {
    if (!active()) return [];
    const links = [];
    if (S.content.summary) links.push(h('a', {class: 'chip-action ghost', href: view('text'), target: '_blank', rel: 'noopener', text: 'Весь текст'}));
    if (S.content.assembled_at && S.content.ready) {
      links.push(h('a', {class: 'chip-action ghost', href: url('/bundle'), text: 'Скачать комплект (.zip)'}),
        h('a', {class: 'chip-action ghost', href: url('/readme'), text: 'README.md'}),
        h('a', {class: 'chip-action ghost', href: url('/check-list'), text: 'check-list.yml'}));
    }
    return links;
  }
  function checksNode() {
    const checks = (S.content && S.content.checks) || [];
    if (!checks.length) return null;
    return h('div', {class: 'stack tight'}, h('span', {class: 'section-label', text: 'Проверки кодом'}),
      checks.map((c) => h('div', {class: 'claim'}, tag(c.severity === 'block' ? ['Исправить', 'del'] : ['Внимание', 'warn']), h('span', {text: c.message}))));
  }
  function generationPanel() {
    const version = (S.content.content_version || '0.0').split('.').map(Number);
    if (version[0] < 1 && version[1] < 11) return null;
    const preferences = S.content.handoff.generation || {};
    const variety = h('select', {}, ['restrained', 'balanced', 'expressive'].map((key, i) =>
      h('option', {value: key, selected: (preferences.variety || 'balanced') === key, text: ['Сдержанная', 'Сбалансированная', 'Выразительная'][i]})));
    const controls = {};
    const labels = {temperature: 'Температура', top_p: 'Top P', frequency_penalty: 'Штраф за частоту', presence_penalty: 'Штраф за повторное появление'};
    for (const [name, label] of Object.entries(labels)) controls[name] = h('input', {type: 'number', step: '0.05',
      min: name === 'top_p' ? '0.01' : name.includes('penalty') ? '-2' : '0', max: name === 'top_p' ? '1' : '2',
      value: preferences[name] ?? '', placeholder: 'По умолчанию', 'aria-label': label});
    return h('details', {class: 'generation-panel'}, h('summary', {text: 'Настройки подачи и генерации'}),
      h('label', {}, 'Подача текста', variety), Object.entries(labels).map(([name, label]) => h('label', {}, label, controls[name])),
      h('p', {class: 'hint', text: 'Температура и Top P — альтернативы: заполните одно из них. Пустое поле сохраняет настройки провайдера. Поддержка числовых параметров экспериментальная. Изменения действуют на следующие генерации.'}),
      h('button', {type: 'button', class: 'button small', text: 'Сохранить настройки', onClick: () => act(async () => {
        const values = {variety: variety.value};
        for (const [name, input] of Object.entries(controls)) if (input.value !== '') values[name] = Number(input.value);
        S.content = await post(url('/generation'), values);
        render();
      })}));
  }
  function nextStep() {
    const step = S.content.next_step;
    if (busy()) {
      return assistant('работаю', h('div', {class: 'progress'}, h('span', {class: 'spinner', 'aria-hidden': 'true'}), h('span', {text: `${S.content.busy}…`})),
        h('p', {class: 'hint', text: step.text}));
    }
    const chips = [];
    if (step.action) chips.push(chip(step.button, () => advance(step.action, step.number, step.button), 'primary'));
    (step.alternatives || []).forEach((a) => chips.push(chip(a.button, () => advance(a.action, a.number === undefined ? null : a.number, a.button))));
    if (step.edit_hint) chips.push(chip('Поправить текст', () => askForEdits(step.edit_hint)));
    const version = (S.content.content_version || '0.0').split('.').map(Number);
    if (S.content.exists && !S.content.assembled_at && (version[0] >= 1 || version[1] >= 11)) {
      chips.push(chip('Подготовить весь черновик', () => advance('prepare_draft', null, 'Подготовить весь черновик')));
      if (S.content.batch_prepared) chips.push(chip('Согласовать комплект целиком', () => advance('approve_bundle', null, 'Согласовать комплект целиком')));
    }
    chips.push(...downloads());
    if (S.content.exists) chips.push(h('a', {class: 'chip-action ghost',
      href: `evaluation?run=${encodeURIComponent(S.content.id)}${S.thread ? `&thread=${encodeURIComponent(S.thread.id)}` : ''}`,
      text: 'Проверка по 56 критериям', target: '_blank', rel: 'noopener'}));
    const run = latest();
    if (run) chips.push(h('a', {class: 'chip-action ghost', href: `api/runs/${run.id}/export/md`, text: 'Скачать замысел'}));
    return assistant('что дальше',
      h('p', {class: 'prose'}, h('b', {text: `${step.title}. `}), step.text),
      checksNode(),
      h('div', {class: 'suggestions', role: 'group', 'aria-label': 'Что можно сделать дальше'}, chips),
      generationPanel(),
      h('small', {class: 'route', text: step.route}));
  }
  function steps() {
    if (!active()) return null;
    const c = S.content;
    const approved = (part) => Boolean(part && part.status === 'approved');
    const tasksDone = c.handoff.tasks.every((p) => approved(c.tasks.find((t) => t.number === p.number)));
    const list = [...(c.styled ? [['Стиль', approved(c.style)]] : []), ['Название', approved(c.summary)], ['Задания и материалы', tasksDone && c.materials.every((m) => m.status === 'approved')],
      ['Теория и введение', approved(c.theory) && approved(c.frame)],
      ['Критики', Boolean(c.review)], ['README', Boolean(c.assembled_at)]];
    return {list, current: list.findIndex(([, done]) => !done)};
  }

  /* ---------- actions ---------- */
  async function refresh() {
    const run = latest();
    S.content = run && run.status === 'approved' ? await load(run.id) : null;
  }
  function advance(action, number, label) {
    waiting(label, 'Запускаю…');
    return act(async () => {
      try {
        const body = {action, number: number === undefined ? null : number};
        S.content = action === 'start' ? await post(`api/content/${encodeURIComponent(latest().id)}/start`)
          : ['prepare_draft', 'approve_bundle'].includes(action) ? await post(url(action === 'prepare_draft' ? '/prepare-draft' : '/approve-bundle'), {})
          : await post(url('/advance'), body);
      } finally { done(); }
      render(true); schedulePoll();
    }).finally(() => { if (S.sending) { done(); render(); } });
  }
  function submit(text) {
    // A finding becomes a chat message, so the reply and its patch card follow the usual path.
    const box = $('#composerText');
    box.value = text; autoGrow();
    $('#composer').requestSubmit();
  }
  async function send(text) {
    S.content = await post(url('/messages'), {text});
  }
  function decide(message, verdict, values) {
    return act(async () => {
      S.content = await post(url(`/messages/${message.id}/decision`), {verdict, values: values || {}});
      S.editing = null; render(); schedulePoll();
    });
  }
  async function poll() {
    const signature = (c) => (c ? `${c.busy}:${c.messages.length}` : '');
    const before = signature(S.content);
    const next = await load(S.content.id);
    if (next) { S.content = next; if (signature(next) !== before) render(); }
  }

  return {active, busy, load, refresh, items, nextStep, steps, send, poll};
})();
