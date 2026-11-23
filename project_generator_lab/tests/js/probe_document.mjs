// What the page of a document makes of a text given on the standard input — as data a test in Python can compare
// with what the generator wrote (tests/content/test_document_page.py). Not a test by itself: `node --test` passes
// it over. With «slugs» for the address: the ids the page gives to the titles given as a JSON list.
import {readFileSync} from 'node:fs';
import {documentPage} from './page.mjs';

const given = readFileSync(0, 'utf8');
const js = documentPage(process.argv[2] === 'slugs' ? undefined : process.argv[2] || '?run=r1&doc=preview');
if (process.argv[2] === 'slugs') {
  js('const __seen = {}');
  console.log(JSON.stringify(JSON.parse(given).map((title) => { js.title = title; return js(`slug(${JSON.stringify(title)}, __seen)`); })));
} else {
  const {root, toc} = js.render(given);
  const texts = (tags) => root.find((el) => tags.includes(el.tagName)).map((el) => el.textContent);
  const links = root.byTag('a').map((a) => a.getAttribute('href') || '');
  const files = links.filter((href) => href.startsWith('document?')).map((href) => {
    const [address, anchor = ''] = href.split('?')[1].split('#');
    return [new URLSearchParams(address).get('path'), decodeURIComponent(anchor)];
  });
  console.log(JSON.stringify({
    tables: root.byTag('table').length,
    rows: root.byTag('tr').length,
    quotes: root.byTag('blockquote').length,
    headings: root.find((el) => /^H[1-6]$/.test(el.tagName)).length,
    ids: root.find((el) => /^H[1-6]$/.test(el.tagName)).map((el) => el.getAttribute('id')),
    toc: toc.length,
    // A line of a table left as a paragraph: the bars of its cells, or the row of dashes under its head.
    leftovers: texts(['P']).filter((text) => /\|\s*:?-{3,}|^\s*\|.*\|\s*$/m.test(text)),
    // Marks left in the text as typed: of emphasis, of code.
    marks: texts(['P', 'LI', 'TD', 'TH']).filter((text) => /\*\*|(?<![\p{L}\p{N}])__(?=\S)|``/u.test(text)),
    // Links that lead nowhere, and «files» that are no paths of a repository.
    dead: [...root.byTag('a').filter((a) => a.getAttribute('href') === '#').map((a) => a.textContent),
      ...files.filter(([path]) => /[<>]|^https?:|^\s|\s$/.test(path || '')).map(([path]) => path)],
    files,
    anchors: links.filter((href) => href.startsWith('#')).map((href) => decodeURIComponent(href.slice(1))),
    outside: links.filter((href) => /^https?:/.test(href)),
    // Numbered lists in the order of the page: the number each begins with and how many items it has.
    numbered: root.byTag('ol').map((ol) => [Number(ol.getAttribute('start') || 1), ol.children.filter((li) => li.tagName === 'LI').length]),
    code: root.byTag('code').map((el) => el.textContent),
  }));
}
