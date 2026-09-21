/** 模态框键盘焦点循环的小型纯函数模块，同时兼容浏览器和 Node 测试。 */
(function exposeModalFocus(root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ConsoleModalFocus = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function createModalFocus() {
  'use strict';

  function tabTarget(focusables, activeElement, shiftKey) {
    if (!focusables.length) return null;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const index = focusables.indexOf(activeElement);
    if (index === -1) return shiftKey ? last : first;
    if (shiftKey) return focusables[(index - 1 + focusables.length) % focusables.length];
    return focusables[(index + 1) % focusables.length];
  }

  return Object.freeze({ tabTarget });
}));
