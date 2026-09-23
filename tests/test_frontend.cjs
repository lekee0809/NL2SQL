const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function element() {
  const classes = new Set();
  return {
    classList: { add: x => classes.add(x), remove: x => classes.delete(x),
      toggle: (x, enabled) => enabled ? classes.add(x) : classes.delete(x) },
    children: [], textContent: '', innerHTML: '', value: '', disabled: false,
    parentElement: {classList: {toggle() {}}},
    appendChild(child) { this.children.push(child); },
    append(...children) { this.children.push(...children); },
    replaceChildren() { this.children = []; },
    remove() { this.removed = true; },
    querySelector() { return this.buttonLabel || (this.buttonLabel = element()); },
    addEventListener() {}, focus() {},
  };
}

const ids = new Map();
const get = id => {
  if (!ids.has(id) || ids.get(id).removed) ids.set(id, element());
  return ids.get(id);
};
const stored = new Map();
const storage = {
  getItem: key => stored.get(key) || null,
  setItem: (key, value) => stored.set(key, value),
  removeItem: key => stored.delete(key),
};
const calls = [];
const responses = [
  { session_id: 's1', turn_count: 1, count: 2, rows: [], sql: 'SELECT 1', changes: [], patch_source: 'initial_model', model_call: {total_tokens: 20} },
  { session_id: 's1', turn_count: 2, count: 1, rows: [], sql: 'SELECT 2', changes: ['改为华南'], patch_source: 'local', model_call: null },
  { detail: {type: 'needs_clarification', message: '请选择', session_id: 's1', turn_count: 3, candidates: [{value: '华东', entity_id: 37}]} },
  { session_id: 's1', turn_count: 4, count: 1, rows: [], sql: 'SELECT 3', changes: ['已确认候选值'], patch_source: 'clarification' },
];
const context = {
  document: {getElementById: get, createElement: element, querySelectorAll: () => []},
  localStorage: storage, sessionStorage: storage,
  navigator: {clipboard: {writeText: async () => {}}},
  setTimeout, console,
  fetch: async (url, options = {}) => {
    calls.push({url, options});
    if (url === '/health') return {ok: true, json: async () => ({api: 'ok', database: {ok: true}})};
    const data = responses.shift();
    return {ok: !data.detail, status: data.detail ? 409 : 200, json: async () => data};
  },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8'), context);

(async () => {
  await vm.runInContext('runQuery("2025年华东销售额")', context);
  await vm.runInContext('runQuery("换成华南")', context);
  await vm.runInContext('runQuery("查候选商品")', context);
  assert.equal(get('question').disabled, true);
  await vm.runInContext('runResolved("华东", "查候选商品", 37)', context);
  assert.deepEqual(calls.filter(x => x.url !== '/health').map(x => x.url), [
    '/sessions', '/sessions/s1/query', '/sessions/s1/query', '/sessions/s1/resolve',
  ]);
  assert.equal(get('question').disabled, false);
  assert.equal(JSON.parse(calls.find(x => x.url === '/sessions/s1/resolve').options.body).entity_id, 37);
  assert.equal(get('sessionStatus').textContent, '第 4 轮 · 可以继续追问');
  assert.equal(get('queryMeta').textContent, '第 4 轮 · 候选值确认 · 未调用模型');
  assert.equal(get('turns').children.length, 4);
  assert.equal(stored.get('nl2sqlSessionId'), 's1');
  context.fetch = async () => ({ok: false, status: 404, json: async () => ({detail: 'expired'})});
  await vm.runInContext('runQuery("继续查询")', context);
  assert.equal(stored.has('nl2sqlSessionId'), false);
  assert.match(get('errorText').textContent, /会话已过期/);
  const trend = {
    query_spec: {dimensions: ['order_month'], metrics: ['sales_amount']},
    rows: [{'月份': '2025-02-01', '销售额': 20}, {'月份': '2025-01-01', '销售额': 10}],
  };
  assert.equal(vm.runInContext('prepareChart', context)(trend).defaultMode, 'line');
  assert.equal(vm.runInContext('prepareChart', context)(trend).modes.includes('share'), false);
  vm.runInContext('renderChart', context)(trend);
  assert.match(get('chart').innerHTML, /viz-line/);
  const missingTrend = {query_spec: trend.query_spec, rows: [{'月份': '2025-01', '销售额': 10}, {'月份': '2025-02', '销售额': null}, {'月份': '2025-03', '销售额': 20}]};
  vm.runInContext('renderChart', context)(missingTrend);
  assert.match(get('chart').innerHTML, /缺失值已断开显示/);
  const ranking = {
    query_spec: {dimensions: ['product'], metrics: ['sales_amount', 'sold_quantity']},
    rows: [{'商品': '<script>alert(1)</script>', '销售额': -5, '销量': 2}, {'商品': '很长的商品名称', '销售额': 10, '销量': 3}],
  };
  const plan = vm.runInContext('prepareChart', context)(ranking);
  assert.equal(plan.defaultMode, 'bar');
  assert.equal(plan.metricKeys.length, 2);
  vm.runInContext('renderChart', context)(ranking);
  assert.match(get('chart').innerHTML, /negative/);
  assert.doesNotMatch(get('chart').innerHTML, /<script>/);
  get('chartMode').value = 'share';
  vm.runInContext('renderSelectedChart()', context);
  assert.match(get('chart').innerHTML, /包含负数/);
  const share = {query_spec: {dimensions: ['category'], metrics: ['sales_amount']}, rows: [{'类别': 'A', '销售额': 30}, {'类别': 'B', '销售额': 70}]};
  vm.runInContext('renderChart', context)(share);
  get('chartMode').value = 'share';
  vm.runInContext('renderSelectedChart()', context);
  assert.match(get('chart').innerHTML, /70.0%/);
  const single = {query_spec: {dimensions: [], metrics: ['sales_amount']}, rows: [{'销售额': 1234.5}]};
  assert.equal(vm.runInContext('prepareChart', context)(single).defaultMode, 'kpi');
  vm.runInContext('renderChart', context)(single);
  assert.match(get('chart').innerHTML, /1,234.5/);
  const comparison = {query_spec: {dimensions: [], metrics: ['sales_amount'], comparison: {type: 'year_over_year'}}, rows: [{'本期销售额': 100, '上年同期销售额': 0, '同比增长率': null}]};
  vm.runInContext('renderChart', context)(comparison);
  assert.match(get('chart').innerHTML, /同比增长率/);
  assert.match(get('chart').innerHTML, /—/);
  assert.equal(vm.runInContext('prepareChart', context)({rows: []}), null);
  console.log('frontend session flow passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
