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
      this.root.innerHTML = `<div class="social-map-editor"><header><span class="map-state">1D · ${definition.presentation === 'forum' ? '论坛' : '群聊'}</span><h2>${escape(this.world.world_name)}</h2><p>所有参与者均可读取公开内容；私聊仅对会话参与者可见。发言、回复和保持沉默由 Brain 与 Skill 决定。</p></header><div class="field"><label for="socialMapName">地图名称</label><input id="socialMapName" class="control" maxlength="120" value="${escape(this.world.world_name)}" ${this.readonly ? 'disabled' : ''} /></div><div class="field"><label for="socialMapDescription">场所说明</label><textarea id="socialMapDescription" class="control" rows="4" ${this.readonly ? 'disabled' : ''}>${escape(definition.description)}</textarea></div><div class="field"><label for="socialMapRules">场所规则</label><textarea id="socialMapRules" class="control" rows="8" placeholder="例如：讨论的主题、参与者身份与交流约定。" ${this.readonly ? 'disabled' : ''}>${escape(definition.rules)}</textarea><small>自然语言规则会进入参与者上下文；系统负责公开/私聊权限、引用关系与消息顺序。</small></div><div class="social-map-example"><strong>${definition.presentation === 'forum' ? '帖子与嵌套评论' : '公共消息与引用回复'}</strong><p>${definition.presentation === 'forum' ? '运行中由智能体发表主题帖，其他智能体可回复主题或回复评论。' : '运行中按智能体顺序发表消息，后续智能体可以读取本轮已有消息并引用回复。'}</p><small>每张地图固定一种展现方式，私聊在两种方式中均可使用。</small></div><section><div class="map-audit-cards" data-map-audit-cards></div><div data-map-validation></div><div data-map-revisions></div></section></div>`;
      [['socialMapName', 'world_name'], ['socialMapDescription', 'description'], ['socialMapRules', 'rules']].forEach(([id, field]) => {
        this.root.querySelector(`#${id}`).addEventListener('input', event => {
          if (this.readonly) return;
          if (field === 'world_name') this.world.world_name = event.target.value;
          else this.definition[field] = event.target.value;
          this.changed = true; this.changeRevision += 1;
          this.root.dispatchEvent(new CustomEvent('map-editor-v2:change', {bubbles: true}));
        });
      });
    }
  }
  window.SocialMapEditor = SocialMapEditor;
})();
