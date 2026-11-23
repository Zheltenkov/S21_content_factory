/* Renders a Markdown document of the content stage as a page, the way GitLab will show it in the repository:
   headings with its anchors, lists, tables, quotations, code. Every text goes in as text, never as HTML. */
'use strict';

const DOCS = {
  text: ['Весь текст', 'text'],
  preview: ['README.md', 'preview'],
  checklist: ['check-list.yml', 'check-list-preview'],
};
const params = new URLSearchParams(location.search);
const run = params.get('run') || '';
// A file of the project by its path from the root of the repository: «materials/theory.md». The README links to its
// files by such paths, and here they open as they will in the repository.
const filePath = params.get('doc') === 'file' ? (params.get('path') || '') : '';
// Only the names written above name a document: «__proto__» in the address is not one.
const named = Object.prototype.hasOwnProperty.call(DOCS, params.get('doc') || '') ? DOCS[params.get('doc')] : DOCS.text;
const [title, path] = filePath ? [filePath, `file?path=${encodeURIComponent(filePath)}`] : named;
const source = `api/content/${encodeURIComponent(run)}/${path}`;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => { if (v !== null && v !== undefined) node.setAttribute(k, v); });
  children.flat().forEach((c) => node.append(c));
  return node;
}

// A path of the repository without «.» and «..»: «materials/../README.md» is «README.md». Null — the path leaves the
// repository or is no path at all.
function normalized(target) {
  const parts = [];
  for (const part of target.split('/')) {
    if (part === '' || part === '.') continue;
    if (part === '..') { if (!parts.length) return null; parts.pop(); } else parts.push(part);
  }
  return parts.length ? parts.join('/') : null;
}

// Where a link of the document leads from this page. An anchor stays on the page; an address of the web opens in a
// new tab; a path is a file of the project — relative to the folder of the file being read, as in the repository —
// and opens here as a document. Anything else (another scheme) leads nowhere.
function resolve(href, base = filePath) {
  if (href.startsWith('#')) return {href};
  if (/^https?:\/\//i.test(href)) return {href, external: true};
  if (/^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith('//')) return {href: '#'};
  const [target, anchor = ''] = href.split('#');
  const folder = base.includes('/') ? base.slice(0, base.lastIndexOf('/') + 1) : '';
  const file = normalized(target.startsWith('/') ? target : folder + target);
  if (!file) return {href: '#'};
  return {href: `document?run=${encodeURIComponent(run)}&doc=file&path=${encodeURIComponent(file)}${anchor ? `#${anchor}` : ''}`};
}

// Inline marks: \escapes, `code`, <br>, <address>, pictures, [link](url), ***both***, **bold**, *italic*, __bold__,
// _italic_, an address written out. Unknown syntax stays text. Every repetition is bounded: the page reads what a
// model wrote, and a line of one character repeated — a model caught in a loop — must not hang the tab.
const TARGET = String.raw`(?:<([^<>\n]{1,2000})>|((?:[^()\s]|\([^()\s]{0,300}\)){1,2000}))`;  // <address> or address, brackets in pairs
const INLINE = new RegExp([
  String.raw`\\([\\\x60*_{}\[\]()<>#+\-.!|~&=])`,                              // 1: an escaped mark is the mark itself
  // Code is closed by as many backticks as opened it: «`` echo `date` ``» is one piece of code.
  String.raw`(?<!\x60)(\x60{1,8})(?!\x60)([\s\S]{0,2000}?[^\x60])\2(?!\x60)`,       // 2, 3
  String.raw`(<br\s{0,3}\/?>)`,                                                 // 4: a break inside a cell or a paragraph
  String.raw`<(https?:\/\/[^\s<>]{1,2000})>`,                                   // 5: an address in angle brackets
  String.raw`!\[([^\]]{0,300})\]\(` + TARGET + String.raw`\)`,                  // 6, 7, 8: a picture
  String.raw`\[((?:\\.|[^\]\\]){1,500})\]\(` + TARGET + String.raw`\)`,         // 9, 10, 11: a link
  // A mark opens before a character and closes after one: «2 * 3 * 4» and «*.txt *.md» are stars, not italics.
  String.raw`\*\*\*(?!\s)((?:\\.|[^*\\]){1,2000})(?<!\s)\*\*\*`,                // 12: bold and italic
  String.raw`\*\*(?!\s)((?:\\.|[^*\\]){1,2000})(?<!\s)\*\*`,                    // 13: bold
  String.raw`(?<![*\p{L}\p{N}_])\*(?![\s*])((?:\\.|[^*\\\n]){1,2000})(?<!\s)\*(?![*\p{L}\p{N}_])`,   // 14: italic
  // An underscore inside a word — «tool_trace», «слово_слово» — is a letter of the name, not a mark.
  String.raw`(?<![\p{L}\p{N}_])__(?!\s)((?:\\.|[^_\\]){1,2000})(?<!\s)__(?![\p{L}\p{N}_])`,          // 15: bold
  String.raw`(?<![\p{L}\p{N}_])_(?![\s_])((?:\\.|[^_\\\n]){1,2000})(?<!\s)_(?![\p{L}\p{N}_])`,       // 16: italic
  String.raw`(?<![\p{L}\p{N}_/.:@=])(https?:\/\/[^\s<>]{1,2000})`,              // 17: an address written out, as GitLab links it
].join('|'), 'giu');

function link(href, ...label) {
  const to = resolve(href);
  return el('a', {href: to.href, target: to.external ? '_blank' : null, rel: to.external ? 'noopener' : null}, ...label);
}

function inline(text) {
  const out = [];
  let last = 0;
  for (const m of text.matchAll(INLINE)) {
    out.push(text.slice(last, m.index));
    last = m.index + m[0].length;
    if (m[1] !== undefined) out.push(m[1]);
    // One space at each edge belongs to the marks, when the code is not spaces alone: «`` `a` ``» is «`a`».
    else if (m[3] !== undefined) out.push(el('code', {}, m[3].length > 2 && m[3].startsWith(' ') && m[3].endsWith(' ') && m[3].trim() ? m[3].slice(1, -1) : m[3]));
    else if (m[4] !== undefined) out.push(el('br'));
    else if (m[5] !== undefined) out.push(el('a', {href: m[5], target: '_blank', rel: 'noopener'}, m[5]));
    else if (m[7] !== undefined || m[8] !== undefined) out.push(el('em', {class: 'picture'}, `[картинка: ${m[6] || m[7] || m[8]}]`));
    else if (m[10] !== undefined || m[11] !== undefined) out.push(link(m[10] !== undefined ? m[10] : m[11], ...inline(m[9])));
    else if (m[12] !== undefined) out.push(el('strong', {}, el('em', {}, ...inline(m[12]))));
    else if (m[13] !== undefined) out.push(el('strong', {}, ...inline(m[13])));
    else if (m[14] !== undefined) out.push(el('em', {}, ...inline(m[14])));
    else if (m[15] !== undefined) out.push(el('strong', {}, ...inline(m[15])));
    else if (m[16] !== undefined) out.push(el('em', {}, ...inline(m[16])));
    else {
      // The full stop or the bracket a sentence ends with is the sentence's, not the address's.
      const address = m[17].replace(/[.,;:!?)\]»"']+$/, '');
      out.push(el('a', {href: address, target: '_blank', rel: 'noopener'}, address), m[17].slice(address.length));
    }
  }
  out.push(text.slice(last));
  return out.filter((part) => part !== '');
}

// The id of a heading, as GitLab makes it and as the README links to it (content/render.py: anchor): lower case,
// letters, digits, «_» and «-» kept, spaces to hyphens, a repeated title numbered.
function slug(text, seen) {
  let s = text.toLowerCase().replace(/[^\p{L}\p{N}_\- ]/gu, '').replace(/ /g, '-').replace(/-{2,}/g, '-').replace(/^-+|-+$/g, '');
  if (seen[s] !== undefined) { seen[s] += 1; s = `${s}-${seen[s]}`; } else seen[s] = 0;
  return s;
}

// The cells of a row of a table. «\|» is a bar inside a cell — in a piece of code too — not the end of the cell;
// the bars at the edges are the frame.
function cells(line) {
  const row = line.trim().replace(/^\|/, '');
  const found = [];
  let cell = '';
  for (let i = 0; i < row.length; i += 1) {
    if (row[i] === '\\' && row[i + 1] === '|') { cell += '|'; i += 1; } else if (row[i] === '|') { found.push(cell.trim()); cell = ''; } else cell += row[i];
  }
  if (cell.trim() || !row.endsWith('|')) found.push(cell.trim());
  return found;
}

// The line under the head of a table: «|---|:--:|--:|». Its cells say how the columns are aligned; null — not such a line.
function alignment(line) {
  if (!line || line.length > 2000 || !line.includes('-') || !/^[\s|:-]+$/.test(line)) return null;
  const marks = cells(line);
  if (!marks.length || !marks.every((mark) => /^:?-+:?$/.test(mark))) return null;
  return marks.map((mark) => (mark.startsWith(':') && mark.endsWith(':') ? 'center' : mark.endsWith(':') ? 'right' : mark.startsWith(':') ? 'left' : null));
}

// The title of a heading without the hashes that may close it: «## Title ##».
function headed(text) {
  const title = text.trim();
  let end = title.length;
  while (end && title[end - 1] === '#') end -= 1;
  return end < title.length && (end === 0 || /\s/.test(title[end - 1])) ? title.slice(0, end).trim() : title;
}

// A line that begins a block of its own — a heading, a quotation, code, a rule — and so ends a table or a paragraph.
const begins = (line) => /^ {0,3}(#{1,6}[ \t]|>|`{3,}|~{3,})/.test(line) || /^ {0,3}([-*_])( *\1){2,} *$/.test(line);

function table(head, aligned, rows) {
  const row = (tag, values) => el('tr', {}, head.map((_, i) => el(tag, {class: aligned[i] ? `al-${aligned[i]}` : null}, ...inline(values[i] || ''))));
  return el('div', {class: 'table-wrap'}, el('table', {}, el('thead', {}, row('th', head)), el('tbody', {}, rows.map((values) => row('td', values)))));
}

// A quotation inside a quotation is read as a document of its own; deeper than this the marks stay text — a line of
// «>» alone must not end the page.
const DEPTH = 12;

function render(markdown, state = {toc: [], seen: {}}, depth = 0) {
  const lines = markdown.replace(/\r\n?/g, '\n').split('\n');
  const root = [];
  const {toc, seen} = state;
  let i = 0;
  let lists = [];   // the lists open at this line, outer first: {node, ordered, indent}
  let paragraph = [];
  const flush = () => {
    if (paragraph.length) root.push(el('p', {}, ...inline(paragraph.join(' '))));
    paragraph = [];
  };
  // The list an item of this indent and kind goes into: the one open at its level, a new one inside the last item
  // of a shallower list, or a new one in the document. A numbered list begins with the number written — a list cut
  // by a block of code goes on from where it stopped, as in the repository.
  const listFor = (indent, ordered, number) => {
    while (lists.length > 1 && indent < lists[lists.length - 1].indent) lists.pop();
    const top = lists[lists.length - 1];
    if (top && top.ordered === ordered && indent < top.indent + 2) return top.node;
    const node = el(ordered ? 'ol' : 'ul', {start: ordered && number !== 1 ? number : null});
    if (top && indent >= top.indent + 2 && top.node.lastElementChild) top.node.lastElementChild.append(node);
    else {
      if (top) lists.pop();
      const outer = lists[lists.length - 1];
      if (outer && outer.node.lastElementChild) outer.node.lastElementChild.append(node); else root.push(node);
    }
    lists.push({node, ordered, indent});
    return node;
  };
  while (i < lines.length) {
    const line = lines[i];
    // A block of code. Indented under an item of a list it is a part of the item — the generator writes the code
    // of a step so — and the list goes on after it; anywhere else it stands by itself and ends a list.
    const fence = line.match(/^( {0,12})(`{3,}|~{3,})/);
    if (fence && (fence[1].length <= 3 || lists.length)) {
      const inside = lists.length && fence[1].length >= 2 ? lists[lists.length - 1].node.lastElementChild : null;
      flush();
      if (!inside) lists = [];
      const body = [];
      i += 1;
      // A block is closed by as many of the same marks or more, alone on a line; an unclosed one runs to the end.
      const closes = (text) => { const end = text.match(/^ {0,12}(`{3,}|~{3,})\s*$/); return end && end[1][0] === fence[2][0] && end[1].length >= fence[2].length; };
      const indent = new RegExp(`^ {0,${fence[1].length}}`);   // the indent of the block is not a part of the code
      while (i < lines.length && !closes(lines[i])) { body.push(lines[i].replace(indent, '')); i += 1; }
      const block = el('pre', {}, body.join('\n'));
      if (inside) inside.append(block); else root.push(block);
      i += 1;
      continue;
    }
    const heading = line.match(/^(#{1,6})[ \t]+(.*)$/);
    if (heading) {
      flush(); lists = [];
      const level = heading[1].length;
      const label = headed(heading[2]);
      const id = slug(label, seen);
      root.push(el(`h${level}`, {id}, ...inline(label)));
      if (level === 2 || level === 3) toc.push([level, label, id]);
      i += 1;
      continue;
    }
    if (depth < DEPTH && /^ {0,3}>/.test(line)) {  // a quotation: its lines without the mark are a document of their own
      flush(); lists = [];
      const quoted = [];
      while (i < lines.length && /^ {0,3}>/.test(lines[i])) { quoted.push(lines[i].replace(/^ {0,3}> ?/, '')); i += 1; }
      root.push(el('blockquote', {}, ...render(quoted.join('\n'), state, depth + 1).root));
      continue;
    }
    // A table: a row of cells and the line of dashes under it, with as many cells. Its rows go on to an empty line
    // or to a line that begins another block; a line without a bar among them is a row of one cell.
    const aligned = line.includes('|') ? alignment(lines[i + 1]) : null;
    if (aligned && cells(line).length === aligned.length) {
      flush(); lists = [];
      const head = cells(line);
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].trim() && !begins(lines[i])) { rows.push(cells(lines[i])); i += 1; }
      root.push(table(head, aligned, rows));
      continue;
    }
    if (/^ {0,3}([-*_])( *\1){2,} *$/.test(line)) { flush(); lists = []; root.push(el('hr')); i += 1; continue; }
    const item = line.match(/^( {0,40})([-*+•]|\d{1,9}[.)])[ \t]+(.*)$/);
    if (item) {
      flush();
      const ordered = /\d/.test(item[2]);
      const node = listFor(item[1].length, ordered, ordered ? parseInt(item[2], 10) : 1);
      // «- [ ] дело» is a box to tick: shown as one, not ticked from here.
      const box = item[3].match(/^\[([ xXхХ])\][ \t]+(.*)$/);
      node.append(box ? el('li', {class: 'todo'}, el('input', {type: 'checkbox', disabled: '', checked: box[1] === ' ' ? null : ''}), ' ', ...inline(box[2]))
        : el('li', {}, ...inline(item[3])));
      i += 1;
      continue;
    }
    if (/^\s{2,}\S/.test(line) && lists.length) {  // continuation of a list item
      lists[lists.length - 1].node.lastElementChild.append(el('br'), ...inline(line.trim()));
      i += 1;
      continue;
    }
    if (!line.trim()) { flush(); lists.pop(); i += 1; continue; }
    lists = [];
    paragraph.push(line.trim());
    i += 1;
  }
  flush();
  return {root, toc};
}

// A file of the project that is not Markdown is shown as it is: data and code are read to the letter.
const isMarkdown = () => !filePath || /\.(md|markdown)$/i.test(filePath);

async function load() {
  document.title = `${title} · Подготовка проекта`;
  document.getElementById('docTitle').textContent = title;
  document.getElementById('raw').href = source;
  const back = document.getElementById('back');
  back.hidden = !filePath;
  back.href = `document?run=${encodeURIComponent(run)}&doc=preview&v=7`;   // «v»: see view() in content.js
  const doc = document.getElementById('doc');
  const toc = document.getElementById('toc');
  try {
    const response = await fetch(source);
    const text = await response.text();
    if (!response.ok) {
      let detail = text;
      try { detail = JSON.parse(text).detail || text; } catch (error) { /* not JSON: the text is the answer */ }
      throw new Error(detail);
    }
    if (path === 'check-list-preview' || !isMarkdown()) {
      doc.replaceChildren(el('pre', {}, text));
      toc.replaceChildren();
      return;
    }
    const result = render(text);
    doc.replaceChildren(...result.root);
    toc.replaceChildren(...result.toc.map(([level, label, id]) => el('a', {href: `#${id}`, class: `l${level}`}, label.replace(/\*\*/g, ''))));
    // The page is filled after it opened: the place the address names is found only now.
    let anchor = location.hash.slice(1);
    try { anchor = decodeURIComponent(anchor); } catch (error) { /* «#100%» is not an encoded name: taken as written */ }
    const place = anchor ? document.getElementById(anchor) : null;
    if (place) place.scrollIntoView();
  } catch (error) {
    doc.replaceChildren(el('p', {class: 'hint'}, `Не удалось загрузить документ. ${String(error.message || error).slice(0, 300)}`));
  }
}

function init() {
  document.getElementById('reload').addEventListener('click', load);
  load();
}

init();
