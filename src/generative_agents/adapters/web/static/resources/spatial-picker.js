/* Shared package-owned spatial address picker for experiment authoring. */
(function (root) {
  'use strict';
  const KINDS = ['WORLD', 'SECTOR', 'ARENA', 'GAME_OBJECT'];
  const LABELS = ['世界', '区域', '场所', '对象'];
  const pathKey = path => JSON.stringify(path);
  const validPath = path => Array.isArray(path) && path.length > 0 && path.length <= 4 &&
    path.every(part => typeof part === 'string' && part.trim());
  const validCoord = coord => Array.isArray(coord) && coord.length === 2 &&
    coord.every(value => Number.isInteger(value) && value >= 0);
  function readBounds(value) {
    if (!value || !['x', 'y', 'width', 'height'].every(key => Number.isFinite(value[key])) ||
        value.width <= 0 || value.height <= 0) return null;
    return {x: value.x, y: value.y, width: value.width, height: value.height};
  }
  function inside(coord, bounds) {
    return !bounds || (coord[0] >= bounds.x && coord[0] < bounds.x + bounds.width &&
      coord[1] >= bounds.y && coord[1] < bounds.y + bounds.height);
  }

  function buildModel(world) {
    const definition = world?.definition;
    const model = {roots: [], nodes: [], byId: new Map(), byPath: new Map(), hasMap: !!definition};
    if (!definition) return model;
    const tiles = (Array.isArray(definition.tiles) ? definition.tiles : [])
      .filter(tile => tile && validPath(tile.address) && validCoord(tile.coord));
    const exactTiles = new Map(), extents = new Map();
    for (const tile of tiles) {
      const key = pathKey(tile.address);
      if (!exactTiles.has(key)) exactTiles.set(key, []);
      exactTiles.get(key).push(tile);
      for (let level = 1; level <= tile.address.length; level++) {
        const prefix = pathKey(tile.address.slice(0, level)), [x, y] = tile.coord;
        const extent = extents.get(prefix) || [x, y, x, y];
        extent[0] = Math.min(extent[0], x); extent[1] = Math.min(extent[1], y);
        extent[2] = Math.max(extent[2], x); extent[3] = Math.max(extent[3], y);
        extents.set(prefix, extent);
      }
    }
    const semantic = definition.semantic_index?.nodes;
    const hierarchy = definition.editor_v2?.hierarchy_nodes || definition.hierarchy_nodes;
    let sources;
    if (Array.isArray(semantic) && semantic.length) sources = semantic;
    else if (Array.isArray(hierarchy) && hierarchy.length) {
      const sourceById = new Map(hierarchy.filter(Boolean).map(node => [String(node.id), node]));
      const addressFor = source => {
        const names = [], seen = new Set();
        let current = source;
        while (current) {
          if (!current.id || seen.has(String(current.id)) || names.length >= 4) return null;
          seen.add(String(current.id)); names.unshift(current.name);
          if (!current.parent_id) break;
          current = sourceById.get(String(current.parent_id));
          if (!current) return null;
        }
        return names;
      };
      sources = hierarchy.filter(Boolean).map(source => ({...source, address: addressFor(source)}));
    } else {
      sources = [...extents.keys()].map(key => {
        const address = JSON.parse(key);
        return {id: `spatial:${key}`, name: address.at(-1), kind: KINDS[address.length - 1], address};
      });
    }
    for (const source of sources) {
      if (!source || !validPath(source.address)) continue;
      const path = [...source.address], key = pathKey(path), kind = String(source.kind || KINDS[path.length - 1]).toUpperCase();
      if (kind !== KINDS[path.length - 1] || model.byPath.has(key)) continue;
      const id = String(source.id || `spatial:${key}`);
      if (model.byId.has(id)) continue;
      const extent = extents.get(key);
      const bounds = readBounds(source.bounds) || (extent ? {x: extent[0], y: extent[1],
        width: extent[2] - extent[0] + 1, height: extent[3] - extent[1] + 1} : null);
      const node = {id, path, kind, name: path.at(-1), parentId: source.parent_id ? String(source.parent_id) : null,
        children: [], bounds, coord: null, objects: [], semantic: String(source.semantic || ''), sortOrder: Number(source.sort_order) || 0};
      if (path.length >= 3 && bounds) {
        const center = [bounds.x + (bounds.width - 1) / 2, bounds.y + (bounds.height - 1) / 2];
        let bestDistance = Infinity;
        for (const tile of exactTiles.get(key) || []) {
          if (tile.collision === true || !inside(tile.coord, bounds)) continue;
          const [x, y] = tile.coord, distance = (x - center[0]) ** 2 + (y - center[1]) ** 2;
          if (distance < bestDistance || (distance === bestDistance &&
              (!node.coord || y < node.coord[1] || (y === node.coord[1] && x < node.coord[0])))) {
            bestDistance = distance; node.coord = [x, y];
          }
        }
      }
      model.nodes.push(node); model.byId.set(id, node); model.byPath.set(key, node);
    }
    // Only expose complete hierarchy paths; invalid/orphaned data cannot become a selectable address.
    const attached = new Set();
    for (const node of [...model.nodes].sort((a, b) => a.path.length - b.path.length)) {
      if (node.path.length === 1) { model.roots.push(node); attached.add(node.id); continue; }
      const parent = model.byPath.get(pathKey(node.path.slice(0, -1)));
      if (!parent || !attached.has(parent.id) || (node.parentId && node.parentId !== parent.id)) continue;
      node.parentId = parent.id; parent.children.push(node); attached.add(node.id);
    }
    model.nodes = model.nodes.filter(node => {
      if (attached.has(node.id)) return true;
      model.byId.delete(node.id); model.byPath.delete(pathKey(node.path)); return false;
    });
    const compare = (a, b) => a.sortOrder - b.sortOrder || a.name.localeCompare(b.name, 'zh-CN') || a.id.localeCompare(b.id);
    model.roots.sort(compare);
    for (const node of model.nodes) {
      node.children.sort(compare);
      node.objects = node.children.filter(child => child.kind === 'GAME_OBJECT').map(child => child.name);
    }
    return model;
  }

  let activePicker = null, sequence = 0;
  function open({world, title = '选择空间位置', selectedPath = [], mode = 'address', onSelect} = {}) {
    activePicker?.close();
    const model = buildModel(world), document = root.document;
    const previousFocus = document.activeElement;
    const expanded = new Set(model.nodes.filter(node => node.path.length <= 2).map(node => node.id));
    let selected = validPath(selectedPath) ? model.byPath.get(pathKey(selectedPath)) || null : null;
    if (selected) for (let level = 1; level < selected.path.length; level++) {
      const ancestor = model.byPath.get(pathKey(selected.path.slice(0, level)));
      if (ancestor) expanded.add(ancestor.id);
    }
    const element = (tag, className, text) => {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = text;
      return node;
    };
    const button = (className, text, handler) => {
      const node = element('button', className, text); node.type = 'button'; node.onclick = handler; return node;
    };
    const backdrop = element('div', 'spatial-picker-backdrop');
    const dialog = element('section', 'spatial-picker-dialog');
    const headingId = `spatial-picker-title-${++sequence}`;
    dialog.setAttribute('role', 'dialog'); dialog.setAttribute('aria-modal', 'true'); dialog.setAttribute('aria-labelledby', headingId);
    const header = element('header', 'spatial-picker-header');
    const heading = element('h2', '', title); heading.id = headingId;
    const closeButton = button('spatial-picker-close', '×', () => close()); closeButton.setAttribute('aria-label', '关闭空间选择');
    header.append(heading, closeButton);
    const help = element('p', 'spatial-picker-help', mode === 'spawn'
      ? '选择场所或对象，自动定位到该地址内靠近中心的可行走位置。'
      : mode === 'space' ? '选择场所，或展开场所选择一个已知对象。' : '从世界、区域、场所到对象，选择需要的空间地址。');
    const search = element('input', 'spatial-picker-search');
    search.type = 'search'; search.placeholder = '搜索名称或完整地址'; search.setAttribute('aria-label', '搜索空间地址');
    const content = element('div', 'spatial-picker-content');
    const tree = element('div', 'spatial-picker-tree'); tree.setAttribute('role', 'tree'); tree.setAttribute('aria-label', '四层空间树');
    const preview = element('aside', 'spatial-picker-preview'); preview.setAttribute('aria-live', 'polite');
    const footer = element('footer', 'spatial-picker-footer');
    const cancel = button('btn spatial-picker-cancel', '取消', () => close());
    const confirm = button('btn btn-primary spatial-picker-confirm', '确认选择', () => {
      if (!canSelect(selected)) return;
      const result = {path: [...selected.path], coord: mode === 'spawn' ? [...selected.coord] : null,
        objects: mode === 'space' && selected.kind === 'GAME_OBJECT' ? [selected.name] : []};
      close(); if (typeof onSelect === 'function') onSelect(result);
    });
    footer.append(cancel, confirm); content.append(tree, preview);
    dialog.append(header, help, search, content, footer); backdrop.append(dialog); document.body.append(backdrop);
    function canSelect(node) {
      return !!node && (mode === 'spawn' ? !!node.coord && node.path.length >= 3 :
        mode === 'space' ? node.path.length >= 3 : true);
    }
    function renderPreview() {
      preview.replaceChildren(); confirm.disabled = !canSelect(selected);
      if (!selected) {
        preview.append(element('strong', '', '尚未选择位置'), element('p', '', '点击左侧空间节点查看完整地址。'));
        return;
      }
      preview.append(element('span', 'spatial-picker-level', `L${selected.path.length} ${LABELS[selected.path.length - 1]}`),
        element('h3', '', selected.name), element('p', 'spatial-picker-address', selected.path.join(' → ')));
      if (selected.semantic) preview.append(element('p', 'spatial-picker-semantic', selected.semantic));
      if (mode === 'spawn') {
        if (selected.coord) preview.append(element('p', 'spatial-picker-result', `初始坐标：(${selected.coord.join(', ')})`),
          element('p', '', '已选择此完整地址内最靠近中心的可行走格。'));
        else preview.append(element('p', 'spatial-picker-unavailable', selected.path.length < 3
          ? '请继续展开，选择具体场所或对象。' : '此地址没有可行走位置，请选择其他场所或对象。'));
      } else if (mode === 'space') {
        preview.append(element('p', canSelect(selected) ? 'spatial-picker-result' : 'spatial-picker-unavailable',
          selected.path.length < 3 ? '请继续展开，选择具体场所或对象。' : selected.kind === 'GAME_OBJECT'
            ? `将添加已知对象：${selected.name}` : '将添加此场所，不自动添加场所内的对象。'));
      }
    }
    function renderTree() {
      tree.replaceChildren();
      if (!model.nodes.length) {
        tree.append(element('p', 'spatial-picker-empty', model.hasMap
          ? '地图尚无可选择的空间节点，请先完善地图四层空间。' : '请先为实验导入地图，再选择空间位置。'));
        renderPreview(); return;
      }
      const query = search.value.trim().toLocaleLowerCase();
      const matches = new Set();
      for (const node of model.nodes) if (!query || node.path.join(' → ').toLocaleLowerCase().includes(query)) {
        matches.add(node.id);
        for (let level = 1; level < node.path.length; level++) {
          const ancestor = model.byPath.get(pathKey(node.path.slice(0, level)));
          if (ancestor) matches.add(ancestor.id);
        }
      }
      const appendNode = (node, container) => {
        if (!matches.has(node.id)) return;
        const branch = element('div', 'spatial-picker-branch');
        const row = element('div', 'spatial-picker-row');
        row.style.setProperty('--spatial-depth', String(node.path.length - 1));
        if (selected?.id === node.id) row.classList.add('is-selected');
        const isExpanded = !!query || expanded.has(node.id);
        const toggle = button('spatial-picker-toggle', node.children.length ? (isExpanded ? '▾' : '▸') : '', () => {
          if (expanded.has(node.id)) expanded.delete(node.id); else expanded.add(node.id);
          renderTree(); tree.querySelector(`[data-toggle-id="${nodeIndex(node)}"]`)?.focus();
        });
        toggle.dataset.toggleId = nodeIndex(node);
        toggle.disabled = !node.children.length;
        toggle.setAttribute('aria-label', `${isExpanded ? '折叠' : '展开'}${node.name}`);
        if (node.children.length) toggle.setAttribute('aria-expanded', String(isExpanded));
        const choice = button('spatial-picker-choice', '', () => {
          selected = node;
          if (node.children.length) expanded.add(node.id);
          renderTree(); tree.querySelector(`[data-node-id="${nodeIndex(node)}"]`)?.focus();
        });
        choice.dataset.nodeId = nodeIndex(node); choice.title = node.path.join(' → ');
        choice.setAttribute('role', 'treeitem'); choice.setAttribute('aria-level', String(node.path.length));
        choice.setAttribute('aria-selected', String(selected?.id === node.id));
        if (node.children.length) choice.setAttribute('aria-expanded', String(isExpanded));
        choice.append(element('span', 'spatial-picker-node-name', node.name),
          element('span', 'spatial-picker-node-level', `L${node.path.length} ${LABELS[node.path.length - 1]}`));
        if (mode === 'spawn' && node.path.length >= 3 && !node.coord)
          choice.append(element('span', 'spatial-picker-node-disabled', '无可走位置'));
        row.append(toggle, choice); branch.append(row); container.append(branch);
        if (isExpanded) for (const child of node.children) appendNode(child, branch);
      };
      for (const node of model.roots) appendNode(node, tree);
      if (!matches.size) tree.append(element('p', 'spatial-picker-empty', '未找到匹配的空间地址。'));
      renderPreview();
    }
    const nodeIndices = new Map(model.nodes.map((node, index) => [node.id, String(index)]));
    function nodeIndex(node) { return nodeIndices.get(node.id); }
    function onKeydown(event) {
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopImmediatePropagation(); close();
      } else if (event.key === 'Tab') {
        event.stopImmediatePropagation();
        const focusable = [...dialog.querySelectorAll('button:not([disabled]), input')];
        const first = focusable[0], last = focusable.at(-1);
        if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
          event.preventDefault(); last?.focus();
        } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
          event.preventDefault(); first?.focus();
        }
      }
    }
    let closed = false;
    function close() {
      if (closed) return;
      closed = true; backdrop.remove(); document.removeEventListener('keydown', onKeydown, true);
      if (activePicker?.close === close) activePicker = null;
      if (previousFocus?.isConnected) previousFocus.focus();
    }
    backdrop.onclick = event => { if (event.target === backdrop) close(); };
    search.oninput = renderTree;
    document.addEventListener('keydown', onKeydown, true);
    renderTree(); search.focus();
    activePicker = {close, model}; return activePicker;
  }
  const api = {buildModel, open};
  root.SpatialPicker = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
