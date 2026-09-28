const {test} = require('node:test');
const assert = require('node:assert/strict');
const picker = require('../../src/generative_agents/adapters/web/static/resources/spatial-picker.js');
const world = (tiles, extra = {}) => ({definition: {tiles, ...extra}});
const tile = (x, y, address, collision = false) => ({coord: [x, y], address, collision});
const get = (model, path) => model.byPath.get(JSON.stringify(path));

test('empty and missing maps produce explicit empty models without invented locations', () => {
  assert.equal(picker.buildModel(null).hasMap, false);
  assert.deepEqual(picker.buildModel(null).nodes, []);
  assert.equal(picker.buildModel(world([])).hasMap, true);
  assert.deepEqual(picker.buildModel(world([])).roots, []);
});

test('four-level tree keeps same-named objects in different places distinct with stable IDs', () => {
  const paths = [['世界', '甲区', '卧室', '床'], ['世界', '乙区', '卧室', '床']];
  const tiles = paths.map((path, index) => tile(index, 0, path));
  const model = picker.buildModel(world(tiles));
  assert.equal(model.roots.length, 1);
  assert.equal(model.roots[0].children.length, 2);
  assert.notEqual(get(model, paths[0]).id, get(model, paths[1]).id);
  assert.deepEqual(get(model, paths[0]).coord, [0, 0]);
  assert.deepEqual(get(model, paths[1].slice(0, 3)).objects, ['床']);
  const reverse = picker.buildModel(world([...tiles].reverse()));
  assert.equal(get(reverse, paths[0]).id, get(model, paths[0]).id);
});

test('spawn uses closest walkable coordinate to coverage center with deterministic y/x ties', () => {
  const path = ['世界', '区域', '房间'];
  const tiles = [];
  for (let y = 0; y < 3; y++) for (let x = 0; x < 3; x++) tiles.push(tile(x, y, path, x === 1 && y === 1));
  assert.deepEqual(get(picker.buildModel(world(tiles)), path).coord, [1, 0]);
  assert.deepEqual(get(picker.buildModel(world(tiles.reverse())), path).coord, [1, 0]);
});

test('spawn never moves from a blocked object to its room or a nearby different object', () => {
  const room = ['世界', '区域', '房间'], object = [...room, '桌子'];
  const model = picker.buildModel(world([tile(0, 0, object, true), tile(1, 0, room), tile(2, 0, [...room, '椅子'])]));
  assert.equal(get(model, object).coord, null);
  assert.deepEqual(get(model, room).coord, [1, 0]);
  assert.equal(get(picker.buildModel(world([tile(0, 0, object)])), room).coord, null);
});

test('semantic IDs, address hierarchy and node bounds take precedence over tile envelope', () => {
  const path = ['世界', '区域', '房间'];
  const nodes = path.map((name, index) => ({id: `node-${index}`, parent_id: index ? `node-${index - 1}` : null,
    name, kind: ['WORLD', 'SECTOR', 'ARENA'][index], address: path.slice(0, index + 1),
    bounds: {x: 0, y: 0, width: 9, height: 1}}));
  const model = picker.buildModel(world([tile(0, 0, path), tile(3, 0, path), tile(8, 0, path)], {
    semantic_index: {nodes}, editor_v2: {hierarchy_nodes: [{id: 'wrong', name: '不应出现', kind: 'WORLD'}]}}));
  assert.equal(get(model, path).id, 'node-2');
  assert.deepEqual(get(model, path).coord, [3, 0]);
  assert.equal(model.nodes.length, 3);
  nodes[2].bounds = {x: 0, y: 0, width: 2, height: 1};
  assert.deepEqual(get(picker.buildModel(world([tile(0, 0, path, true), tile(3, 0, path)], {semantic_index: {nodes}})), path).coord, null);
});

test('editor hierarchy includes empty nodes and rejects cycles and orphan paths', () => {
  const nodes = [
    {id: 'w', kind: 'WORLD', name: '世界'},
    {id: 's', kind: 'SECTOR', name: '区域', parent_id: 'w'},
    {id: 'a', kind: 'ARENA', name: '空房间', parent_id: 's'},
    {id: 'cycle', kind: 'ARENA', name: '循环', parent_id: 'cycle'},
    {id: 'orphan', kind: 'ARENA', name: '孤儿', parent_id: 'missing'},
  ];
  const model = picker.buildModel(world([], {editor_v2: {hierarchy_nodes: nodes}}));
  assert.equal(model.nodes.length, 3);
  assert.deepEqual(model.byId.get('a').path, ['世界', '区域', '空房间']);
  assert.equal(model.byId.get('a').coord, null);
});

test('malformed tiles cannot provide coordinates or generate an extra spatial level', () => {
  const model = picker.buildModel(world([
    tile(0, 0, ['世界', '区域', '房间', '对象', '第五层']), tile(-1, 0, ['世界']),
    tile(1.5, 0, ['世界']), tile(0, 0, ['世界', '']), {coord: [0, 0]}, null,
  ]));
  assert.equal(model.nodes.length, 0);
});

function fakeDocument() {
  class Element {
    constructor(tag) { this.tagName = tag; this.children = []; this.className = ''; this.dataset = {}; this.attributes = {}; this.value = ''; this.style = {setProperty() {}}; this.classList = {add: value => {this.className += ` ${value}`;}}; }
    append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
    replaceChildren(...children) { this.children = []; this.append(...children); }
    setAttribute(key, value) { this.attributes[key] = value; }
    remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
    focus() { document.activeElement = this; }
    contains(target) { return target === this || this.children.some(child => child.contains(target)); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    querySelectorAll(selector) {
      const results = [], matcher = selector.match(/^\[data-(node|toggle)-id="(\d+)"\]$/);
      const matches = node => matcher ? node.dataset[`${matcher[1]}Id`] === matcher[2] :
        selector.startsWith('.') ? node.className.split(' ').includes(selector.slice(1)) :
        selector === 'button:not([disabled]), input' ? (node.tagName === 'button' && !node.disabled) || node.tagName === 'input' : false;
      for (const child of this.children) { if (matches(child)) results.push(child); results.push(...child.querySelectorAll(selector)); }
      return results;
    }
  }
  const listeners = new Map();
  const document = {createElement: tag => new Element(tag), activeElement: null,
    addEventListener: (event, handler) => listeners.set(event, handler),
    removeEventListener: event => listeners.delete(event), listeners};
  document.body = new Element('body');
  return document;
}
function ui(options) {
  const document = fakeDocument(); global.document = document;
  const selections = [], handle = picker.open({...options, onSelect: value => selections.push(value)});
  const find = selector => document.body.querySelector(selector);
  return {document, selections, handle, find};
}

test('cancelling a spawn selection leaves caller state untouched, confirm emits copied path and coordinate', () => {
  const path = ['世界', '区域', '房间'], options = {world: world([tile(2, 3, path)]), selectedPath: path, mode: 'spawn'};
  let view = ui(options);
  assert.equal(view.find('.spatial-picker-confirm').disabled, false);
  view.find('.spatial-picker-cancel').onclick();
  assert.equal(view.selections.length, 0);
  assert.equal(view.document.body.children.length, 0);
  assert.equal(view.document.listeners.size, 0);
  view = ui(options); view.find('.spatial-picker-confirm').onclick();
  assert.deepEqual(view.selections, [{path, coord: [2, 3], objects: []}]);
  assert.notEqual(view.selections[0].path, path);
});

test('space selection adds only the explicitly chosen object; address mode supports the world root', () => {
  const room = ['世界', '区域', '房间'], object = [...room, '桌子'];
  const map = world([tile(0, 0, object, true)]);
  for (const [path, objects] of [[room, []], [object, ['桌子']]]) {
    const view = ui({world: map, selectedPath: path, mode: 'space'});
    assert.equal(view.find('.spatial-picker-confirm').disabled, false);
    view.find('.spatial-picker-confirm').onclick();
    assert.deepEqual(view.selections, [{path, coord: null, objects}]);
  }
  const view = ui({world: map, selectedPath: ['世界'], mode: 'address'});
  view.find('.spatial-picker-confirm').onclick();
  assert.deepEqual(view.selections, [{path: ['世界'], coord: null, objects: []}]);
});

test('blocked spawn and incomplete location cannot be confirmed; searching shows the matching full hierarchy', () => {
  const path = ['世界', '区域', '房间', '桌子'];
  const view = ui({world: world([tile(0, 0, path, true)]), selectedPath: path, mode: 'spawn'});
  assert.equal(view.find('.spatial-picker-confirm').disabled, true);
  view.find('.spatial-picker-confirm').onclick();
  assert.equal(view.selections.length, 0);
  const search = view.find('.spatial-picker-search'); search.value = '桌子'; search.oninput();
  assert.equal(view.document.body.querySelectorAll('.spatial-picker-choice').length, 4);
  search.value = '不存在'; search.oninput();
  assert.equal(view.find('.spatial-picker-empty').textContent, '未找到匹配的空间地址。');
  view.handle.close();
});
