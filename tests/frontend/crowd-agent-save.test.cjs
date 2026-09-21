const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function workspace() {
  const elements = new Map();
  const document = { getElementById(id) {
    if (!elements.has(id)) elements.set(id, { value: '', innerHTML: '', querySelectorAll: () => [] });
    return elements.get(id);
  } };
  const context = { window: {}, document, console };
  vm.runInNewContext(readFileSync(join(__dirname, '../../src/generative_agents/adapters/web/static/resources/crowd-workspace.js'), 'utf8'), context);
  const manager = context.window.CrowdWorkspace;
  manager.modal = () => {};
  manager.notify = () => {};
  manager.revision = { members: [{ agent_id: 'zhao-yue' }] };
  return { manager, elements };
}

function agent(currently, row_version = 1) {
  return { id: 'zhao-yue', row_version, name: '赵悦', agent_key: 'zhao-yue', definition: {
    name: '赵悦', agent_key: 'zhao-yue', currently, scratch: { age: 22 },
  } };
}

test('saved server definition appears immediately and survives reopening the manager', async () => {
  const { manager, elements } = workspace();
  let stored = agent('旧目标');
  manager.request = async (path, options = {}) => {
    if (options.method === 'PUT') {
      const body = JSON.parse(options.body);
      assert.equal(body.row_version, stored.row_version);
      stored = { ...agent(body.definition.currently.trim(), stored.row_version + 1) };
      return stored;
    }
    return path === '/agents' ? { items: [stored] } : stored;
  };
  await manager.openAgentManager();
  assert.match(elements.get('crowdAgentList').innerHTML, /旧目标/);
  for (const goal of ['新目标一', '新目标二']) {
    const saved = await manager.saveSharedAgent({
      definition: { ...stored.definition, currently: ` ${goal} ` },
      agentDraft: { agent_id: stored.id, lock_version: stored.row_version },
    });
    // The first render after saving must already use the server response.
    const render = manager.renderAgentList.bind(manager);
    manager.renderAgentList = () => {
      render();
      assert.match(elements.get('crowdAgentList').innerHTML, new RegExp(goal));
      assert.doesNotMatch(elements.get('crowdAgentList').innerHTML, /旧目标/);
    };
    await manager.afterSharedAgentSaved(saved, saved.name);
    await manager.openAgentManager();
    manager.renderAgentList = render;
    elements.get('crowdAgentSearch').value = goal;
    manager.renderAgentList();
    assert.match(elements.get('crowdAgentList').innerHTML, /赵悦/);
    elements.get('crowdAgentSearch').value = '';
  }
});

for (const failedRead of [false, true]) {
  test(`pending ${failedRead ? 'failed' : 'stale'} read cannot replace a saved definition`, async () => {
    const { manager } = workspace();
    manager.agents = [agent('旧目标')];
    let finish;
    manager.request = () => new Promise((resolve, reject) => {
      finish = () => failedRead ? reject(new Error('read failed')) : resolve(agent('旧目标'));
    });
    const loading = manager.loadAgentRevisionDetails();
    const saved = agent('新目标', 2);
    manager.request = async () => saved;
    await manager.saveSharedAgent({ definition: saved.definition, agentDraft: { agent_id: saved.id, lock_version: 1 } });
    finish();
    await loading;
    assert.match(manager.agentCardMarkup(saved), /新目标/);
    assert.doesNotMatch(manager.agentCardMarkup(saved), /旧目标/);
  });
}
