// Expert cards must remain unscored even if an older response contains numeric marks.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

class Element {
  constructor() { this.children = []; this.attributes = {}; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener() {}
  append(...values) { this.children.push(...values); }
  replaceChildren(...values) { this.children = values; }
  set textContent(value) { this.children = [String(value)]; }
  get textContent() { return this.children.map((v) => typeof v === 'string' ? v : v.textContent).join(''); }
}
function evaluator() {
  const nodes = new Map();
  const context = vm.createContext({document:{createElement:() => new Element(),querySelector:(id) => { if (!nodes.has(id)) nodes.set(id,new Element()); return nodes.get(id); }},
    location:{search:''},window:{addEventListener() {}},URLSearchParams,encodeURIComponent,console});
  vm.runInContext(readFileSync(new URL('../../static/evaluation.js',import.meta.url),'utf8'),context);
  return (expression) => vm.runInContext(expression,context);
}

test('expert badge ignores old numeric grades and does not present pass or failure',() => {
  const js = evaluator();
  for (const mark of [-1,0,1,null]) {
    const node = js(`badge({criterion:41,state:'assessed',mark:${mark},confirmed_blocker:true})`);
    assert.equal(node.textContent,'Экспертное наблюдение');
    assert.equal(node.attributes.class,'badge');
  }
  assert.match(js("badge({criterion:3,state:'assessed',mark:-1})").textContent,/-1/);
});

test('expert rubric uses qualitative guidance without numeric scale or critical badge',() => {
  const js = evaluator();
  js("data={verdicts:[{criterion:41,state:'assessed',mark:-1,confirmed_blocker:true,evidence:[],reason:'Уточнить разнообразие данных'}],snapshot:{units:[]}}");
  const card = js("criterionCard({number:41,title:'Данные и примеры',section:'Качество',fulfilled:'Ориентир',partial:'Уточнение',failed:'Проблема',requires_confirmation:''})");
  assert.match(card.textContent,/Уточнить разнообразие данных/);
  assert.match(card.textContent,/Ориентиры для обсуждения/);
  assert.doesNotMatch(card.textContent,/Критическое замечание|\+1:|−1:|0:/);
});

test('one finding presents all affected criteria as links without repeating a penalty',() => {
  const js=evaluator();
  const card=js("findingCard({title:'Одна отсутствующая зависимость',level:'critical',reason:'Одна причина',criteria:[9,12,16,31,32,34],suggestion:'Исправить ссылку',evidence:[]})");
  assert.equal((card.textContent.match(/Критический дефект/g)||[]).length,1);
  assert.match(card.textContent,/Связанные критерии/);
  assert.doesNotMatch(card.textContent,/\d+%|штраф:|баллов/);
});

test('unconfirmed remarks remain visible for review and cannot look like accepted defects',() => {
  const js=evaluator();
  js("renderFindings({findings:[],incomplete:true,review_queue:[{criterion:28,reason:'Цитату надо проверить'}]})");
  assert.match(js("$('#findings').textContent"),/не подтверждение готовности/);
  assert.match(js("$('#reviewQueue').textContent"),/Цитата не подтверждена/);
});
