const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/generative_agents/adapters/web/static/shell/console-api.js', 'utf8');
const saveCode = source.slice(source.indexOf('  async function saveDraftUnlocked('), source.indexOf('  function saveDraft(options'));

for (const provider of ['openai_compatible', 'openai']) {
  test(`saving before execution preserves copied ${provider} model configuration and host credential bindings`, async () => {
    const models = {
      chat: { provider, model: 'selected-chat', base_url: 'https://example.test/v1',
        credential_env: 'GA_SELECTED_CHAT_KEY', timeout_seconds: 90, max_tokens: 4096,
        temperature: 0.6, retry_attempts: 2, retry_backoff_seconds: 1,
        resolved_model: 'resolved-chat', context_window: 64000, enable_thinking: true },
      embedding: { provider: 'openai_compatible', model: 'selected-embedding',
        base_url: 'https://example.test/v1', credential_env: 'GA_SELECTED_EMBEDDING_KEY',
        timeout_seconds: 60, transport_retry_attempts: 2, index_operation_retry_attempts: 3,
        retry_backoff_seconds: 1, resolved_model: 'resolved-embedding' },
    };
    const crowds = [{crowd_key:'campus', name:'Campus crowd', agent_keys:['renamed-agent']}];
    const definition = { experiment: { name: 'Campus' }, simulation: {}, results: {}, agents: [{agent_key:'renamed-agent'}], crowds, models,
      world: {definition: {largeMap: 'x'.repeat(1_000_000)}} };
    const values = { timezone: 'Asia/Shanghai', startTime: '2026-09-23T08:00', stride: '10',
      maxSteps: '12', seed: '42', checkpointInterval: '1', checkpointRetention: '2', projectionInterval: '1' };
    const requests = [];
    const context = {
      state: { draft: { id: 'experiment', definition_hash: 'before', definition },
        selectedExperimentId: 'experiment', experiment: { name: 'Campus' } },
      // Unknown/hidden legacy fields are empty, as unsupported select values
      // were in the browser. They must never replace the selected model copy.
      $: id => ({ value: values[id] || '', dataset: {}, classList: { contains: () => false } }),
      structuredClone, simulationStartTime: value => `${value}:00+08:00`,
      document: { querySelectorAll: () => [] }, saveSecret: async () => null,
      api: async (url, options) => { const body = JSON.parse(options.body); requests.push({url, body, method:options.method}); return {definition:{...definition,...body.sections}}; },
      acceptSavedDraft: async () => {}, showToast: () => {},
    };
    vm.createContext(context); vm.runInContext(saveCode, context);
    await context.saveDraftUnlocked({silent:true});
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, '/experiments/experiment');
    assert.equal(requests[0].method, 'PATCH');
    assert.deepEqual(requests[0].body.sections.models, models);
    assert.deepEqual(requests[0].body.sections.crowds, crowds);
    assert.equal(requests[0].body.sections.simulation.max_steps, 12);
    assert.ok(!Object.hasOwn(requests[0].body.sections, 'world'));
    assert.ok(JSON.stringify(requests[0].body).length < 4000);
    assert.equal(requests[0].body.expected_content_sha256, 'before');
    assert.ok(!JSON.stringify(requests[0].body).includes('secret_ref'));
  });
}
