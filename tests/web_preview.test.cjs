// Pure presentation-logic regression tests; no browser or paid network access.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/delta_loot_assistant/web_assets/app.js'), 'utf8');
const context = vm.createContext({});
vm.runInContext(source.slice(0, source.indexOf('function render()')), context);
const evaluate = expression => JSON.parse(vm.runInContext(`JSON.stringify(${expression})`, context));

test('unconfirmed candidates are priced and sorted before confirmation', () => {
  assert.deepEqual(evaluate('previewRows([{key:"b",total:90,per_cell:90,eligible:true},{key:"a",total:10,per_cell:10,eligible:false}]).map(r=>r.key)'), ['a','b']);
  assert.equal(evaluate('previewSummary([{total:90,eligible:true},{total:10,eligible:false}])').total, 100);
});
test('unknown values sort last, explicit zero remains a real price', () => {
  assert.deepEqual(evaluate('previewRows([{key:"u",total:null},{key:"p",total:20},{key:"z",total:0}],"total").map(r=>r.key)'), ['z','p','u']);
  assert.deepEqual(evaluate('previewSummary([{total:null},{total:0}])'), {total:0,priced:1,unpriced:1});
  assert.equal(evaluate('previewSummary([{total:null}])').total, null);
});
test('excluded rows cannot inflate totals or appear as cheapest', () => {
  assert.deepEqual(evaluate('previewSummary([{total:100},{total:9000,excluded:true}])'), {total:100,priced:1,unpriced:0});
  assert.deepEqual(evaluate('previewRows([{key:"a",total:10},{key:"x",total:1,excluded:true}],"total").map(r=>r.key)'), ['a']);
});
test('per-cell and total sorting are distinct', () => {
  const rows = '[{key:"a",total:100,per_cell:10},{key:"b",total:50,per_cell:50}]';
  assert.deepEqual(evaluate(`previewRows(${rows},"per_cell").map(r=>r.key)`), ['a','b']);
  assert.deepEqual(evaluate(`previewRows(${rows}).map(r=>r.key)`), ['b','a']);
  assert.deepEqual(evaluate(`previewRows(${rows},"total").map(r=>r.key)`), ['b','a']);
});
test('incomplete weapons explicitly label body-only reference price', () => {
  assert.match(evaluate('priceCaveat({definition:{needs_total:true,category:"weapon"}})'), /不含完整配件/);
  assert.equal(evaluate('priceCaveat({manual_total:0,definition:{needs_total:true,category:"weapon"}})'), '');
  assert.match(evaluate('priceCaveat({definition:{needs_total:true,category:"armor"}})'), /耐久/);
});
test('saving a draft does not require the confirmation checkbox', () => {
  const html = fs.readFileSync(path.join(__dirname, '../src/delta_loot_assistant/web_assets/index.html'), 'utf8');
  assert.doesNotMatch(html.match(/<input id="confirmed"[^>]*>/)[0], /required/);
  assert.ok(html.indexOf('class="panel results"') < html.indexOf('class="workspace"'));
});
test('all scope includes ground and every carried location', () => {
  vm.runInContext('state.session = {scope:"all"}', context);
  assert.deepEqual(evaluate('["loot","backpack","carried","safe_box","unassigned"].map(scope=>inScope({scope}))'), [true,true,true,true,true]);
  vm.runInContext('state.session = {scope:"carried"}', context);
  assert.equal(evaluate('inScope({scope:"loot"})'), false);
});

test('correction and model settings are opt-in dialogs, not permanent panels', () => {
  const html = fs.readFileSync(path.join(__dirname, '../src/delta_loot_assistant/web_assets/index.html'), 'utf8');
  assert.match(html, /<dialog id="item-dialog"/);
  assert.match(html, /<dialog id="llm-dialog"/);
  assert.doesNotMatch(html, /待核对/);
  assert.doesNotMatch(html.match(/<input id="agent-llm"[^>]*>/)[0], /checked/);
  assert.doesNotMatch(html.match(/<input id="allow-cloud"[^>]*>/)[0], /checked/);
  assert.match(html, /id="llm-key" type="password"/);
});
