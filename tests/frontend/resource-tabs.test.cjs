const {test} = require('node:test');
const assert = require('node:assert/strict');
const tabs = require('../../src/generative_agents/adapters/web/static/resources/resource-tabs.js');

test('both workspaces use two menus while retaining distinct Agent and Crowd panels', () => {
  assert.equal(tabs.menuPage('crowds'), 'public-agents');
  assert.equal(tabs.menuPage('crowds', true), 'agents');
  assert.equal(tabs.menuPage('brains'), 'skills');
  assert.equal(tabs.menuPage('results', true), 'results');
  assert.deepEqual(tabs.tabs('crowds').map(item => item.label), ['智能体', '人群']);
  assert.deepEqual(tabs.tabs('brains', true).map(item => item.label), ['技能', '大脑']);
});

test('Tab navigation remembers sibling choice, updates accessibility and supports keyboard selection', () => {
  const memory = new Map(), panels = new Map(), navigated = [], handlers = {};
  let buttons = [], markup;
  const bar = {dataset:{}, setAttribute() {}, addEventListener(key, fn) {handlers[key] = fn;},
    querySelectorAll() {return buttons;}, get innerHTML() {return markup;}, set innerHTML(value) {
      markup = value;
      buttons = [...value.matchAll(/data-resource-page="([^"]+)"/g)].map(([,page]) => ({
        id:`resource-tab-${page}`, dataset:{resourcePage:page}, attributes:{},
        setAttribute(key, value) {this.attributes[key] = value;}, getAttribute(key) {return this.attributes[key];},
        closest() {return this;}, focus() {this.focused = true;},
      }));
    }};
  const win = {ResourceScope:{experimentId:'one'}, ResourceList:{
    read(key) {return memory.get(key) || {};}, remember(key, value) {memory.set(key, value);}},
    document:{getElementById(id) {
      if (id === 'resourceTabs') return bar;
      if (!panels.has(id)) panels.set(id, {attributes:{}, setAttribute(key, value) {this.attributes[key] = value;}});
      return panels.get(id);
    }}};
  const controller = tabs.mount(win, page => {navigated.push(page); controller.sync(page);});
  controller.sync('agents');
  assert.equal(controller.target('agents'), 'agents');
  handlers.click({target:buttons[1]});
  assert.equal(controller.target('agents'), 'crowds');
  assert.equal(buttons[1].attributes['aria-selected'], 'true');
  assert.equal(panels.get('page-crowds').attributes['aria-labelledby'], 'resource-tab-crowds');
  handlers.keydown({key:'ArrowLeft', target:buttons[1], preventDefault() {}});
  assert.deepEqual(navigated, ['crowds', 'agents']);
  assert.equal(buttons[0].focused, true);
  controller.sync('brains'); controller.sync('maps');
  assert.equal(bar.hidden, true);
  assert.equal(controller.target('skills'), 'brains');
  assert.equal(controller.target('agents'), 'agents');
});
