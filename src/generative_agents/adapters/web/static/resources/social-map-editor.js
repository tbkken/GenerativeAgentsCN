/* Social maps share the map workspace's save/recovery contract, without a canvas. */
(() => {
  'use strict';
  const escape = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#039;');
  class SocialMapEditor {
    constructor(root) { this.root = root; this.changed = false; this.changeRevision = 0; this.readonly = false; }
    setWorld(world) { this.world = structuredClone(world); this.definition = this.world.definition; this.changed = false; this.render(); }
    getWorld() { return structuredClone(this.world); }
    setReadOnly(value) { this.readonly = Boolean(value); this.render(); }
    refreshSkillCatalog() { return Promise.resolve(); }
    resize() {}
    fit() {}
    destroy() { this.root.replaceChildren(); }
    acceptSavedWorld(world, revision) {
      if (revision !== this.changeRevision) return;
      this.world = structuredClone(world); this.definition = this.world.definition; this.changed = false;
    }
    render() {
      const definition = this.definition;
      if (!definition) return;
      this.root.innerHTML = `<div class="social-map-editor">
        <div class="social-map-fields" role="group" aria-label="地图信息">
          <div class="field"><label for="socialMapName">地图名称</label><input id="socialMapName" class="control" maxlength="120" value="${escape(this.world.world_name)}" ${this.readonly ? 'disabled' : ''} /></div>
          <div class="field"><label for="socialMapKey">稳定键</label><input id="socialMapKey" class="control" value="${escape(this.world.world_key)}" readonly /></div>
          <div class="field"><label for="socialMapType">地图类型</label><input id="socialMapType" class="control" value="1D 社交空间" readonly /></div>
          <div class="field"><label for="socialMapPresentation">展现方式</label><input id="socialMapPresentation" class="control" value="${definition.presentation === 'forum' ? '论坛' : '群聊'}" readonly /></div>
          <div class="field social-map-description"><label for="socialMapDescription">用途说明</label><textarea id="socialMapDescription" class="control" rows="4" maxlength="10000" ${this.readonly ? 'disabled' : ''}>${escape(definition.description)}</textarea></div>
        </div>
        <section data-map-validation></section>
      </div>`;
      [['socialMapName', 'world_name'], ['socialMapDescription', 'description']].forEach(([id, field]) => {
        this.root.querySelector(`#${id}`).addEventListener('input', event => {
          if (this.readonly) return;
          // The saved document can replace world/definition while these inputs stay focused.
          (field === 'world_name' ? this.world : this.definition)[field] = event.target.value;
          this.changed = true; this.changeRevision += 1;
          this.root.dispatchEvent(new CustomEvent('map-editor-v2:change', {bubbles: true}));
        });
      });
    }
  }
  window.SocialMapEditor = SocialMapEditor;
})();
