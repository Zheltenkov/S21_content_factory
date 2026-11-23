// The chat page's scripts loaded into a sandbox with a minimal DOM: enough for h() and icon(), no browser.
// Functions are called as the page calls them; assertions read the rendered text, as the methodologist sees it.
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const STATIC = new URL('../../static/', import.meta.url);

class Node {}
class Text extends Node {
  constructor(data) { super(); this.data = data; }
  get textContent() { return this.data; }
}
class Element extends Node {
  constructor(tag) { super(); this.tagName = tag.toUpperCase(); this.children = []; this.attributes = {}; this.className = ''; this.listeners = {}; }
  set textContent(value) { this.children = [new Text(String(value))]; }
  get textContent() { return this.children.map((c) => c.textContent).join(''); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return key in this.attributes ? this.attributes[key] : null; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  append(...nodes) { for (const n of nodes) this.children.push(typeof n === 'string' ? new Text(n) : n); }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  get classList() { const el = this; return {contains: (c) => (el.className || el.attributes.class || '').split(/\s+/).includes(c), toggle() {}, add() {}, remove() {}}; }
  get lastElementChild() { return this.children.filter((c) => c instanceof Element).at(-1) || null; }
  // Every element below this one (depth first), for simple lookups by tag or class.
  *all() { for (const c of this.children) if (c instanceof Element) { yield c; yield* c.all(); } }
  find(test) { return [...this.all()].filter(test); }
  byClass(name) { return this.find((el) => el.classList.contains(name)); }
  byTag(tag) { return this.find((el) => el.tagName === tag.toUpperCase()); }
}

// dom: the page's own elements exist — each selector the scripts ask for gets one element, kept for the whole test,
// so a function that fills the page (a list of projects, a card) can be called and its result read back.
export function page({dom = false} = {}) {
  const elements = new Map();
  const element = (selector) => { if (!elements.has(selector)) elements.set(selector, new Element('div')); return elements.get(selector); };
  const document = {
    createElement: (tag) => new Element(tag), createElementNS: (ns, tag) => new Element(tag),
    querySelector: dom ? element : () => null, querySelectorAll: () => [], getElementById: () => null, addEventListener() {},
  };
  const storage = new Map();
  const context = vm.createContext({
    document, Node, console, URLSearchParams, FormData, location: {search: ''}, setTimeout, clearTimeout,
    localStorage: {getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, String(v)), removeItem: (k) => storage.delete(k)},
    fetch: async () => { throw new Error('no network in tests'); },
  });
  // Loaded in the page's order; chat.js ends by starting the page, which needs the real DOM: not here.
  for (const name of ['content.js', 'chat.js']) {
    const source = readFileSync(new URL(name, STATIC), 'utf8');
    vm.runInContext(name === 'chat.js' ? source.replace(/\ninit\(\);\s*$/, '\n') : source, context, {filename: name});
  }
  // Top-level const and function declarations of the scripts, as the page sees them.
  return (expression) => vm.runInContext(expression, context);
}

// The page of a document (document.js) in the same sandbox: its Markdown is rendered into elements of the minimal DOM
// and read back by tag. `search` — the address the page was opened with: which run, which document or file.
export function documentPage(search = '?run=r1&doc=preview', {hash = '', answer = null} = {}) {
  // The parts of the page, kept for the whole test; an id a heading got is looked for in what was put into «doc».
  const parts = new Map(['docTitle', 'raw', 'back', 'doc', 'toc', 'reload'].map((id) => [id, new Element('div')]));
  const scrolled = [];
  const found = (id) => {
    const place = parts.get('doc').find((el) => el.getAttribute('id') === id)[0] || null;
    if (place) place.scrollIntoView = () => scrolled.push(id);
    return place;
  };
  const document = {createElement: (tag) => new Element(tag), getElementById: (id) => parts.get(id) || found(id), title: ''};
  // `answer(address)` — what the server would say: {ok, text}. Without it the page has no server, as in a test of render().
  const fetch = async (address) => {
    if (!answer) throw new Error('no network in tests');
    const said = answer(address);
    return {ok: said.ok !== false, text: async () => said.text};
  };
  const context = vm.createContext({document, console, URLSearchParams, location: {search, hash}, encodeURIComponent, decodeURIComponent, fetch});
  // The script ends by loading the document, which needs the server: not here.
  vm.runInContext(readFileSync(new URL('document.js', STATIC), 'utf8').replace(/\ninit\(\);\s*$/, '\n'), context, {filename: 'document.js'});
  const js = (expression) => vm.runInContext(expression, context);
  // The document as one element, to look through by tag; what the table of contents got, as data.
  js.render = (markdown) => {
    context.__markdown = markdown;
    const result = js('render(__markdown)');
    const root = new Element('main');
    root.append(...result.root);
    return {root, toc: JSON.parse(JSON.stringify(result.toc))};
  };
  // The page as it stands after load(): its parts by id, the title of the tab, the places it scrolled to.
  js.part = (id) => parts.get(id);
  js.scrolled = scrolled;
  js.document = document;
  return js;
}
