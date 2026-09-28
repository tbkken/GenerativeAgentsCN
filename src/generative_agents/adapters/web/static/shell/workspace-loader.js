/* Load optional editors only when their workspace is opened. */
(() => {
  'use strict';
  const pending = new Map();
  const root = '/static/console/';
  const groups = {
    maps: ['resources/spatial-asset-workspace.js', 'resources/map-workspace.js'],
    'map-editor': ['resources/render-materials.js', 'resources/map-navigation.js', 'resources/map-editor-v2.js'],
    crowds: ['resources/crowd-workspace.js'],
    skills: ['resources/skill-workspace.js'],
    models: ['resources/model-workspace.js'],
    spatial: ['resources/spatial-picker.js'],
    replay: ['resources/render-materials.js', 'vendor/phaser.min.js', 'replay/replay-player.js'],
  };
  const styles = {
    maps: ['resources/map-workspace.css'], crowds: ['resources/crowd-workspace.css'],
    skills: ['resources/skill-workspace.css'], models: ['resources/model-workspace.css'],
    spatial: ['resources/spatial-picker.css'],
  };
  function script(path) {
    if (pending.has(path)) return pending.get(path);
    const request = new Promise((resolve, reject) => {
      const node = document.createElement('script');
      node.src = root + path;
      node.onload = resolve;
      node.onerror = () => { node.remove(); pending.delete(path); reject(new Error('模块加载失败，请重试：' + path)); };
      document.head.appendChild(node);
    });
    pending.set(path, request);
    return request;
  }
  async function load(group) {
    for (const path of styles[group] || []) {
      if (!document.querySelector(`link[data-workspace-style="${path}"]`)) {
        const link = document.createElement('link');
        link.rel = 'stylesheet'; link.href = root + path; link.dataset.workspaceStyle = path;
        document.head.appendChild(link);
      }
    }
    for (const path of groups[group] || []) await script(path);
  }
  window.WorkspaceLoader = {load};
})();
