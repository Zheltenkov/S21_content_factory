// The page that shows a README as the repository will: tables, quotations, marks inside a line, and links to the
// files of the project. Run: node --test project_generator_lab/tests/js
import assert from 'node:assert/strict';
import {test} from 'node:test';

import {documentPage} from './page.mjs';

const text = (el) => el.textContent;
const rows = (table) => table.byTag('tr').map((row) => row.children.map(text));
const tags = (root) => root.children.map((el) => el.tagName);

test('a table of the README is a table: head, rows, marks inside the cells', () => {
  const {root} = documentPage().render([
    '**Материалы:**', '',
    '| Что нужно | Где найти |', '|---|---|',
    '| Карточка **варианта** помощника | [helper-card.md](materials/helper-card.md) |',
    '| Журнал `JSONL` | [calls.txt](materials/calls.txt) |', '', 'Текст после таблицы.'].join('\n'));
  assert.deepEqual(tags(root), ['P', 'DIV', 'P']);
  const [table] = root.byTag('table');
  assert.deepEqual(rows(table), [['Что нужно', 'Где найти'], ['Карточка варианта помощника', 'helper-card.md'], ['Журнал JSONL', 'calls.txt']]);
  assert.deepEqual([table.byTag('th').length, table.byTag('td').length, table.byTag('thead').length, table.byTag('tbody').length], [2, 4, 1, 1]);
  assert.deepEqual([table.byTag('strong').map(text), table.byTag('code').map(text)], [['варианта'], ['JSONL']]);
  assert.equal(table.byTag('a')[0].getAttribute('href'), 'document?run=r1&doc=file&path=materials%2Fhelper-card.md');
  // The frame scrolls a wide table inside itself.
  assert.equal(root.children[1].getAttribute('class'), 'table-wrap');
  // Nothing of the table is left as a line of bars.
  assert.ok(!root.byTag('p').some((p) => text(p).includes('|')));
});

test('the cells of a table: a break, a bar that is not the end of a cell, an empty cell, a row too short or too long', () => {
  const {root} = documentPage().render([
    '| Ввод | Вывод |', '|---|---|',
    '| `3 5` | `8` |',
    '| `a\\|b`<br>`c` | `ошибка` |',
    '| пустой ввод | |',
    '| a \\| b | один | лишняя |',
    '| короткая |'].join('\n'));
  const [table] = root.byTag('table');
  assert.deepEqual(rows(table), [['Ввод', 'Вывод'], ['3 5', '8'], ['a|bc', 'ошибка'], ['пустой ввод', ''], ['a | b', 'один'], ['короткая', '']]);
  const cell = table.byTag('td')[2];
  assert.deepEqual(cell.children.map((c) => c.tagName), ['CODE', 'BR', 'CODE']);
  assert.deepEqual(cell.byTag('code').map(text), ['a|b', 'c']);
});

test('the line under the head says how the columns are aligned, and whether this is a table at all', () => {
  const js = documentPage();
  const [table] = js.render('| Задание | Часы | Доля |\n|:---|:-:|--:|\n| 1 | 2 | 0,5 |').root.byTag('table');
  assert.deepEqual(table.byTag('td').map((td) => td.getAttribute('class')), ['al-left', 'al-center', 'al-right']);
  assert.deepEqual(js.render('a | b\n--- | ---\n1 | 2').root.byTag('td').map(text), ['1', '2']);  // no bars at the edges
  // A table may stand right under a line of text. Its rows go on to an empty line — a line without a bar is a row of
  // one cell, as in the repository — or to a line that begins another block.
  const under = js.render('Таблица:\n| a | b |\n|---|---|\n| 1 | 2 |\nещё строка\n\nуже текст');
  assert.deepEqual([tags(under.root), under.root.byTag('p').map(text), rows(under.root.byTag('table')[0])],
    [['P', 'DIV', 'P'], ['Таблица:', 'уже текст'], [['a', 'b'], ['1', '2'], ['ещё строка', '']]]);
  for (const next of ['## Дальше', '> цитата', '```\nкод\n```', '---']) {
    const cut = js.render(`| a | b |\n|---|---|\n| 1 | 2 |\n${next}`);
    assert.deepEqual([cut.root.byTag('tr').length, cut.root.children.length], [2, 2], next);
  }
  // Not a table: no line of dashes; a line of dashes with another number of cells; a bar inside a sentence.
  for (const plain of ['| a | b |\n| 1 | 2 |', '| a | b |\n|---|\n| 1 | 2 |', 'Выбери a | b и запиши.', 'Строка\n---']) {
    assert.equal(js.render(plain).root.byTag('table').length, 0, plain);
  }
  // Inside a block of code a table is code.
  const fenced = js.render('```\n| a | b |\n|---|---|\n```');
  assert.deepEqual([fenced.root.byTag('table').length, fenced.root.byTag('pre').map(text)], [0, ['| a | b |\n|---|---|']]);
});

test('a quotation is a quotation, with what stands inside it', () => {
  const {root} = documentPage().render('До.\n\n> **Место для картинки:** тёмная комната\n\n> Первая строка\n> вторая строка.\n>\n> Второй абзац.\n\nПосле.');
  assert.deepEqual(tags(root), ['P', 'BLOCKQUOTE', 'BLOCKQUOTE', 'P']);
  const [place, quote] = root.byTag('blockquote');
  assert.deepEqual([text(place), place.byTag('strong').map(text)], ['Место для картинки: тёмная комната', ['Место для картинки:']]);
  assert.deepEqual(quote.byTag('p').map(text), ['Первая строка вторая строка.', 'Второй абзац.']);
});

test('the marks inside a line', () => {
  const js = documentPage();
  const line = (markdown) => js.render(markdown).root.children[0];
  // An escaped mark is the mark itself: the generator escapes what a quotation of a page says.
  assert.equal(text(line('git rebase \\[\\<options\\>\\] и \\_\\_init\\_\\_ — \\*не курсив\\*')), 'git rebase [<options>] и __init__ — *не курсив*');
  assert.equal(line('git rebase \\[\\<options\\>\\] и \\*не курсив\\*').children.filter((c) => c.tagName).length, 0);
  const loading = line('***LOADING…***');
  assert.deepEqual([loading.tagName, loading.byTag('strong').length, loading.byTag('em').map(text)], ['P', 1, ['LOADING…']]);
  assert.deepEqual(line('**Получен Quest 1.** Создай `src/a.c`.').children.map((c) => c.tagName || 'text'), ['STRONG', 'text', 'CODE', 'text']);
  // An underscore inside a name is a letter of the name — in Russian words too; around a word it is italic.
  assert.equal(line('Файл tool_trace_final.png и слово_в_слове.').byTag('em').length, 0);
  assert.deepEqual(line('Все файлы — в папке _src/_ репозитория.').byTag('em').map(text), ['src/']);
  // Code is read to the letter.
  assert.deepEqual(line('Запусти `python3 src/a.py *.txt` и __main__.').byTag('code').map(text), ['python3 src/a.py *.txt']);
  // An address written out is a link to it; a break is a break; a picture is named, not loaded.
  const source = line('Источник: <https://git-scm.example.org/docs/roles>.').byTag('a')[0];
  assert.deepEqual([text(source), source.getAttribute('href'), source.getAttribute('target')], ['https://git-scm.example.org/docs/roles', 'https://git-scm.example.org/docs/roles', '_blank']);
  assert.deepEqual(line('первая<br>вторая<BR/>третья').children.map((c) => c.tagName || text(c)), ['первая', 'BR', 'вторая', 'BR', 'третья']);
  assert.equal(text(line('![схема ролей](misc/images/roles.png)')), '[картинка: схема ролей]');
});

test('a link to a file of the project opens the file here, as the repository would', () => {
  const href = (js, markdown) => { const a = js.render(markdown).root.byTag('a')[0]; return [a.getAttribute('href'), a.getAttribute('target')]; };
  const readme = documentPage('?run=r 1&doc=preview');
  assert.deepEqual(href(readme, '[materials/theory.md](materials/theory.md#2-роли)'), ['document?run=r%201&doc=file&path=materials%2Ftheory.md#2-роли', null]);
  assert.deepEqual(href(readme, '[Чек-лист](./check-list.yml)'), ['document?run=r%201&doc=file&path=check-list.yml', null]);
  assert.deepEqual(href(readme, '[Теория](#теория)'), ['#теория', null]);
  assert.deepEqual(href(readme, '[Git](https://git-scm.example.org/docs)'), ['https://git-scm.example.org/docs', '_blank']);
  // Another scheme, an address without one, a path out of the repository lead nowhere.
  for (const bad of ['javascript:alert(1)', 'data:text/html,x', '//evil.example.org/x', '../../etc/passwd', 'file:///C:/x']) {
    assert.deepEqual(href(readme, `[x](${bad})`), ['#', null], bad);
  }
  // Inside a file the paths are from its own folder, as in the repository.
  const file = documentPage('?run=r1&doc=file&path=materials/theory.md');
  assert.deepEqual(href(file, '[данные](team.csv)'), ['document?run=r1&doc=file&path=materials%2Fteam.csv', null]);
  assert.deepEqual(href(file, '[README](../README.md#теория)'), ['document?run=r1&doc=file&path=README.md#теория', null]);
  assert.deepEqual(href(file, '[x](/materials/a.md)'), ['document?run=r1&doc=file&path=materials%2Fa.md', null]);
  assert.equal(file('source'), 'api/content/r1/file?path=materials%2Ftheory.md');
  assert.equal(file('title'), 'materials/theory.md');
  assert.equal(file('isMarkdown()'), true);
  assert.equal(documentPage('?run=r1&doc=file&path=materials/calls.txt')('isMarkdown()'), false);
});

test('headings get the anchors the table of contents links to; lists and code stay as they were', () => {
  const {root, toc} = documentPage().render([
    '# Проект', '', '## Содержание', '', '- [Задание 1. Роли: кто за что?](#задание-1-роли-кто-за-что)', '  - [Теория](#теория)', '',
    '## Задание 1. Роли: кто за что?', '', '1. Собери таблицу', '   и сверь её', '2. Сохрани', '', '### Теория', '', '~~~', '## не заголовок', '~~~', '', '## Теория', '', '---'].join('\n'));
  assert.deepEqual(root.find((el) => /^H\d$/.test(el.tagName)).map((el) => [el.tagName, el.getAttribute('id')]),
    [['H1', 'проект'], ['H2', 'содержание'], ['H2', 'задание-1-роли-кто-за-что'], ['H3', 'теория'], ['H2', 'теория-1']]);
  assert.deepEqual(toc, [[2, 'Содержание', 'содержание'], [2, 'Задание 1. Роли: кто за что?', 'задание-1-роли-кто-за-что'], [3, 'Теория', 'теория'], [2, 'Теория', 'теория-1']]);
  assert.deepEqual([root.byTag('ul').length, root.byTag('ol')[0].byTag('li').map(text), root.byTag('pre').map(text), root.byTag('hr').length],
    [2, ['Собери таблицуи сверь её', 'Сохрани'], ['## не заголовок'], 1]);
});

test('what the generator writes around a link: an address in angle brackets, brackets inside an address, a title with marks', () => {
  const js = documentPage();
  const first = (markdown) => js.render(markdown).root.byTag('a')[0];
  // The source of an excerpt is always written as [title](<address>) (content/render.py: address).
  const source = first('**Источник:** [git-rebase \\[options\\]](<https://git-scm.example.org/docs/git-rebase>)');
  assert.deepEqual([text(source), source.getAttribute('href'), source.getAttribute('target'), source.getAttribute('rel')],
    ['git-rebase [options]', 'https://git-scm.example.org/docs/git-rebase', '_blank', 'noopener']);
  assert.equal(first('[Python](https://ru.wikipedia.example.org/wiki/Python_(язык)) — язык').getAttribute('href'), 'https://ru.wikipedia.example.org/wiki/Python_(язык)');
  assert.equal(first('[файл](<materials/мой файл.md>)').getAttribute('href'), 'document?run=r1&doc=file&path=materials%2F%D0%BC%D0%BE%D0%B9%20%D1%84%D0%B0%D0%B9%D0%BB.md');
  assert.equal(text(js.render('![схема](<misc/images/схема ролей.png>)').root), '[картинка: схема]');
  // An address written out is a link, as in the repository; the full stop of the sentence is not a part of it.
  const bare = js.render('Документация: https://git-scm.example.org/docs/git_rebase. Или (https://example.org/a_b), или `https://not.a.link`.').root;
  assert.deepEqual(bare.byTag('a').map((a) => [text(a), a.getAttribute('rel')]), [['https://git-scm.example.org/docs/git_rebase', 'noopener'], ['https://example.org/a_b', 'noopener']]);
  assert.equal(text(bare), 'Документация: https://git-scm.example.org/docs/git_rebase. Или (https://example.org/a_b), или https://not.a.link.');
  assert.equal(bare.byTag('em').length, 0);
});

test('a numbered list goes on from the number written: a block of code, a table or a quotation does not begin it again', () => {
  const js = documentPage();
  const steps = js.render(['1. Создай файл', '2. Запиши в него:', '```c', 'int main(void) { return 0; }', '```', '3. Собери', '4. Запусти', '',
    '| a | b |', '|---|---|', '| 1 | 2 |', '', '5. Сравни', '> подсказка', '6. Сдай'].join('\n')).root;
  assert.deepEqual(steps.byTag('ol').map((ol) => [ol.getAttribute('start'), ol.byTag('li').map(text)]),
    [[null, ['Создай файл', 'Запиши в него:']], ['3', ['Собери', 'Запусти']], ['5', ['Сравни']], ['6', ['Сдай']]]);
  // A block of code indented under a step — as the generator writes the code of a step — is a part of the step:
  // one list, the code inside its item, without the indent; the list inside a step is inside it too.
  const whole = js.render(['1. Создай файл src/a.c:', '   ```c', '   int main(void) {', '       return 0;', '   }', '   ```', '2. Собери проект:', '   - запусти make', '   - проверь вывод',
    '3. Запусти тесты', '', 'Должно получиться:'].join('\n')).root;
  assert.deepEqual(tags(whole), ['OL', 'P']);
  const [first, second, third] = whole.children[0].children;
  assert.deepEqual([whole.byTag('ol').length, whole.children[0].getAttribute('start'), first.byTag('pre').map(text), second.byTag('li').map(text), text(third)],
    [1, null, ['int main(void) {\n    return 0;\n}'], ['запусти make', 'проверь вывод'], 'Запусти тесты']);
  // A list that begins with another number begins with it; items apart by empty lines keep their numbers.
  assert.deepEqual(js.render('3. три\n4. четыре').root.byTag('ol').map((ol) => ol.getAttribute('start')), ['3']);
  assert.deepEqual(js.render('1. раз\n\n2. два\n\n3. три').root.byTag('ol').map((ol) => ol.getAttribute('start')), [null, '2', '3']);
  assert.deepEqual(js.render('- раз\n- два').root.byTag('ul').map((ul) => ul.getAttribute('start')), [null]);
});

test('lists inside lists, three deep; a box to tick', () => {
  const js = documentPage();
  const {root} = js.render(['- Блок', '  - Проект', '    - Задание 1', '    - Задание 2', '  - Второй проект', '- Второй блок', '  1. шаг', '  2. шаг'].join('\n'));
  assert.deepEqual(tags(root), ['UL']);
  const [outer] = root.children;
  assert.deepEqual(outer.children.map((li) => li.children[0].textContent), ['Блок', 'Второй блок']);
  const [inner] = outer.children[0].byTag('ul');
  assert.deepEqual(inner.children.map((li) => li.children[0].textContent), ['Проект', 'Второй проект']);
  assert.deepEqual(inner.children[0].byTag('ul')[0].children.map(text), ['Задание 1', 'Задание 2']);
  assert.deepEqual(outer.children[1].byTag('ol')[0].children.map(text), ['шаг', 'шаг']);
  const todo = js.render('- [ ] договориться о встрече\n- [x] собрать таблицу\n- [не флажок] текст').root.byTag('li');
  assert.deepEqual(todo.map((li) => [li.getAttribute('class'), li.byTag('input').map((box) => [box.getAttribute('disabled'), box.getAttribute('checked')]), text(li)]),
    [['todo', [['', null]], ' договориться о встрече'], ['todo', [['', '']], ' собрать таблицу'], [null, [], '[не флажок] текст']]);
});

test('code is closed by as many backticks as opened it; stars and underscores around spaces are themselves', () => {
  const js = documentPage();
  const line = (markdown) => js.render(markdown).root.children[0];
  // An example with a backtick inside is written between two (content/render.py: code).
  const code = (markdown) => line(markdown).byTag('code').map(text);
  assert.deepEqual(code('Запусти `` echo `date +%Y` `` и ```` ``` ````, потом `` `a` `` и ` `.'), ['echo `date +%Y`', '```', '`a`', ' ']);
  assert.deepEqual([code('``a``b``'), text(line('``a``b``'))], [['a'], 'ab``']);   // two backticks close what two opened
  assert.deepEqual(code('` a `, `  `, ` a`'), ['a', '  ', ' a']);                  // a space at each edge is the marks'; spaces alone are code
  const cell = js.render('| Ввод | Вывод |\n|---|---|\n| `` echo `date` `` | `2026` |').root.byTag('td');
  assert.deepEqual(cell.map((td) => td.byTag('code').map(text)), [['echo `date`'], ['2026']]);
  assert.equal(text(line('Незакрытый `код и ``ещё')), 'Незакрытый `код и ``ещё');
  for (const plain of ['2 * 3 * 4 = 24', 'Найди *.txt и *.md в папке', 'a * b, c ** d ** e', 'snake_case и _ отдельно _ тоже', '__ не жирный __']) {
    const shown = line(plain);
    assert.deepEqual([text(shown), shown.byTag('em').length, shown.byTag('strong').length], [plain, 0, 0], plain);
  }
  assert.deepEqual(line('*курсив*, **жирный**, _тоже курсив_, __тоже жирный__').children.filter((c) => c.tagName).map((c) => c.tagName), ['EM', 'STRONG', 'EM', 'STRONG']);
});

test('a heading: hashes that close it, an underscore in its anchor, the same title twice — inside a quotation too', () => {
  const {root, toc} = documentPage().render(['## Quest 3. ft_strlen ##', '', '### __init__ и main_loop', '', '> ## Quest 3. ft_strlen', '', '## C#', '## #'].join('\n'));
  assert.deepEqual(root.find((el) => /^H\d$/.test(el.tagName)).map((el) => [text(el), el.getAttribute('id')]),
    [['Quest 3. ft_strlen', 'quest-3-ft_strlen'], ['init и main_loop', '__init__-и-main_loop'], ['Quest 3. ft_strlen', 'quest-3-ft_strlen-1'], ['C#', 'c'], ['', '']]);
  assert.deepEqual(toc.map((entry) => entry[2]), ['quest-3-ft_strlen', '__init__-и-main_loop', 'quest-3-ft_strlen-1', 'c', '']);
});

test('a block of code is closed by its own mark only; the lines of another computer end as ours do', () => {
  const js = documentPage();
  assert.deepEqual(js.render('```\n~~~\nкод\n``\n```\nтекст').root.children.map((el) => [el.tagName, text(el)]), [['PRE', '~~~\nкод\n``'], ['P', 'текст']]);
  assert.deepEqual(js.render('~~~~\n~~~\n```\n~~~~~\nтекст').root.children.map((el) => [el.tagName, text(el)]), [['PRE', '~~~\n```'], ['P', 'текст']]);
  const windows = js.render('| a | b |\r\n|---|---|\r\n| 1 | 2 |\r\n\r\n## Заголовок\r\n\r\nпервая\rвторая');
  assert.deepEqual([rows(windows.root.byTag('table')[0]), windows.root.byTag('h2').map(text), windows.root.byTag('p').map(text)],
    [[['a', 'b'], ['1', '2']], ['Заголовок'], ['первая вторая']]);
});

test('a text of one character repeated does not hang the page', () => {
  // What a model caught in a loop writes — and what the independent review of 07.10.2026 hung the page with.
  const N = 60000;
  const hostile = {
    brackets: '['.repeat(N), stars: '*'.repeat(N), 'star words': '*a '.repeat(N / 3), underscores: '_'.repeat(N), backticks: '`'.repeat(N),
    'backtick words': '`a``'.repeat(N / 4), 'open links': '[a]('.repeat(N / 4), addresses: 'http://'.repeat(N / 7), 'angle addresses': '<https://a'.repeat(N / 10),
    'dashes under a row': 'a | b\n' + ' '.repeat(N / 2) + '-' + ' '.repeat(N / 2) + 'x', 'a wide rule': '-' + ' '.repeat(N) + '-' + ' '.repeat(N) + 'x',
    'a heading of spaces': '# a' + ' '.repeat(N) + 'b' + ' '.repeat(N) + '#x', 'a heading of hashes': '# ' + 'a #'.repeat(N / 3) + 'x',
    'quotations in quotations': '>'.repeat(N) + ' x', 'an indented item': ' '.repeat(N) + '- x', pictures: '!['.repeat(N / 2), escapes: '\\'.repeat(N),
    'brackets in an address': '[a](' + '('.repeat(N), 'a long table': '| a | b |\n|---|---|\n' + '| 1 | 2 |\n'.repeat(N / 10),
  };
  const js = documentPage();
  for (const [name, markdown] of Object.entries(hostile)) {
    const started = Date.now();
    const {root} = js.render(markdown);
    assert.ok(Date.now() - started < 1500, `${name}: ${Date.now() - started} ms`);
    assert.ok(root.children.length >= 1, name);
  }
  // Quotations are read twelve deep; the rest of the marks stay text.
  const deep = js.render('>'.repeat(40) + ' дно').root;
  assert.deepEqual([deep.byTag('blockquote').length, text(deep)], [12, '>'.repeat(28) + ' дно']);
});

test('the page loads a document: what it shows of a README, of a file that is not Markdown, of a refusal', async () => {
  const served = (text, ok = true) => () => ({ok, text});
  const readme = documentPage('?run=r1&doc=preview', {hash: '#%D1%82%D0%B5%D0%BE%D1%80%D0%B8%D1%8F', answer: served('# Проект\r\n\r\n## Теория\r\n\r\n| a |\r\n|---|\r\n| 1 |\r\n')});
  await readme('load()');
  assert.deepEqual([readme.document.title, text(readme.part('docTitle')), readme.part('raw').href, readme.part('back').hidden],
    ['README.md · Подготовка проекта', 'README.md', 'api/content/r1/preview', true]);
  assert.deepEqual([readme.part('doc').byTag('table').length, readme.part('toc').children.map((a) => [text(a), a.getAttribute('href')]), readme.scrolled],
    [1, [['Теория', '#теория']], ['теория']]);
  // A file of the project that is not Markdown is shown to the letter; so is the check-list.
  const data = documentPage('?run=r1&doc=file&path=materials/team.csv', {answer: served('name,role\n**Анна**,CEO\n')});
  await data('load()');
  assert.deepEqual([data.part('doc').children.map((el) => [el.tagName, text(el)]), data.part('toc').children.length, data.part('back').hidden, data.part('back').href],
    [[['PRE', 'name,role\n**Анна**,CEO\n']], 0, false, 'document?run=r1&doc=preview&v=7']);
  const checklist = documentPage('?run=r1&doc=checklist', {answer: served('# не заголовок\nname: x\n')});
  await checklist('load()');
  assert.deepEqual(checklist.part('doc').children.map((el) => el.tagName), ['PRE']);
  // A refusal of the server is said in its words; a place the address names wrongly is no reason not to show the document.
  const missing = documentPage('?run=r1&doc=file&path=src/a.py', {answer: served(JSON.stringify({detail: 'Файла «src/a.py» в проекте нет.'}), false)});
  await missing('load()');
  assert.equal(text(missing.part('doc')), 'Не удалось загрузить документ. Файла «src/a.py» в проекте нет.');
  const odd = documentPage('?run=r1&doc=preview', {hash: '#100%', answer: served('# Проект\n')});
  await odd('load()');
  assert.deepEqual([odd.part('doc').byTag('h1').map(text), odd.scrolled], [['Проект'], []]);
  // Only the documents the page knows are documents: anything else in the address is the whole text.
  for (const name of ['__proto__', 'constructor', 'toString', 'nothing']) {
    assert.deepEqual([documentPage(`?run=r1&doc=${name}`)('title'), documentPage(`?run=r1&doc=${name}`)('source')], ['Весь текст', 'api/content/r1/text'], name);
  }
});
