const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {window: {}, clearTimeout};
vm.createContext(context);
for (const file of ['map-editor-v2.js', 'map-navigation.js']) {
  vm.runInContext(fs.readFileSync(`src/generative_agents/adapters/web/static/resources/${file}`, 'utf8'), context);
}
function setup() {
  const editor = Object.create(context.window.MapEditorV2.prototype);
  Object.assign(editor, {
    root: {focus() {}}, canvas: {setPointerCapture() {}, hasPointerCapture: () => false},
    workspace: 'world', tool: 'navigation', offsetX: 0, offsetY: 0,
    localPoint: e => ({x: e.clientX, y: e.clientY}), isCanvasEditing: () => false,
    updateCanvasCursor() {}, renderCanvas() {}, undoStack: [], redoStack: [], updateMapHistoryControls() {},
  });
  const navigation = Object.create(context.window.MapNavigationEditor.prototype);
  const values = {};
  Object.assign(navigation, {editor, active: true, tool: 'block', generation: 0,
    scope: () => ({key: 'world', width: 10}), values: () => values,
    point: p => [p.x, p.y], details() {}, schedule() {},
  });
  editor.navigation = navigation;
  return {editor, navigation, values};
}
const event = (x, y, ctrlKey = false) => ({button: 0, pointerId: 1, clientX: x, clientY: y, ctrlKey});
test('navigation plain click and drag pan without editing or history', () => {
  const {editor, values} = setup();
  editor.pointerDown(event(1, 1));
  editor.pointerMove(event(4, 5));
  editor.pointerUp(event(4, 5));
  assert.equal(editor.offsetX, 3);
  assert.equal(editor.offsetY, 4);
  assert.deepEqual(values, {});
  assert.equal(editor.undoStack.length, 0);
});
test('Ctrl left drag paints continuous cells and stops when Ctrl is released', () => {
  const {editor, navigation, values} = setup();
  editor.pointerDown(event(1, 1, true));
  editor.pointerMove(event(3, 1, true));
  editor.pointerMove(event(4, 1));
  assert.deepEqual(values, {11: true, 12: true, 13: true});
  assert.equal(navigation.stroke, null);
  editor.pointerUp(event(4, 1));
  assert.equal(editor.undoStack.length, 1);
  assert.equal(editor.offsetX, 0);
});
test('Ctrl click edits one cell', () => {
  const {editor, values} = setup();
  editor.pointerDown(event(2, 2, true));
  editor.pointerUp(event(2, 2, true));
  assert.deepEqual(values, {22: true});
  assert.equal(editor.undoStack.length, 1);
});
