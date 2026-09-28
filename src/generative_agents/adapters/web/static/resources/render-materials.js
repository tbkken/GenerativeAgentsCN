/* Resolve image dependencies from rendered content, excluding unused library sources. */
(() => {
  'use strict';
  function sourceIds(document, extraSources = []) {
    const sources = new Set(extraSources.filter(Boolean).map(String));
    const slices = new Map((document.material_slices || []).map(item => [String(item.id), item]));
    const gids = new Map((document.material_slices || []).filter(item => item.indexed_gid).map(item => [Number(item.indexed_gid), item.id]));
    const canvases = new Map((document.material_canvases || []).map(item => [String(item.source_id), item]));
    const visited = new Set();
    function slice(id) {
      const item = slices.get(String(id));
      if (!item || visited.has(String(id))) return;
      visited.add(String(id)); sources.add(String(item.source_id));
      if (canvases.has(String(item.source_id))) scan(canvases.get(String(item.source_id)));
    }
    function scan(value, key = '') {
      if (key === 'slice_id' || key === 'material_slice_id') { slice(value); return; }
      if (key === 'raw_gids' && Array.isArray(value)) { value.forEach(gid => slice(gids.get(Number(gid) & 0x1fffffff))); return; }
      if (!value || typeof value !== 'object') return;
      if (Array.isArray(value)) value.forEach(item => scan(item));
      else Object.entries(value).forEach(([name, item]) => scan(item, name));
    }
    scan(document.hierarchy_nodes);
    scan((document.visual_layers || []).filter(item => item.visible !== false));
    Object.values(document.tile_overrides || {}).forEach(slice);
    scan(document.tile_override_layers);
    for (const id of extraSources) if (canvases.has(String(id))) scan(canvases.get(String(id)));
    return sources;
  }
  window.RenderMaterials = {sourceIds};
})();
