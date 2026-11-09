// What the chat page shows: outcome names, the participant's path, block goals, how a message was understood,
// and the values an edited proposal sends back. Run: node --test project_generator_lab/tests/js
import assert from 'node:assert/strict';
import {test} from 'node:test';

import {page} from './page.mjs';

const run = {id: 'r1', status: 'input_review', request: {brief: {outcomes: 'Собирать требования\nОценивать сроки\n\nСтроить план'}}};

function withRun(js) {
  js('S').runs = {r1: run};
  js('S').thread = {runs: ['r1']};
  return js;
}

test('outcomes are named by their wording, numbers that are not outcomes stay as written', () => {
  const js = page();
  const expand = js('expandOutcomes');
  assert.equal(expand(run, 'Закрывает ОР 1 и 3.'), 'Закрывает ОР «Собирать требования», «Строить план».');
  assert.equal(expand(run, 'ОР 2, ОР 4'), 'ОР «Оценивать сроки», ОР 4');  // 4 is not an outcome: left as is
  assert.equal(expand(run, 'ОР 1-2 и ОР 2.5'), 'ОР 1-2 и ОР 2.5');      // a range and a decimal are not outcome numbers
  assert.equal(js('outcomeName')(run, 2), '«Оценивать сроки»');
  assert.equal(js('outcomeName')(run, 9), 'ОР 9');
});

test('Russian plural forms', () => {
  const plural = page()('plural');
  const forms = [1, 2, 5, 11, 12, 14, 21, 22, 25, 111, 112].map((n) => plural(n, 'проект', 'проекта', 'проектов'));
  assert.deepEqual(forms, ['проект', 'проекта', 'проектов', 'проектов', 'проектов', 'проектов', 'проект', 'проекта', 'проектов', 'проектов', 'проектов']);
});

const learner = {
  before: [{id: 'a', title: 'Сбор требований', block: 'Блок 1', hours: 12, outcomes: ['Собирать требования'], skills: ['Интервью'], tools: ['Miro']},
           {id: 'b', title: 'Сроки', block: 'Блок 1', hours: 8.5, outcomes: [], skills: [], tools: []}],
  tools_before: [{tool: 'Miro', projects: ['Сбор требований', 'Сроки']}],
  tools_here: [{tool: 'Miro', before: 2}, {tool: 'Jira', before: 0}],
  tools_ahead: ['Figma'],
};
const path = {before: 'По УП участник прошёл ОР 1.', builds_on: ['требования — «Сбор требований»'],
  next_needs: [{project: 'План работ', needs: 'план по бэклогу', teach_here: 'оценивать задачи', outcome_numbers: [2], evidence_ids: []},
               {project: 'Риски', needs: 'риски', teach_here: 'видеть зависимости', outcome_numbers: [], evidence_ids: []}]};

test('the participant path shows what was covered before, what is new here and what later projects need', () => {
  const js = withRun(page());
  const rows = js('learnerPath')(learner, path, run);
  const text = rows.map((r) => r.textContent).join('\n');
  assert.match(text, /Что участник прошёл до проекта · 2 проекта, 20,5 ч/);
  assert.match(text, /По УП участник прошёл ОР «Собирать требования»\./);
  assert.match(text, /Miro · 2/);
  assert.match(text, /Miro · встречался по УП/);
  assert.match(text, /Jira · впервые/);
  assert.match(text, /Опирается на прошлые проекты — заново не учим/);
  assert.match(text, /Здесь: оценивать задачи/);
  assert.match(text, /ОР: «Оценивать сроки»/);
  // A need no outcome of this project covers is put to the methodologist, not hidden.
  assert.match(text, /Ни один ОР этого проекта это не закрывает — решите, нужно ли/);
  assert.match(text, /Новые инструменты впереди: Figma/);
  // On the latest version the path can be corrected from the chat.
  assert.ok(rows.at(-1).byTag('button').some((b) => b.textContent === 'Поправить путь участника'));
  // Without the plan's facts there is nothing to show; without the card's path the facts are still shown.
  assert.equal(js('learnerPath')(null, path, run), null);
  const facts = js('learnerPath')(learner, null, run).map((r) => r.textContent).join('\n');
  assert.match(facts, /Проекты до этого: ОР, навыки, инструменты по УП/);
  assert.doesNotMatch(facts, /Опирается на прошлые проекты/);
});

test('the block goals of this project are shown open, the placement of the others folded', () => {
  const js = withRun(page());
  const goals = [{number: 1, text: 'Собирать требования'}, {number: 2, text: 'Планировать'}, {number: 3, text: 'Управлять рисками'}];
  const placed = new Map([[1, {owner: 'previous', project: 'Сбор требований', why: 'по названию'}], [2, {owner: 'this', project: '', why: 'ОР'}]]);
  const node = js('mineGoals')(goals, placed, run);
  assert.match(node.textContent, /Цели блока, которые закрывает этот проект · 1 из 3/);
  const open = node.byClass('goal-list')[0];
  assert.equal(open.byTag('li').length, 1);
  assert.match(open.textContent, /Планировать/);
  const folded = node.byTag('details')[0].textContent;
  assert.match(folded, /этому проекту 1 из 3/);
  assert.match(folded, /«Сбор требований» · по названию/);
  assert.match(folded, /не разложена/);  // goal 3 has no placement
  const none = js('mineGoals')(goals, new Map(), run);
  assert.match(none.textContent, /Ни одной цели блока этому проекту не отдано — проверьте раскладку\./);
});

test('every reply says how the message was understood, in both chats', () => {
  const js = withRun(page());
  const reply = (kind) => js('messageNode')({role: 'assistant', kind, text: 'Ответ про ОР 2', run_id: 'r1', patches: [], notes: ['Заметка']});
  assert.match(reply('question').textContent, /понял как: вопрос/);
  assert.match(reply('answer').textContent, /понял как: ответ на вопрос/);
  assert.match(reply('question').textContent, /Ответ про ОР «Оценивать сроки»/);
  assert.match(reply('question').textContent, /Заметка/);
  assert.match(reply('').textContent, /Ассистент методолога · правки задания/);  // older replies have no kind
  // The content chat labels the same kinds with the same words.
  // (Before 01.10.2026 the content chat had its own list and called an answer just «ответ».)
  js('S').content = {exists: true, messages: [
    {id: 'm1', role: 'assistant', kind: 'answer', text: 'Сократил.', model: 'm', patches: [], notes: [], part: ''},
    {id: 'm2', role: 'assistant', kind: 'off_topic', text: 'Я помогаю с проектом.', model: '', patches: [], notes: [], part: ''}]};
  const [answer, off] = js('Content').items().map((item) => item.node().textContent);
  assert.match(answer, /контент · m · понял как: ответ на вопрос/);
  assert.match(off, /контент · понял как: не по теме/);
});

test('values edited in a proposal go back typed', () => {
  const parse = page()('parseValue');
  assert.equal(parse('bonus', 'true'), true);
  assert.equal(parse('bonus', 'false'), false);
  assert.equal(parse('hours', '12.5'), 12.5);
  assert.equal(parse('tasks_count', ''), null);
  assert.equal(parse('notes', '12'), '12');  // text stays text
});

// The standard plan has exams among its projects (РГ 02.10.2026, «Батон»): control points, not projects.
const planRows = [
  {id: 'p1', block: 'Python', title: 'Скрипты', kind: 'project', description: 'Три консольные программы.', cases: '1. Починить файл.\n2. Сохранить в Git.'},
  {id: 'p2', block: 'Python', title: 'Команда', kind: 'project', description: 'Адаптивный тест.', cases: ''},
  {id: 'e1', block: 'Python', title: 'Экзамен 1. Якорь', kind: 'exam', description: 'Тест по первым дням.', cases: ''},
  {id: 'p3', block: 'Данные', title: 'Дашборд', description: '', cases: '1. Выгрузка.'},   // a plan saved before the kind existed
  {id: 'e2', block: 'Контроль', title: 'Экзамен 2', kind: 'exam', description: '', cases: ''},
  {id: 'p4', block: 'Питч', title: 'Питч', kind: 'project', description: '', cases: ''},
];

test('the place in the programme counts projects; an exam is nobody\'s neighbour', () => {
  const js = page();
  const position = js('programPosition');
  const text = js('positionText');
  assert.equal(text(position({rows: planRows}, 'p3')), 'Проект 3 из 4 · середина программы · блок 2 из 3 · до него: «Команда» · после: «Питч»');
  assert.equal(text(position({rows: planRows}, 'p4')), 'Проект 4 из 4 · последний проект программы · блок 3 из 3 · до него: «Дашборд»');
  assert.equal(position({rows: planRows}, 'e1'), null);
  assert.equal(js('planCount')(planRows), '4 проекта и 2 экзамена');
  assert.equal(js('planCount')(planRows.filter((r) => r.kind !== 'exam')), '4 проекта');
  assert.equal(js('planCount')([planRows[0], planRows[2]]), '1 проект и 1 экзамен');
});

test('an exam is shown in its block and cannot be chosen', () => {
  const projectOptions = page()('projectOptions');
  // Values made inside the page's sandbox are compared as data, not as objects of this realm.
  const options = (block) => JSON.parse(JSON.stringify(projectOptions(planRows, block)));
  assert.deepEqual(options('Python'), [['p1', 'Скрипты'], ['p2', 'Команда'], ['e1', 'Экзамен 1. Якорь — экзамен', true]]);
  assert.deepEqual(options('Данные'), [['p3', 'Дашборд']]);
  assert.deepEqual(options('Нет такого'), []);
});

test('the description of the brief is the theses of the plan and its cases', () => {
  const describe = page()('rowDescription');
  assert.equal(describe(planRows[0]), 'Три консольные программы.\n\nКейсы проекта:\n1. Починить файл.\n2. Сохранить в Git.');
  assert.equal(describe(planRows[1]), 'Адаптивный тест.');
  assert.equal(describe(planRows[3]), 'Кейсы проекта:\n1. Выгрузка.');
  assert.equal(describe({description: '', cases: ''}), '');
  assert.equal(describe({description: 'Старый план без кейсов'}), 'Старый план без кейсов');
});

test('the participant path counts projects and names an exam as an exam', () => {
  const js = withRun(page());
  const withExam = {...learner, before: [...learner.before, {id: 'e', kind: 'exam', title: 'Экзамен 1. Якорь', block: 'Блок 1', hours: 2, outcomes: ['Объясняет правила'], skills: [], tools: []}]};
  const rows = js('learnerPath')(withExam, null, run);
  const text = rows.map((r) => r.textContent).join('\n');
  assert.match(text, /Что участник прошёл до проекта · 2 проекта и 1 экзамен, 22,5 ч/);
  assert.match(text, /Проекты и экзамены до этого: ОР, навыки, инструменты по УП/);
  const items = rows[0].byTag('li').filter((li) => li.byTag('strong').length);
  assert.deepEqual(items.map((li) => li.byClass('tag').filter((t) => t.textContent === 'экзамен').length), [0, 0, 1]);
});

test('on the page: the plan lists projects, an exam is disabled, the brief gets the theses and the cases', () => {
  const js = page({dom: true});
  const el = js('$');
  // The form itself is not under test: what would be put into it is caught, saving and the hours hint are switched off.
  js('fillBrief = (brief) => { S.filled = brief; }; saveIntake = () => {}; refreshIntakeWorkload = () => {};');
  const S = js('S');
  S.curriculum = {filename: 'Паспорт.xlsx', rows: planRows, columns: {code: ['Код'], cases: ['Кейсы'], checking: ['Формат проверки'], day: ['День']},
    missing_fields: [], unmapped_columns: [], warnings: ['Прочитана вкладка «УП».']};
  S.rowId = 'e1';  // a choice remembered from a plan loaded before exams were told apart
  js('renderCurriculum')(true);
  const card = el('#curriculumCard').textContent;
  assert.match(card, /4 проекта и 2 экзамена · контекст всей программы сохранится/);
  assert.match(card, /Код проекта/); assert.match(card, /Кейсы \(в описании\)/); assert.match(card, /Форма контроля/);
  assert.match(card, /Прочитана вкладка «УП»\./);
  // A block with nothing but an exam is not offered; inside a block the exam is listed where it stands, closed.
  assert.deepEqual(el('#blockSelect').children.map((o) => o.value), ['Python', 'Данные', 'Питч']);
  assert.deepEqual(el('#projectSelect').children.map((o) => [o.value, o.textContent, Boolean(o.disabled)]),
    [['p1', 'Скрипты', false], ['p2', 'Команда', false], ['e1', 'Экзамен 1. Якорь — экзамен', true]]);
  assert.equal(S.rowId, 'p1');
  assert.equal(el('#projectSelect').value, 'p1');
  assert.equal(S.filled.description, 'Три консольные программы.\n\nКейсы проекта:\n1. Починить файл.\n2. Сохранить в Git.');
  assert.equal(el('#positionLine').textContent, 'Проект 1 из 4 · первый проект программы · блок 1 из 3 · после: «Команда»');
  // Another block, and a project chosen by id.
  el('#blockSelect').value = 'Данные';
  js('showProjects')('', true);
  assert.equal(S.rowId, 'p3');
  assert.equal(S.filled.description, 'Кейсы проекта:\n1. Выгрузка.');
  el('#blockSelect').value = 'Python';
  js('showProjects')('p2', false);
  assert.equal(S.rowId, 'p2');
  js('showProjects')('e1', false);   // an exam's id never becomes the choice
  assert.equal(S.rowId, 'p1');
  // A remembered exam that stands alone in its block: the page opens on the first project, not on an empty list.
  S.rowId = 'e2';
  js('renderCurriculum')(false);
  assert.equal(el('#blockSelect').value, 'Python');
  assert.equal(S.rowId, 'p1');
});

test('on the page: between two skills tabs the methodologist chooses, and only the skills of the form change', async () => {
  const js = page({dom: true});
  const el = js('$');
  const S = js('S');
  const sent = [];
  // The server is stood in for: it answers with the plan read from the tab that was asked for.
  const plan = (tab) => ({filename: 'Паспорт.xlsx', columns: {}, missing_fields: [], unmapped_columns: [],
    warnings: tab ? [`Развесовка — вкладка «${tab}»`] : ['В книге несколько вкладок развесовки'],
    skill_tabs: ['Развесовка (OLD)', 'Развесовка'], skills_tab: tab,
    rows: [{id: 'p1', block: 'Б', title: 'Скрипты', kind: 'project', skills: tab === 'Развесовка' ? 'Git\nPython' : tab ? 'Linux' : ''},
           {id: 'p2', block: 'Б', title: 'Команда', kind: 'project', skills: tab ? 'SQL' : ''}]});
  // The form: its skills field and any other field, to see that the other is not touched.
  const skillsField = {value: 'что было', tagName: 'TEXTAREA'}, described = {value: 'Описание, набранное руками', tagName: 'TEXTAREA'};
  el('#briefForm').elements = {namedItem: (name) => (name === 'skills' ? skillsField : described)};
  js('countMore = () => {}; saveIntake = () => {}; updateStart = () => {};');
  js('notify = (message) => { S.said = message; }');
  js(`api = async (url, options) => { S.sent.push([url, options.body.get('skills_tab'), options.body.get('file').name]); return S.answer(options.body.get('skills_tab')); }`);
  S.sent = sent; S.answer = plan;
  S.curriculum = plan(''); S.rowId = 'p2';

  // One tab or none: nothing to choose.
  assert.equal(js('skillsTabChoice')({skill_tabs: ['Развесовка']}), null);
  assert.equal(js('skillsTabChoice')({}), null);

  // After the page was reloaded the file is gone: the choice is shown closed, with what to do.
  let choice = js('skillsTabChoice')(S.curriculum);
  assert.equal(choice.byTag('select')[0].disabled, true);
  assert.match(choice.textContent, /Чтобы выбрать вкладку, загрузите книгу снова\./);
  await js('chooseSkillsTab')('Развесовка');
  assert.equal(sent.length, 0);

  S.planFile = new File(['x'], 'Паспорт.xlsx');
  js('renderCurriculum')(false);
  const select = el('#curriculumCard').byTag('select')[0];
  assert.deepEqual(select.children.map((o) => [o.value, o.textContent]), [['', 'не выбрана'], ['Развесовка (OLD)', 'Развесовка (OLD)'], ['Развесовка', 'Развесовка']]);
  assert.equal(Boolean(select.disabled), false);
  assert.equal(select.value, '');
  assert.doesNotMatch(el('#curriculumCard').textContent, /загрузите книгу снова/);

  // Choosing sends the same workbook with the tab; the chosen project stays, its skills come from the tab,
  // and the rest of the form is not touched.
  select.value = 'Развесовка';
  await select.listeners.change[0]();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(JSON.parse(JSON.stringify(sent)), [['api/curriculum', 'Развесовка', 'Паспорт.xlsx']]);
  assert.equal(S.curriculum.skills_tab, 'Развесовка');
  assert.equal(S.rowId, 'p2');
  assert.equal(skillsField.value, 'SQL');
  assert.equal(described.value, 'Описание, набранное руками');
  assert.equal(el('#curriculumCard').byTag('select')[0].value, 'Развесовка');
  assert.equal(S.uploads, 0);

  // A refusal of the server is shown; the plan on the page stays as it was.
  S.answer = () => { throw new Error('Файл больше 4 МБ'); };
  await js('chooseSkillsTab')('Развесовка (OLD)');
  assert.equal(S.said, 'Файл больше 4 МБ');
  assert.equal(S.curriculum.skills_tab, 'Развесовка');
  assert.equal(S.uploads, 0);
});

test('on the page: the workbook kept for the tab choice is the one the plan on the page was read from', async () => {
  const js = page({dom: true});
  const el = js('$');
  const S = js('S');
  const plan = (name, tab = '') => ({filename: name, columns: {}, missing_fields: [], unmapped_columns: [], warnings: [], skill_notes: [`Развесовка: ${tab || 'не выбрана'}`],
    skill_tabs: ['Старая', 'Новая'], skills_tab: tab, rows: [{id: 'p1', block: 'Б', title: 'Скрипты', kind: 'project', skills: tab ? `Навык вкладки «${tab}»` : ''}]});
  const skillsField = {value: 'набрано руками', tagName: 'TEXTAREA'};
  el('#briefForm').elements = {namedItem: () => skillsField};
  js('countMore = () => {}; saveIntake = () => { S.saved = (S.saved || 0) + 1; }; updateStart = () => {}; fillBrief = () => {}; refreshIntakeWorkload = () => {};');
  js('notify = (message) => { S.said = message; }');
  // The server answers when the test lets it: `S.waiting` holds what was asked and the way to answer.
  js(`api = (url, options) => new Promise((resolve, reject) => { S.waiting.push({tab: options.body.get('skills_tab'), file: options.body.get('file').name, resolve, reject}); })`);
  S.waiting = [];
  const first = new File(['1'], 'Первая.xlsx'), second = new File(['2'], 'Вторая.xlsx');
  const select = () => el('#curriculumCard').byTag('select')[0];
  const remove = () => el('#curriculumCard').find((e) => e.getAttribute('aria-label') === 'Убрать учебный план')[0].listeners.click[0]();

  // A plan read from a file: the file is kept with it. A file the server refused is not: the choice goes on sending the first.
  await js('loadCurriculum')(Promise.resolve(plan('Первая.xlsx')), first);
  assert.equal(S.planFile, first);
  await js('loadCurriculum')(Promise.reject(new Error('В файле нет учебного плана')), second);
  assert.equal(S.said, 'В файле нет учебного плана');
  assert.equal(S.planFile, first);
  assert.equal(S.curriculum.filename, 'Первая.xlsx');
  assert.match(el('#curriculumCard').textContent, /Развесовка: не выбрана/);   // the notes about skills are shown with the warnings

  // One choice at a time: while the first is on its way the second is not sent, and the start button waits.
  const choosing = js('chooseSkillsTab')('Новая');
  assert.equal(S.choosingTab, true);
  assert.equal(S.uploads, 1);
  await js('chooseSkillsTab')('Старая');
  assert.deepEqual(S.waiting.map((w) => [w.tab, w.file]), [['Новая', 'Первая.xlsx']]);
  assert.equal(select().disabled, true);   // on the page itself, not only in a list drawn anew
  S.waiting.pop().resolve(plan('Первая.xlsx', 'Новая'));
  await choosing;
  assert.equal(S.choosingTab, false);
  assert.equal(S.uploads, 0);
  assert.equal(skillsField.value, 'Навык вкладки «Новая»');
  assert.equal(select().value, 'Новая');
  assert.equal(Boolean(select().disabled), false);

  // «Не выбрана» takes no skills from anywhere: the plan is read without a tab, the form keeps what stands in it.
  skillsField.value = 'поправлено руками';
  const none = js('chooseSkillsTab')('');
  S.waiting.pop().resolve(plan('Первая.xlsx'));
  await none;
  assert.equal(S.curriculum.skills_tab, '');
  assert.equal(skillsField.value, 'поправлено руками');
  assert.equal(select().value, '');

  // A refusal: the list returns to the tab the plan on the page was read from, and can be used again.
  const before = S.saved;
  select().value = 'Старая';
  const refused = js('chooseSkillsTab')('Старая');
  S.waiting.pop().reject(new Error('Сервис занят'));
  await refused;
  assert.equal(S.said, 'Сервис занят');
  assert.equal(select().value, '');
  assert.equal(Boolean(select().disabled), false);
  assert.equal(S.saved, before);   // nothing changed, nothing to save
  assert.equal(skillsField.value, 'поправлено руками');

  // The plan was replaced while the answer was on its way: the answer is about a workbook that is no longer on the page.
  const late = js('chooseSkillsTab')('Новая');
  await js('loadCurriculum')(Promise.resolve(plan('Вторая.xlsx')), second);
  S.waiting.pop().resolve(plan('Первая.xlsx', 'Новая'));
  await late;
  assert.equal(S.curriculum.filename, 'Вторая.xlsx');
  assert.equal(S.curriculum.skills_tab, '');
  assert.equal(skillsField.value, 'поправлено руками');
  assert.equal(S.uploads, 0);

  // A refusal that comes after the plan was replaced is about the other workbook too: it is not shown.
  S.said = '';
  const lateRefusal = js('chooseSkillsTab')('Новая');
  await js('loadCurriculum')(Promise.resolve(plan('Первая.xlsx')), first);
  S.waiting.pop().reject(new Error('Сервис занят'));
  await lateRefusal;
  assert.equal(S.said, '');
  assert.equal(S.curriculum.filename, 'Первая.xlsx');

  // Two workbooks one after the other: the page keeps the one asked for last, whichever answer comes last —
  // and the file kept for the choice is that plan's file.
  let answerEarlier, refuseEarlier;
  const earlier = js('loadCurriculum')(new Promise((resolve) => { answerEarlier = resolve; }), first);
  await js('loadCurriculum')(Promise.resolve(plan('Вторая.xlsx')), second);
  answerEarlier(plan('Первая.xlsx'));
  await earlier;
  assert.equal(S.curriculum.filename, 'Вторая.xlsx');
  assert.equal(S.planFile, second);
  const refusedEarlier = js('loadCurriculum')(new Promise((resolve, reject) => { refuseEarlier = reject; }), first);
  await js('loadCurriculum')(Promise.resolve(plan('Вторая.xlsx')), second);
  refuseEarlier(new Error('Файл больше 4 МБ'));
  await refusedEarlier;
  assert.equal(S.said, '');
  assert.equal(S.uploads, 0);

  // … or removed: nothing comes back onto the page, and the file is forgotten with the plan.
  const gone = js('chooseSkillsTab')('Новая');
  remove();
  assert.equal(S.planFile, null);
  S.waiting.pop().resolve(plan('Вторая.xlsx', 'Новая'));
  await gone;
  assert.equal(S.curriculum, null);
  assert.equal(el('#curriculumCard').hidden, true);
  assert.equal(S.choosingTab, false);
  await js('chooseSkillsTab')('Новая');
  assert.equal(S.waiting.length, 0);

  // A new form forgets the file too.
  await js('loadCurriculum')(Promise.resolve(plan('Первая.xlsx')), first);
  el('#briefForm').reset = () => {};
  js('renderMaterials = () => {}; renderOutcomes = () => {}; syncGroup = () => {};');
  js('clearIntake')();
  assert.equal(S.planFile, null);
  assert.equal(S.curriculum, null);
});

test('on the page: the skills of the project are shown on each task and as the block of the passport tab', () => {
  const js = page({dom: true});
  const task = (title, optional, skills) => ({title, optional, skills, deliverable: 'Отчёт', action: 'Сделать', peer_check: 'Проверить', independent_choice: 'Выбор',
    support: 'Документация', outcome_numbers: [1], hours: 1, block_goal_numbers: []});
  const run = {id: 'r1', request: {brief: {outcomes: 'Выделять факты', hours: 3}},
    concept: {tasks: [task('Первое', false, {Git: 5}), task('Бонусное', true, {SQL: 1}), task('Второе', false, {Git: 1, Python: 3}), task('Третье', false, {})]},
    skill_block: {code: 'P1',
      main: {shares: {Git: 0.2, Python: 0.8}, tasks: [{label: '01', shares: {Git: 0.71}}, {label: '02', shares: {Git: 0.29, Python: 1}}, {label: '03', shares: {}}]},
      bonus: {shares: {SQL: 1}, tasks: [{label: 'Bonus 01', shares: {SQL: 1}}]}}};
  js('S').thread = {};
  // Each task of the concept gets its own line of the block: main and bonus tasks are counted apart, in the concept's order.
  assert.deepEqual(JSON.parse(JSON.stringify(js('taskShares')(run))), [{Git: 0.71}, {SQL: 1}, {Git: 0.29, Python: 1}, {}]);
  const rows = run.concept.tasks.map((t, i) => js('taskRow')(run, t, i + 1, js('taskShares')(run)[i]));
  assert.deepEqual(rows.map((row) => row.byClass('tag').map((tag) => tag.textContent)), [['Git'], ['Бонус', 'SQL'], ['Git', 'Python'], []]);
  assert.match(rows[2].textContent, /Навыки: часть баллов навыка за это задание/);
  assert.match(rows[2].textContent, /Git — 29%\nPython — 100%/);   // 0.29 × 100 is 28.999999999999996
  assert.doesNotMatch(rows[3].textContent, /Навыки: часть баллов/);
  // The table: a column for each skill, a line for the project and a line for each task, in percents; nothing where there
  // is no share. A task is called by its number on the page and its title — the findings of code use the same numbers.
  const table = js('skillsTable')(run);
  const cells = (node) => node.byTag('tr').map((tr) => [...tr.children].map((cell) => cell.textContent));
  assert.deepEqual(cells(table), [
    ['Задание', 'Git', 'Python', 'SQL'],
    ['Основная часть — доля навыка в проекте', '20%', '80%', ''],
    ['1. Первое', '71%', '', ''], ['3. Второе', '29%', '100%', ''], ['4. Третье', '', '', ''],
    ['Бонусная часть — доля навыка', '', '', '100%'],
    ['2. Бонусное', '', '', '100%']]);
  assert.match(table.textContent, /Доли посчитал код: из веса, который замысел дал навыку в задании, и часов задания/);
  assert.deepEqual(table.byTag('tr').map((tr) => tr.className), ['', 'total', '', '', '', 'total', '']);
  // No bonus part in the block — no bonus lines; a run of earlier rules has no block and its tasks are drawn as before.
  const main = {...run, concept: {tasks: run.concept.tasks.filter((t) => !t.optional)}, skill_block: {...run.skill_block, bonus: {shares: {}, tasks: []}}};
  assert.equal(js('skillsTable')(main).byTag('tr').length, 5);
  // The plan states a bonus part and the concept has no bonus tasks: the plan's line is shown, with no tasks under it.
  // Shares the plan states are not rounded to whole percents: an eighth is 12,5%.
  const stated = {...main, skill_block: {...main.skill_block, main: {...main.skill_block.main, shares: {Git: 0.125, Python: 0.875}}, bonus: {shares: {SQL: 1}, tasks: []}}};
  assert.deepEqual(cells(js('skillsTable')(stated)).filter((line) => /часть/.test(line[0])),
    [['Основная часть — доля навыка в проекте', '12,5%', '87,5%', ''], ['Бонусная часть — доля навыка', '', '', '100%']]);
  // A skill only a task has (the plan keeps it for the bonus part, a main task practises it) has a column too.
  const stray = {...main, concept: {tasks: [run.concept.tasks[0]]}, skill_block: {code: 'P1', main: {shares: {Git: 1}, tasks: [{label: '01', shares: {Git: 1, SQL: 1}}]}, bonus: {shares: {}, tasks: []}}};
  assert.deepEqual(cells(js('skillsTable')(stray)), [['Задание', 'Git', 'SQL'], ['Основная часть — доля навыка в проекте', '100%', ''], ['1. Первое', '100%', '100%']]);
  const older = {...run, skill_block: null};
  assert.deepEqual(JSON.parse(JSON.stringify(js('taskShares')(older))), [{}, {}, {}, {}]);
  assert.deepEqual(js('taskRow')(older, run.concept.tasks[1], 2).byClass('tag').map((tag) => tag.textContent), ['Бонус']);
  assert.equal(js('taskRow')(older, run.concept.tasks[0], 1).byClass('chips').length, 0);
  // In the message of a concept: the block is offered when code counted one, folded; opened, it is the table.
  const whole = (given) => ({...given, concept: {...given.concept, student_result: 'Отчёт', program_summary: '', project_role: '', prerequisites: [], later_use: [],
    out_of_scope: [], material_gaps: [], decisions: [], questions: []}});
  js('S').thread = {runs: ['r1']}; js('S').runs = {r1: run};
  const titles = (message) => message.byClass('title').map((node) => node.textContent);
  assert.ok(titles(js('conceptMessage')(whole(run), 1)).includes('Развесовка навыков по заданиям'));
  assert.ok(!titles(js('conceptMessage')(whole(older), 1)).includes('Развесовка навыков по заданиям'));
  assert.equal(js('conceptMessage')(whole(run), 1).byTag('table').length, 0);
  js('S').open.set('skills-r1', true);
  assert.equal(js('conceptMessage')(whole(run), 1).byTag('table').length, 1);
  assert.deepEqual(js('conceptMessage')(whole(run), 1).byClass('task').map((row) => row.byClass('tag').map((tag) => tag.textContent)), [['Git'], ['Бонус', 'SQL'], ['Git', 'Python'], []]);
});

test('on the page: the block is offered as a file, and a project with a bonus part in the plan opens with bonus tasks allowed', async () => {
  const js = page({dom: true});
  const el = js('$');
  const S = js('S');
  // The download of the block stands with its table.
  const run = {id: 'r7', concept: {tasks: [{title: 'Первое', optional: false}]}, skill_block: {code: 'P1', main: {shares: {Git: 1}, tasks: [{label: '01', shares: {Git: 1}}]}, bonus: {shares: {}, tasks: []}}};
  const link = js('skillsTable')(run).byTag('a')[0];
  assert.equal(link.getAttribute('href'), 'api/runs/r7/export/skills');
  assert.equal(link.textContent, 'Скачать блок для паспорта (CSV)');
  // The form: what a row of the plan puts into it.
  js('fillBrief = (brief) => { S.filled = brief; }; saveIntake = () => {}; refreshIntakeWorkload = () => {}; countMore = () => {}; updateStart = () => {}; notify = () => {};');
  const plan = (tab) => ({filename: 'Паспорт.xlsx', columns: {}, missing_fields: [], unmapped_columns: [], warnings: [], skill_tabs: ['Старая', 'Новая'], skills_tab: tab,
    rows: [{id: 'p1', block: 'Б', title: 'С бонусом', kind: 'project', skills: tab ? 'Git' : '', bonus: Boolean(tab)},
           {id: 'p2', block: 'Б', title: 'Без бонуса', kind: 'project', skills: tab ? 'SQL' : '', bonus: false}]});
  S.curriculum = plan('Новая'); S.rowId = 'p1';
  js('applyRow')();
  assert.equal(S.filled.bonus, true);
  S.rowId = 'p2';
  js('applyRow')();
  assert.equal(S.filled.bonus, false);   // another project of the plan is another answer
  // A plan read without a skills tab (a CSV, a passport whose tab is not chosen, a plan kept by an earlier page) knows
  // nothing of a bonus: the box is not among what the row fills, and a tick put by hand stays.
  S.curriculum = plan('');
  js('applyRow')();
  assert.equal('bonus' in S.filled, false);
  assert.equal(S.filled.skills, '');
  // The tab is chosen after the project: the bonus part it brings allows the bonus, and never takes away what was ticked by hand.
  const bonus = {checked: false, type: 'checkbox', tagName: 'INPUT'}, skillsField = {value: '', tagName: 'TEXTAREA'};
  el('#briefForm').elements = {namedItem: (name) => (name === 'bonus' ? bonus : skillsField)};
  js(`api = async (url, options) => S.answer(options.body.get('skills_tab'))`);
  S.answer = plan; S.curriculum = plan(''); S.rowId = 'p1'; S.planFile = new File(['x'], 'Паспорт.xlsx');
  await js('chooseSkillsTab')('Новая');
  assert.equal(bonus.checked, true);
  assert.equal(skillsField.value, 'Git');
  S.rowId = 'p2'; bonus.checked = true;
  await js('chooseSkillsTab')('Старая');
  assert.equal(bonus.checked, true);
  assert.equal(skillsField.value, 'SQL');
});

test('on the page: the address of the repository comes from the GitLab column of the plan and goes with the brief', () => {
  const js = page({dom: true});
  const S = js('S');
  js('fillBrief = (brief) => { S.filled = brief; }; saveIntake = () => {}; refreshIntakeWorkload = () => {}; countMore = () => {}; updateStart = () => {}; notify = () => {};');
  S.curriculum = {filename: 'УП.xlsx', columns: {repository: ['GitLab']}, missing_fields: [], unmapped_columns: [], warnings: [], skill_tabs: [], skills_tab: '',
    rows: [{id: 'p1', block: 'Б', title: 'Есть репозиторий', kind: 'project', repository: 'https://gitlab.example/school/DA1_Legend'},
           {id: 'p2', block: 'Б', title: 'Будущий проект', kind: 'project'}]};
  S.rowId = 'p1';
  js('applyRow')();
  assert.equal(S.filled.repository, 'https://gitlab.example/school/DA1_Legend');
  // A project that is not made yet has an empty cell: the address of the project chosen before must not stay in the form.
  S.rowId = 'p2';
  js('applyRow')();
  assert.equal(S.filled.repository, '');
  assert.equal(js('COLUMN_LABELS').repository, 'GitLab');
  assert.ok(js('TEXT_FIELDS').includes('repository') && js('MORE_FIELDS').includes('repository'));
});

// Вопросы по одному. Методолог жаловался: выдаёт три вопроса, ответил на первый — и листай
// вверх за вторым, потом ещё выше за третьим. Текущий вопрос стоит над полем ввода, и на его
// место встаёт следующий; список целиком остаётся в ленте, чтобы можно было ответить разом.
function threadWithQuestions(messages = []) {
  const run = {
    id: 'r1', status: 'input_review', request: {brief: {outcomes: ''}},
    input_card: {questions: [
      {question: 'Какой уровень аудитории?', why: 'В УП не указан'},
      {question: 'Сколько часов на проект?', why: 'Расходятся форма и план'},
      {question: 'Нужна ли групповая работа?', why: 'Формат не задан'},
    ]},
    input_attempts: [{questions: [{blocking: true}, {blocking: false}, {blocking: false}]}],
  };
  return {run, thread: {runs: ['r1'], messages}};
}

function withThread(js, {run, thread}) {
  js('S').runs = {r1: run};
  js('S').thread = thread;
  return run;
}

test('unanswered questions: an applied answer and one waiting are both out of the way', () => {
  const js = page();
  const run = withThread(js, threadWithQuestions([
    {run_id: 'r1', status: 'applied', patches: [{question_numbers: [1]}]},
    {run_id: 'r1', status: 'pending', patches: [{question_numbers: [2]}]},
  ]));

  // Array.from: список приходит из песочницы vm, и его прототип не равен здешнему.
  assert.deepEqual(Array.from(js('unansweredQuestions')(run), (q) => q.number), [3]);
});

test('the question over the composer is the first unanswered one and says how many are left', () => {
  const js = page();
  const run = withThread(js, threadWithQuestions([
    {run_id: 'r1', status: 'applied', patches: [{question_numbers: [1]}]},
  ]));

  const node = js('askNowBlock')(run);
  const text = node.textContent;
  assert.match(text, /Сколько часов на проект\?/);
  assert.match(text, /2 из 3/, text);
  assert.doesNotMatch(text, /Какой уровень аудитории/);
});

test('a blocking question is marked important over the composer too', () => {
  const js = page();
  const run = withThread(js, threadWithQuestions());

  assert.match(js('askNowBlock')(run).textContent, /Важно/);
});

test('with every question answered there is nothing over the composer', () => {
  const js = page();
  const run = withThread(js, threadWithQuestions([
    {run_id: 'r1', status: 'applied', patches: [{question_numbers: [1, 2, 3]}]},
  ]));

  assert.equal(js('askNowBlock')(run), null);
});

test('the number of the question is put into the message, so the answer is not matched by guesswork', () => {
  const js = page();
  const run = withThread(js, threadWithQuestions([
    {run_id: 'r1', status: 'applied', patches: [{question_numbers: [1]}]},
  ]));

  assert.equal(js('askNowPrefill')(run, ''), 'Вопрос 2: ');
  assert.equal(js('askNowPrefill')(run, 'уже пишу'), null, 'начатое сообщение не трогаем');
});

// Ссылки проверки актуальности, которые не открылись. Методолог жаловался на ссылки, которые
// у него не открываются; код теперь их пробует и называет. Проверка идёт с сервера, и отчёт
// об этом говорит, чтобы молчание не читалось как обещание.
function runWithFreshness(unreachable) {
  return {
    id: 'r1', status: 'review', request: {brief: {outcomes: ''}},
    freshness: {
      checked_at: '2026-10-27T10:00:00Z', model: 'perplexity/sonar', summary: 'Один пункт устарел.',
      items: [], ideas: [], sources: ['https://a.example/1', 'https://b.example/2'],
      unreachable, dropped_references: 0,
    },
  };
}

test('sources that did not open are named, and the report says the check ran from the server', () => {
  const js = page();
  const run = runWithFreshness(['https://b.example/2']);
  js('S').runs = {r1: run};
  js('S').thread = {runs: ['r1'], messages: []};

  const text = js('freshnessMessage')(run).textContent;
  assert.match(text, /b\.example\/2/);
  assert.match(text, /с сервера/, text);
  assert.doesNotMatch(text, /a\.example\/1/, 'открывшуюся ссылку в список не кладём');
});

test('when every source opens, nothing is said about unreachable ones', () => {
  const js = page();
  const run = runWithFreshness([]);
  js('S').runs = {r1: run};
  js('S').thread = {runs: ['r1'], messages: []};

  assert.doesNotMatch(js('freshnessMessage')(run).textContent, /не открыл/);
});
