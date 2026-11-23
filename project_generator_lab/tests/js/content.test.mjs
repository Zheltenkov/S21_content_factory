// What the content chat shows of the style a project is told in: the card of the style, a task named and labelled as
// its README will have it, the step of the route, and what the form sends. Run: node --test project_generator_lab/tests/js
import assert from 'node:assert/strict';
import {test} from 'node:test';

import {page} from './page.mjs';

// What the page's scripts return lives in their own sandbox: compared as data, not by the sandbox's own Array.
const plain = (value) => JSON.parse(JSON.stringify(value));

const task = {number: 1, title: 'Сумма', status: 'draft', context: 'Экран мигает.', goal: 'Читать ввод', steps: ['Создать программу src/sum.c.'],
  expected: ['Печатает сумму'], examples: ['3 5 → 8'], constraints: ['Без библиотек'], result: 'src/sum.c', check: 'Пир вводит свои числа.',
  transition: 'Дверь открылась.', peer_checks: ['`./sum`, ввод `3 5` — `8`'], needs: ['Ввод и вывод'], rationale: null};

// The words the server sends for a task's fields (content/render.py FIELD_WORDS); a draft begun before the styles sends its own.
const WORDS = {
  quest: {context: 'Сюжет квеста', steps: 'Формулировка', expected: 'Требования к результату', examples: 'Примеры «ввод → вывод»', constraints: 'Внимание', result: 'Сдать', check: 'Проверка', transition: 'Чем закончилась сцена'},
  legend: {context: 'Что создаёшь и с чем работаешь', steps: 'Шаги', expected: 'Должно получиться', examples: 'Примеры «ввод → вывод»', constraints: 'Условия', result: 'Результат', check: 'Проверка', transition: 'Зачем'},
};

function content(style, extra = {}) {
  return {exists: true, id: 'r1', busy: '', styled: Boolean(style), style, tasks: [task], materials: [], summary: null, theory: null, frame: null,
    ...(style ? {task_words: WORDS[style.profile]} : {}),
    handoff: {tasks: [{number: 1, hours: 2, outcome_numbers: [], optional: false}], outcomes: []},
    task_headings: style ? {1: style.profile === 'quest' ? 'Quest 1. Сумма' : 'Задание 1. Сумма'} : {},
    messages: [{id: 'm1', role: 'assistant', part: 'style', text: 'Предлагаю стиль.', patches: [], notes: [], model: 'm', kind: ''},
      {id: 'm2', role: 'assistant', part: 'task:1', text: 'Задание 1.', patches: [], notes: [], model: 'm', kind: ''}], ...extra};
}
const quest = {profile: 'quest', status: 'draft', chosen_by: 'form', reason: 'Есть сквозной сюжет серии.', story: 'ИИ Марк открывает двери.',
  questions: ['Как зовут персонажа?', 'Уместен ли юмор?']};

test('the card of the style says which it is, who chose it, why, the story and what is asked', () => {
  const js = page();
  js('S').content = content(quest);
  const [card] = js('Content').items().map((item) => item.node().textContent);
  assert.match(card, /Стиль проекта: Квест/);
  assert.match(card, /выбран в форме/);
  assert.match(card, /Почему этот стиль/); assert.match(card, /Есть сквозной сюжет серии\./);
  assert.match(card, /Сюжет — по нему пишутся все части/); assert.match(card, /ИИ Марк открывает двери\./);
  assert.match(card, /Вопросы по сюжету — ответьте в чате/); assert.match(card, /1\. Как зовут персонажа\?\n2\. Уместен ли юмор\?/);
  assert.match(card, /Черновик/);
  // The assistant's own choice of the other style, agreed; no questions — no row for them.
  js('S').content = content({...quest, profile: 'legend', chosen_by: 'assistant', status: 'approved', questions: []});
  const [legend] = js('Content').items().map((item) => item.node().textContent);
  assert.match(legend, /Стиль проекта: Рабочая легенда/); assert.match(legend, /предложен ассистентом/); assert.match(legend, /Согласовано/);
  assert.doesNotMatch(legend, /Вопросы по сюжету/);
});

test('a task is headed and its fields are named as the README of the style will have them', () => {
  const js = page();
  const card = (style) => { js('S').content = content(style); return js('Content').items()[1].node().textContent; };
  const inQuest = card(quest);
  for (const said of [/Quest 1\. Сумма/, /Сюжет квеста/, /Формулировка · после «Получен Quest N\.»/, /Требования к результату/, /Внимание/, /Сдать/,
    /Чем закончилась сцена/, /Примеры «ввод → вывод» · ⏎ — новая строка/, /• 3 5 → 8/, /Проверка/, /Цель — для методолога и критика, в README не выводится/]) assert.match(inQuest, said);
  const inLegend = card({...quest, profile: 'legend'});
  for (const said of [/Задание 1\. Сумма/, /Что создаёшь и с чем работаешь/, /Шаги/, /Должно получиться/, /Условия/, /Результат/, /Зачем/]) assert.match(inLegend, said);
  assert.doesNotMatch(inLegend, /Сюжет квеста|Формулировка/);
  // A draft begun before the styles: the words of the template of that time, and the task named as it always was.
  const before = card(null);
  for (const said of [/Задание 1\. Сумма/, /Ситуация и данные/, /Что нужно сделать/, /Что должно получиться/, /Формат сдачи/, /Переход к следующему заданию/]) assert.match(before, said);
  assert.doesNotMatch(before, /в README не выводится/);
  // A task written before the style was changed is marked as standing apart; one written in the style is not.
  js('S').content = content(quest, {tasks: [{...task, written_in: 'legend'}]});
  assert.match(js('Content').items()[1].node().textContent, /написано в стиле «рабочая легенда»/);
  js('S').content = content(quest, {tasks: [{...task, written_in: 'quest'}]});
  assert.doesNotMatch(js('Content').items()[1].node().textContent, /написано в стиле/);
});

test('a style changed after its story was written says so, and an edit of the style names it in words', () => {
  const js = page();
  // The story on the card was written for the other style: its reasons are not shown as the reasons for this one.
  js('S').content = content({...quest, written_for: 'legend'});
  const stale = js('Content').items()[0].node().textContent;
  assert.match(stale, /Стиль сменили/); assert.match(stale, /Обоснование и сюжет ниже написаны для стиля «рабочая легенда»\./);
  assert.doesNotMatch(stale, /Почему этот стиль/); assert.doesNotMatch(stale, /Есть сквозной сюжет серии\./);
  // Written for the style in force, or before the mark was kept: the reasons are the reasons.
  for (const style of [{...quest, written_for: 'quest'}, quest]) {
    js('S').content = content(style);
    const card = js('Content').items()[0].node().textContent;
    assert.match(card, /Почему этот стиль/); assert.doesNotMatch(card, /Стиль сменили/);
  }
  // Kept as it stands by the methodologist: no reasons were given for this style, and the card does not invent them.
  js('S').content = content({...quest, written_for: 'quest', reason: '', status: 'approved'});
  assert.match(js('Content').items()[0].node().textContent, /Стиль и сюжет приняты вами; ассистент этот стиль не обосновывал\./);
  const patches = [{part: 'style', field: 'profile', label: 'Стиль проекта · Стиль подачи', before: 'quest', after: 'legend', reason: 'Просьба', edited: false},
    {part: 'style', field: 'written_for', label: 'Стиль проекта · Для какого стиля написан сюжет', before: 'quest', after: 'legend', reason: 'Сюжет переписан под выбранный стиль', edited: false},
    {part: 'task:1', field: 'written_in', label: 'Задание 1 · В каком стиле написано', before: 'quest', after: 'legend', reason: 'Задание переписано под выбранный стиль', edited: false}];
  js('S').content = content(quest, {messages: [{id: 'm3', role: 'assistant', part: '', text: 'Меняю стиль.', notes: [], model: 'm', kind: 'request', status: 'pending', patches}]});
  const card = js('Content').items()[0].node();
  assert.equal((card.textContent.match(/−Квест/g) || []).length, 3); assert.equal((card.textContent.match(/\+Рабочая легенда/g) || []).length, 3);
  assert.doesNotMatch(card.textContent, /−quest|\+legend/);
  // «Изменить»: the box holds the name the methodologist reads, not the key the file keeps.
  js('S').editing = 'm3';
  const boxes = js('Content').items()[0].node().byTag('textarea').map((el) => el.value);
  assert.deepEqual(boxes, ['Рабочая легенда', 'Рабочая легенда', 'Рабочая легенда']);
  js('S').editing = null;
});

test('the theory and the frame are named as the README of the style has them', () => {
  const js = page();
  const theory = {status: 'draft', note: '', topics: [{number: 2, title: 'Ввод и вывод', tasks: [1], text: 'Направление.', links: [], keywords: ['stdin'], question: ''}]};
  const frame = {status: 'draft', introduction: 'Экран мигает.', instruction: ['Только стандартная библиотека.'], conclusion: 'Дверь открыта.'};
  const cards = (style) => {
    js('S').content = content(style, {theory, frame, messages: [
      {id: 't', role: 'assistant', part: 'theory', text: 'Теория.', patches: [], notes: [], model: 'm', kind: ''},
      {id: 'f', role: 'assistant', part: 'frame', text: 'Введение.', patches: [], notes: [], model: 'm', kind: ''}]});
    return js('Content').items().map((item) => item.node().textContent);
  };
  let [topics, around] = cards(quest);
  assert.match(topics, /List 2\. Ввод и вывод/); assert.match(around, /Сцена, правила проекта и развязка/); assert.match(around, /Сцена · Chapter I/); assert.match(around, /Развязка · Chapter IV/);
  [topics, around] = cards({...quest, profile: 'legend'});
  assert.match(topics, /Раздел 2\. Ввод и вывод/); assert.match(around, /Легенда, правила проекта и итог/); assert.match(around, /Легенда · до содержания/);
  [topics, around] = cards(null);
  assert.match(topics, /2\.2\. Ввод и вывод/); assert.doesNotMatch(topics, /Раздел 2\.2\./); assert.match(around, /Глава 1 и заключение/); assert.match(around, /Введение/); assert.match(around, /Заключение/);
});

// The names the server sends for the plan of the theory (content/models.py PRESENTATIONS, YES_NO).
const NAMES = {presentations: {chapter: 'короткая глава в README', notes: 'конспект в README', materials: 'конспект в материалах', both: 'коротко в README и конспект в материалах'},
  yes_no: {yes: 'да', no: 'нет'}};
const plan = {presentation: 'materials', internet: 'no', english: 'yes', status: 'draft', chosen_by: 'assistant', written_for: 'materials',
  reason: 'Интернета у участников нет.', sample: '| Роли | materials/theory.md |', questions: ['Можно ли пользоваться ИИ-ассистентом?']};

test('the card of the plan says how the theory is given, why, shows the sample and what is asked', () => {
  const js = page();
  const shown = (pl) => {
    js('S').content = content(quest, {...NAMES, theory_plan: pl, messages: [{id: 'p', role: 'assistant', part: 'theory_plan', text: 'Предлагаю.', patches: [], notes: [], model: 'm', kind: ''}]});
    return js('Content').items()[0].node().textContent;
  };
  let card = shown(plan);
  assert.match(card, /Подача теории: конспект в материалах/); assert.match(card, /предложено ассистентом/); assert.match(card, /Черновик/);
  assert.match(card, /Почему так/); assert.match(card, /Интернета у участников нет\./);
  assert.match(card, /Как это будет выглядеть — пример одного раздела/); assert.match(card, /\| Роли \| materials\/theory\.md \|/);
  assert.match(card, /нет — ссылки не ищем и не показываем/); assert.match(card, /да — её найдёт поиск, код проверит, что она стоит на странице дословно/);
  assert.match(card, /Вопросы — ответьте в чате/); assert.match(card, /1\. Можно ли пользоваться ИИ-ассистентом\?/);
  // Chosen by the methodologist after it was argued for: the reasons and the sample are named as those of the other one.
  card = shown({...plan, presentation: 'notes', chosen_by: 'chat'});
  assert.match(card, /Подача теории: конспект в README/); assert.match(card, /выбрано вами/); assert.match(card, /Вариант сменили/);
  assert.match(card, /написаны для варианта «конспект в материалах»/); assert.match(card, /Пример — для варианта «конспект в материалах»/);
  assert.doesNotMatch(card, /Почему так/); assert.doesNotMatch(card, /Интернета у участников нет\./);
  // The chip that writes an example exists only while the plan is not agreed: an agreed card does not point to it.
  assert.match(card, /Пример для выбранного — кнопкой под чатом\./);
  assert.doesNotMatch(shown({...plan, presentation: 'notes', chosen_by: 'chat', status: 'approved'}), /кнопкой под чатом/);
  // Taken without reasons, with the internet and no excerpt: said as it is, and nothing is invented.
  card = shown({...plan, presentation: 'chapter', written_for: 'chapter', internet: 'yes', english: 'no', reason: '', sample: '', questions: [], status: 'approved'});
  assert.match(card, /Вариант выбран вами; ассистент его не обосновывал\./); assert.match(card, /есть — к разделам ищем ссылки/);
  assert.doesNotMatch(card, /Как это будет выглядеть/); assert.doesNotMatch(card, /Вопросы — ответьте в чате/); assert.match(card, /Согласовано/);
});

test('the card of the theory shows the summary, the excerpt and where the summaries lie', () => {
  const js = page();
  const topic = {number: 1, title: 'Роли', tasks: [1], text: 'Справка.', notes: 'Конспект: роль — это набор обязанностей.', links: [], keywords: ['roles'], question: ''};
  const theory = {status: 'draft', note: '', topics: [topic, {...topic, number: 2, title: 'Встречи', notes: ''}],
    excerpt: {topic: 1, title: 'Roles Guide', url: 'https://example.org/roles', quote: 'A role is a set of responsibilities.'}};
  const shown = (extra) => {
    js('S').content = content(quest, {...NAMES, theory, ...extra, messages: [{id: 't', role: 'assistant', part: 'theory', text: 'Теория.', patches: [], notes: [], model: 'm', kind: ''}]});
    return js('Content').items()[0].node();
  };
  let card = shown({theory_plan: plan, theory_file: 'theory.md'});
  assert.match(card.textContent, /подача: конспект в материалах · materials\/theory\.md/);
  assert.equal((card.textContent.match(/Конспект: роль — это набор обязанностей\./g) || []).length, 1);
  assert.match(card.textContent, /Первоисточник \(на английском\) — дословно со страницы/); assert.match(card.textContent, /A role is a set of responsibilities\./);
  assert.deepEqual(card.byTag('a').map((a) => [a.getAttribute('href'), a.textContent]), [['https://example.org/roles', 'Roles Guide']]);
  // Without the internet a section without links is no gap: nothing is said of links that were not looked for.
  assert.doesNotMatch(card.textContent, /Ссылок нет/); assert.match(card.textContent, /У участников нет интернета: ссылки не искали\./);
  // A chapter with the internet, the excerpt taken out: the card of a theory as it always was.
  card = shown({theory_plan: {...plan, presentation: 'chapter', internet: 'yes'}, theory: {...theory, excerpt: {...theory.excerpt, removed: true}}});
  assert.doesNotMatch(card.textContent, /подача:/); assert.doesNotMatch(card.textContent, /Первоисточник/);
  assert.match(card.textContent, /Ссылок нет\. Ключевые слова: roles/); assert.match(card.textContent, /Ссылки — только со страниц, которые открылись/);
  // A draft begun before the plan has neither a plan nor summaries: the same card, nothing added.
  card = shown({theory: {status: 'draft', note: '', topics: [{...topic, notes: undefined}]}});
  assert.doesNotMatch(card.textContent, /Конспект|подача:|Первоисточник/);
});

test('a quotation is taken out or put back, never typed over', () => {
  const js = page();
  const patches = [{part: 'theory', field: 'excerpt', label: 'Теория · Выдержка из первоисточника', before: 'A role is a set of responsibilities.', after: '', reason: 'Просьба', edited: false},
    {part: 'topic:1', field: 'text', label: 'List 1 · Текст', before: 'Справка.', after: 'Новая справка.', reason: 'Просьба', edited: false}];
  js('S').content = content(quest, {...NAMES, messages: [{id: 'm4', role: 'assistant', part: '', text: 'Убираю.', notes: [], model: 'm', kind: 'request', status: 'pending', patches}]});
  // «Изменить» gives a box to the text of the section and none to the quotation: a sentence typed there would stand
  // under the name and the address of the real source.
  js('S').editing = 'm4';
  const card = js('Content').items()[0].node();
  assert.deepEqual(card.byTag('textarea').map((el) => el.getAttribute('id')), ['cpatch-m4-1']);
  assert.match(card.textContent, /\+убрать/); assert.match(card.textContent, /−A role is a set of responsibilities\./);
  js('S').editing = null;
});

test('an edit of the plan names its values in words', () => {
  const js = page();
  const patches = [{part: 'theory_plan', field: 'presentation', label: 'Подача теории · Как подаётся теория', before: 'chapter', after: 'materials', reason: 'Просьба', edited: false},
    {part: 'theory_plan', field: 'internet', label: 'Подача теории · Интернет у участников', before: 'yes', after: 'no', reason: 'Ответ', edited: false},
    {part: 'theory_plan', field: 'english', label: 'Подача теории · Выдержка из первоисточника на английском', before: 'no', after: 'yes', reason: 'Просьба', edited: false}];
  js('S').content = content(quest, {...NAMES, messages: [{id: 'm3', role: 'assistant', part: '', text: 'Меняю.', notes: [], model: 'm', kind: 'request', status: 'pending', patches}]});
  const card = js('Content').items()[0].node().textContent;
  assert.match(card, /−короткая глава в README/); assert.match(card, /\+конспект в материалах/); assert.match(card, /−да/); assert.match(card, /\+нет/); assert.match(card, /−нет/); assert.match(card, /\+да/);
  assert.doesNotMatch(card, /−chapter|\+materials|−yes|\+no\b/);
  js('S').editing = 'm3';
  assert.deepEqual(js('Content').items()[0].node().byTag('textarea').map((el) => el.value), ['конспект в материалах', 'нет', 'да']);
  js('S').editing = null;
});

test('the style is a step of the route only for a draft that has one', () => {
  const js = page();
  js('S').content = content(quest);
  assert.deepEqual(plain(js('Content').steps().list.map(([name, done]) => [name, done])),
    [['Стиль', false], ['Название', false], ['Задания и материалы', false], ['Теория и введение', false], ['Критики', false], ['README', false]]);
  assert.equal(js('Content').steps().current, 0);
  js('S').content = content({...quest, status: 'approved'});
  assert.equal(js('Content').steps().current, 1);
  js('S').content = content(null);
  assert.deepEqual(plain(js('Content').steps().list.map(([name]) => name)), ['Название', 'Задания и материалы', 'Теория и введение', 'Критики', 'README']);
});

test('on the page: the other style is offered as a chip beside the agreement', () => {
  const js = page();
  js('S').content = content(quest, {next_step: {action: 'approve_style', number: null, button: 'Стиль подходит — предложить название и аннотацию',
    title: 'Стиль проекта предложен', text: 'Проверьте сюжет.', route: 'Этапы', edit_hint: 'Что поменять в стиле или сюжете…',
    alternatives: [{action: 'switch_style', button: 'Сменить стиль на «рабочая легенда»'}]}, checks: []});
  const node = js('Content').nextStep();
  const chips = node.byTag('button').map((b) => b.textContent);
  assert.deepEqual(chips.slice(0, 3), ['Стиль подходит — предложить название и аннотацию', 'Сменить стиль на «рабочая легенда»', 'Поправить текст']);
});

test('on the page: the form sends the style and the story of the series with the brief', () => {
  const js = page({dom: true});
  // The form is a set of named fields; each keeps its own value.
  const fields = {style_profile: {value: 'quest'}, series_story: {value: '  ИИ Марк открывает двери.  '}, format: {value: 'individual'}};
  js('form')().elements = {namedItem: (name) => (fields[name] ||= {value: '', checked: false})};
  const brief = js('readBrief')();
  assert.equal(brief.style_profile, 'quest');
  assert.equal(brief.series_story, 'ИИ Марк открывает двери.');
  // Left as they open: the assistant will propose a style, and there is no story.
  fields.style_profile.value = ''; fields.series_story.value = '';
  assert.deepEqual([js('readBrief')().style_profile, js('readBrief')().series_story], ['', '']);
  assert.deepEqual(plain(Object.keys(js('STYLE'))), ['', 'quest', 'legend']);
});

test('the card of the title lists the titles offered until the title is agreed', () => {
  const js = page();
  const summary = {title: 'Битая память', annotation: 'Ты разберёшь файл с испорченными записями.', status: 'draft',
    title_options: ['Битая память', 'Собрать отчёт', 'Что уцелело в файле?']};
  const shown = (extra) => {
    js('S').content = content(null, {summary: {...summary, ...extra}, messages: [{id: 'm1', role: 'assistant', part: 'summary', text: 'Название и аннотация.', patches: [], notes: [], model: 'm', kind: ''}]});
    return js('Content').items().map((item) => item.node().textContent).join('\n');
  };
  const card = shown({});
  assert.match(card, /Битая память/);
  assert.match(card, /Варианты названия — выбрать кнопкой под чатом/);
  assert.match(card, /1\. Битая память\n2\. Собрать отчёт\n3\. Что уцелело в файле\?/);
  // Agreed, or begun before the titles were offered: the card is the one it always was.
  assert.doesNotMatch(shown({status: 'approved'}), /Варианты названия/);
  assert.doesNotMatch(shown({title_options: []}), /Варианты названия/);
  assert.doesNotMatch(shown({title_options: undefined}), /Варианты названия/);
});
