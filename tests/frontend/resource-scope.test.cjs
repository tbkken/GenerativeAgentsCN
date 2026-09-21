const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const scopeSource = fs.readFileSync('src/generative_agents/adapters/web/static/resources/resource-scope.js', 'utf8');
const consoleSource = fs.readFileSync('src/generative_agents/adapters/web/static/shell/console-api.js', 'utf8');

function setup(pathname) {
  const navigations = [], events = [];
  const location = {pathname, assign: url => navigations.push(url)};
  const context = {location, URLSearchParams, encodeURIComponent, decodeURIComponent,
    stopExperimentListRefresh() {},
    document: {body: {classList: {toggle() {}}}},
    window: {location, dispatchEvent: event => events.push(event)},
    CustomEvent: function(type, options) { this.type = type; this.detail = options.detail; },
  };
  vm.createContext(context);
  vm.runInContext(scopeSource, context);
  vm.runInContext(consoleSource.slice(consoleSource.indexOf('  function navigateToExperiment('), consoleSource.indexOf('  async function openExperiment(')), context);
  return {context, scope: context.window.ResourceScope, location, navigations, events};
}

test('experiment editors stay bound to their own package for every request', () => {
  const {scope, location} = setup('/experiments/experiment-a');
  location.pathname = '/';
  assert.equal(scope.url('/maps/experiment-a'), '/api/studio/experiments/experiment-a/resources/maps/experiment-a');
  assert.equal(scope.pageUrl('crowds', {crowd_id:'local-team'}), '/experiments/experiment-a?view=crowds&crowd_id=local-team');
  assert.equal(scope.assetUrl('assets/maps/background.png'), '/api/studio/experiments/experiment-a/assets/maps/background.png');
});

test('optimistic saves retain the editor token instead of overwriting it with a newer global token', () => {
  const {scope} = setup('/experiments/experiment-a');
  scope.digest = 'new-server-token';
  const options = scope.options({method:'PUT', body:JSON.stringify({row_version:'old-editor-token', world:{}})});
  assert.equal(JSON.parse(options.body).row_version, 'old-editor-token');
  assert.equal(JSON.parse(scope.options({method:'POST', body:'{}'}).body).expected_content_sha256, 'new-server-token');
});

test('author workspace requests never acquire experiment endpoints or tokens', () => {
  const {scope} = setup('/');
  const options = {method:'PUT', body:'{"row_version":3}'};
  assert.equal(scope.url('/agents/a'), '/api/studio/resources/agents/a');
  assert.equal(scope.options(options), options);
  assert.equal(scope.pageUrl('public-agents'), '/?view=public-agents');
});

test('opening an experiment navigates to a new document and preserves a selected Run', () => {
  const {context, navigations} = setup('/');
  assert.equal(context.navigateToExperiment('experiment-a', 'results', 'run-a'), true);
  assert.deepEqual(navigations, ['/experiments/experiment-a?view=results&run_id=run-a']);
});

test('switching between experiments cannot reuse the previous editor scope', () => {
  const {context, navigations} = setup('/experiments/experiment-a');
  assert.equal(context.navigateToExperiment('experiment-a', 'maps'), false);
  assert.equal(context.navigateToExperiment('experiment-b', 'brains'), true);
  assert.deepEqual(navigations, ['/experiments/experiment-b?view=brains']);
});

test('map recovery accepts a matching package hash and preserves conflicting recovery data', async () => {
  const source = fs.readFileSync('src/generative_agents/adapters/web/static/resources/map-workspace.js', 'utf8');
  const method = source.slice(source.indexOf('    async restoreLocalRecovery('), source.indexOf('    handleBeforeUnload('));
  let restored = null, cleared = false;
  const recovery = {schema:'ga-map-draft-recovery/v1', baseLockVersion:'abc123', world:{name:'unsaved'}};
  const context = {MAP_RECOVERY_SCHEMA: recovery.schema, readRecovery: async () => recovery,
    same: (a,b) => JSON.stringify(a) === JSON.stringify(b), notify() {},
  };
  vm.createContext(context);
  vm.runInContext(`this.manager = {${method}}`, context);
  Object.assign(context.manager, {recoveryKey: () => 'experiment:a', clearLocalRecovery: () => {cleared=true;},
    publicEditor: {setWorld: world => {restored=world;}}, setAutoSaveStatus() {},
  });
  assert.equal(await context.manager.restoreLocalRecovery('a',{row_version:'abc123',world:{name:'saved'}}), true);
  assert.equal(restored, recovery.world);
  restored = null;
  assert.equal(await context.manager.restoreLocalRecovery('a',{row_version:'def456',world:{name:'saved'}}), false);
  assert.equal(restored, null);
  assert.equal(cleared, false);
});
