const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('generative_agents/web/static/console-api.js', 'utf8');
const renderer = source.slice(source.indexOf('  function artifactSourceMarkup('), source.indexOf('  function closeLogStream('));

function context() {
  const elements = new Map();
  const value = {
    state: {selectedRunId: 'current-run', currentRun: {status: 'COMPLETED', completed_steps: 32}},
    statusLabels: {PAUSED: '已暂停', COMPLETED: '已完成'},
    escapeHtml: text => String(text).replaceAll('<', '&lt;'),
    systemTimeMarkup: value => `<time>${value}</time>`,
    renderModelUsage() {},
    $: key => {
      if (!elements.has(key)) elements.set(key, {});
      return elements.get(key);
    },
  };
  vm.createContext(value);
  vm.runInContext(renderer, value);
  return value;
}

test('artifact list uses persisted source instead of the completed current Run', () => {
  const ctx = context();
  const partial = {logical_name: 'partial.zip', sha256: 'a'.repeat(64), artifact_id: 'id', size_bytes: 12, type: 'ZIP',
    provenance_status: 'RECORDED', source_step: 2, source_total_steps: 32, source_status: 'PAUSED', partial: true,
    generated_at: '2026-09-09T00:00:00Z'};
  ctx.renderOperations({artifacts: [partial], artifact_jobs: []});
  const html = ctx.$('artifactRows').innerHTML;
  assert.match(html, /Step 2 \/ 32 · 部分结果 · 取材时已暂停/);
  assert.match(html, /<time>2026-09-09T00:00:00Z<\/time>/);
  assert.doesNotMatch(html, /最终结果|取材时已完成/);
  ctx.state.currentRun.completed_steps = 99;
  ctx.renderOperations({artifacts: [partial], artifact_jobs: []});
  assert.equal(ctx.$('artifactRows').innerHTML, html);
});

test('unknown artifacts do not infer step, finality or creation time', () => {
  const ctx = context();
  for (const item of [{}, {provenance_status: 'UNKNOWN', source_step: 32, partial: false}]) {
    const text = ctx.artifactSourceMarkup(item);
    assert.match(text, /来源未知/);
    assert.doesNotMatch(text, /最终结果|Step 32|已完成/);
  }
  const final = ctx.artifactSourceMarkup({provenance_status: 'RECORDED', source_step: 32, source_total_steps: 32,
    source_status: 'COMPLETED', partial: false, generated_at: '2026-09-09T01:00:00Z'});
  assert.match(final, /Step 32 \/ 32 · 最终结果 · 取材时已完成/);
});
