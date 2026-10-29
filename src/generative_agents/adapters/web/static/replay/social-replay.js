/* Social playback reads committed projections; previews are a separate, temporary layer. */
(function (root, factory) {
  const exported = factory();
  if (typeof module === 'object' && module.exports) module.exports = exported;
  root.GASocialReplayPlayer = exported.GASocialReplayPlayer;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';
  const escape = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#039;');
  const order = (a, b) => Number(a.step_no || 0) - Number(b.step_no || 0) || Number(a.actor_sequence || 0) - Number(b.actor_sequence || 0) || Number(a.sequence || 0) - Number(b.sequence || 0) || String(a.message_id).localeCompare(String(b.message_id));

  function mergeConversations(committed, preview = []) {
    const records = new Map();
    for (const [items, pending] of [[committed, false], [preview, true]]) {
      for (const conversation of items || []) {
        const id = conversation.conversation_id;
        const existing = records.get(id);
        const messages = existing?._messages || new Map();
        for (const message of conversation.messages || []) {
          if (!message.message_id || messages.has(message.message_id)) continue;
          messages.set(message.message_id, {...message, pending});
        }
        records.set(id, {...conversation, ...existing, _messages: messages});
      }
    }
    return [...records.values()].map(({_messages, ...item}) => ({...item, messages: [..._messages.values()].sort(order)}));
  }

  function forumTree(messages) {
    const nodes = new Map((messages || []).map(message => [message.message_id, {...message, children: []}]));
    const roots = [];
    for (const node of nodes.values()) {
      const parent = nodes.get(node.reply_to_message_id);
      // Cycles are invalid runtime facts; tolerate malformed display data without recursion loops.
      const ancestors = new Set([node.message_id]);
      let cursor = parent;
      while (cursor && !ancestors.has(cursor.message_id)) { ancestors.add(cursor.message_id); cursor = nodes.get(cursor.reply_to_message_id); }
      if (parent && !cursor) parent.children.push(node); else roots.push(node);
    }
    return roots;
  }

  class GASocialReplayPlayer {
    static resolveAgentSelection(key, selectedExperiment, experiment, agents) { return key && selectedExperiment === experiment && agents.some(agent => agent.agent_key === key) ? key : null; }
    constructor(options = {}) {
      Object.assign(this, {onStep: () => {}, onStatus: () => {}, onError: () => {}, onSelection: () => {}, fetchImpl: (...args) => fetch(...args)}, options);
      this.isSocial = true; this.availableStep = 0; this.currentStep = 0; this.pendingStep = null;
      this.speed = 1; this.generation = 0; this.seekRequest = 0; this.conversations = []; this.preview = null;
      this.expanded = new Set(); this.collapsed = new Set(); this.selectedAgentKey = null; this.privateConversationId = null;
      this.live = false; this.timer = null; this.pollTimer = null; this.ready = false;
    }
    async loadRun(runId, {signal, manifest} = {}) {
      this.destroy();
      this.runId = runId; this.abortController = new AbortController();
      signal?.addEventListener('abort', () => this.abortController?.abort(), {once: true});
      const generation = ++this.generation;
      this.onStatus({state: 'LOADING', runId});
      this.manifest = manifest || await this.json('/replay/manifest');
      if (!this.owns(generation)) return;
      this.availableStep = Number(this.manifest.available_step || 0);
      this.live = Boolean(this.manifest.partial);
      this.host = document.createElement('section'); this.host.className = 'social-replay';
      this.container.appendChild(this.host);
      this.container.classList.add('social-result-map');
      this.canvas.hidden = true;
      this.host.addEventListener('click', event => this.click(event).catch(error => this.reportReadError(error)));
      this.host.addEventListener('toggle', event => {
        const id = event.target.dataset?.socialBranch;
        if (id) { if (event.target.open) { this.expanded.add(id); this.collapsed.delete(id); } else { this.expanded.delete(id); this.collapsed.add(id); } }
      }, true);
      await this.seek(this.live ? this.availableStep : Math.min(1, this.availableStep), {live: this.live});
      if (!this.owns(generation)) return;
      this.ready = true; this.onStatus({state: 'READY', runId, availableStep: this.availableStep});
      this.schedulePoll();
    }
    owns(generation) { return Boolean(this.runId && generation === this.generation && !this.abortController?.signal.aborted); }
    async json(path) {
      const response = await this.fetchImpl(`/api/studio/runs/${encodeURIComponent(this.runId)}${path}`, {signal: this.abortController?.signal});
      const body = await response.json();
      if (!response.ok) throw new Error(body.error?.message || body.detail?.message || '社交内容读取失败');
      return body;
    }
    destroy() {
      this.pause({notify: false}); clearTimeout(this.pollTimer); this.pollTimer = null;
      this.abortController?.abort(); this.generation += 1; this.seekRequest += 1;
      this.host?.remove(); this.host = null; this.container?.classList.remove('social-result-map');
      if (this.canvas) this.canvas.hidden = false;
      this.runId = null; this.ready = false; this.preview = null; this.conversations = []; this.lastMarkup = null; this.readError = null;
      this.selectedAgentKey = null; this.privateConversationId = null; this.expanded.clear(); this.collapsed.clear();
    }
    schedulePoll() {
      clearTimeout(this.pollTimer);
      if (!this.runId || !this.live || !this.manifest?.partial) return;
      this.pollTimer = setTimeout(async () => {
        try { await this.refreshAvailable(); }
        catch (error) { this.reportReadError(error); }
        finally { this.schedulePoll(); }
      }, 1000);
    }
    reportReadError(error) {
      if (error.name === 'AbortError' || !this.runId) return;
      this.preview = null; this.readError = error.message || '内容读取失败'; this.render();
      this.onError({...error, message: this.readError, nonFatal: true});
    }
    async refreshAvailable() {
      if (!this.runId || this.refreshing) return;
      this.refreshing = true;
      const generation = this.generation;
      try {
        const available = await this.json('/replay/availability');
        if (!this.owns(generation)) return;
        const changed = Number(available.available_step || 0) !== this.availableStep;
        this.availableStep = Number(available.available_step || 0);
        this.manifest = {...this.manifest, ...available};
        if (this.live && (changed || this.currentStep !== this.availableStep)) await this.seek(this.availableStep, {live: true});
        if (!this.owns(generation)) return;
        if (this.live) {
          let request = this.seekRequest;
          const preview = await this.json('/runtime-preview');
          if (!this.owns(generation) || request !== this.seekRequest || !this.live) return;
          if (Number(preview.committed_step || 0) > this.availableStep) {
            this.availableStep = Number(preview.committed_step);
            await this.seek(this.availableStep, {live: true});
            request += 1;
            if (!this.owns(generation) || request !== this.seekRequest || !this.live) return;
          }
          this.preview = preview.active !== false && preview.uncommitted && preview.run_id === this.runId && Number(preview.step_no) > this.availableStep ? preview : null;
        } else this.preview = null;
        this.readError = null; this.render();
        this.onStatus({state: this.playing ? 'PLAYING' : 'AVAILABLE_STEP', runId: this.runId, availableStep: this.availableStep});
      } finally { this.refreshing = false; }
    }
    async seek(stepNo, {live = false} = {}) {
      if (!this.runId) return;
      this.live = live; this.preview = null;
      this.render();
      const target = Math.max(0, Math.min(this.availableStep, Number(stepNo) || 0));
      const request = ++this.seekRequest, generation = this.generation;
      this.pendingStep = target;
      const [projection, frames] = await Promise.all([
        this.json(`/results/social?step_no=${target}`),
        target ? this.json(`/replay/steps?from_step=${target}&limit=1`) : Promise.resolve({steps: []}),
      ]);
      if (!this.owns(generation) || request !== this.seekRequest) return;
      if (projection.run_id !== this.runId) throw new Error('社交内容不属于当前 Run');
      this.currentStep = target; this.pendingStep = null; this.conversations = projection.conversations || [];
      const step = (frames.steps || frames.items || [])[0] || {step_no: target, virtual_time: this.manifest.start_time, conversations: [], domain_events: [], agents: [], memory_deltas: []};
      this.currentStepFact = step;
      this.render(); this.onStep({step, availableStep: this.availableStep});
      return step;
    }
    async stepBy(delta) { return this.seek((this.pendingStep ?? this.currentStep) + Number(delta)); }
    async play() {
      if (this.timer || !this.availableStep) return;
      this.live = false; this.preview = null; this.playing = true;
      if (this.currentStep >= this.availableStep) await this.seek(1);
      const advance = async () => {
        this.timer = null;
        if (!this.runId || !this.playing) return;
        try { await this.stepBy(1); if (this.playing && this.currentStep < this.availableStep) this.timer = setTimeout(advance, 700 / this.speed); else this.pause(); }
        catch (error) { this.onError(error); this.pause(); }
      };
      this.timer = setTimeout(advance, 700 / this.speed); this.onStatus({state: 'PLAYING', availableStep: this.availableStep});
    }
    pause({notify = true} = {}) { clearTimeout(this.timer); this.timer = null; this.playing = false; if (notify && this.runId) this.onStatus({state: 'PAUSED', availableStep: this.availableStep}); }
    setSpeed(value) { this.speed = Math.max(.5, Math.min(16, Number(value) || 1)); }
    setLayerVisibility() {}
    followAgent() {}
    selectAgent(key) { if (key !== this.selectedAgentKey) { this.selectedAgentKey = key; this.privateConversationId = null; this.render(); } }
    async click(event) {
      const button = event.target.closest('button'); if (!button) return;
      if (button.dataset.socialMode) {
        this.pause(); await this.seek(this.availableStep, {live: button.dataset.socialMode === 'live'}); await this.refreshAvailable(); this.schedulePoll();
      } else if (button.dataset.socialConversation) { this.privateConversationId = button.dataset.socialConversation; this.render(); }
      else if (button.hasAttribute('data-social-private-back')) { this.privateConversationId = null; this.render(); }
      else if (button.hasAttribute('data-social-private-close')) { this.selectedAgentKey = null; this.privateConversationId = null; this.onSelection(null); this.render(); }
      else if (button.dataset.socialTarget) {
        const target = [...this.host.querySelectorAll('[data-social-message]')].find(node => node.dataset.socialMessage === button.dataset.socialTarget);
        if (target) { let parent = target.parentElement; while (parent && parent !== this.host) { if (parent.tagName === 'DETAILS') parent.open = true; parent = parent.parentElement; } target.scrollIntoView({block: 'center', behavior: 'smooth'}); target.focus(); }
      }
    }
    name(key) { return this.manifest?.agents?.find(agent => agent.agent_key === key)?.display_name || key; }
    message(message, byId, depth = 0) {
      const quote = byId.get(message.quoted_message_id);
      const parent = byId.get(message.reply_to_message_id);
      return `<article class="social-message${message.pending ? ' pending' : ''}" data-social-message="${escape(message.message_id)}" tabindex="-1"><div class="social-message-meta"><strong>${escape(message.speaker_name || this.name(message.speaker_agent_key))}</strong><span>Step ${escape(message.step_no || this.currentStep)} · 顺序 ${escape(message.actor_sequence || message.sequence)}${message.pending ? ' · 未提交' : ''}</span></div>${message.title ? `<h3>${escape(message.title)}</h3>` : ''}${parent ? `<button class="social-reference" data-social-target="${escape(parent.message_id)}">回复 ${escape(parent.speaker_name || this.name(parent.speaker_agent_key))}</button>` : ''}${quote ? `<button class="social-quote" data-social-target="${escape(quote.message_id)}">引用 ${escape(quote.speaker_name || this.name(quote.speaker_agent_key))}：${escape(quote.content)}</button>` : ''}<p>${escape(message.content)}</p>${message.children?.length ? `<details class="social-comments" data-social-branch="${escape(message.message_id)}"${this.expanded.has(message.message_id) || depth === 0 && !this.collapsed.has(message.message_id) ? ' open' : ''}><summary>${message.children.length} 条回复</summary>${message.children.map(child => this.message(child, byId, depth + 1)).join('')}</details>` : ''}</article>`;
    }
    render() {
      if (!this.host || !this.manifest) return;
      const previousScroll = this.host.querySelector('.social-stream')?.scrollTop || 0;
      const privateScroll = this.host.querySelector('.social-private-content')?.scrollTop || 0;
      const previousStream = this.host.querySelector('.social-stream');
      const following = !previousStream || previousStream.scrollHeight - previousStream.scrollTop - previousStream.clientHeight < 80;
      const all = mergeConversations(this.conversations, this.live ? this.preview?.conversations : []);
      const publicConversations = all.filter(item => item.visibility === 'public');
      const publicMessages = publicConversations.flatMap(item => item.messages).sort(order);
      const publicById = new Map(publicMessages.map(message => [message.message_id, message]));
      const forum = this.manifest.presentation === 'forum' || this.manifest.world?.definition?.presentation === 'forum';
      const messages = forum ? publicConversations.map(conversation => `<section class="social-thread">${forumTree(conversation.messages).map(message => this.message(message, publicById)).join('')}</section>`).join('') : publicMessages.map(message => this.message(message, publicById)).join('');
      const progress = this.preview ? `Step ${this.preview.step_no} · 已执行 ${this.preview.actor_sequence} / ${this.preview.actor_total} 位智能体；本轮消息尚未提交` : `已提交至 Step ${this.availableStep}`;
      const markup = `<header class="social-replay-head"><div><strong>${escape(this.manifest.world?.world_name || (forum ? '论坛' : '群聊'))}</strong><small>${forum ? '公开帖子与评论' : '公共群聊'} · ${this.live ? '实时运行' : `回放 Step ${this.currentStep}`}</small></div><div class="social-mode-controls"><button class="btn btn-sm${this.live ? ' active' : ''}" data-social-mode="live">实时运行</button><button class="btn btn-sm${!this.live ? ' active' : ''}" data-social-mode="replay">正式回放</button></div></header>${this.readError ? `<div class="social-read-error" role="alert">${escape(this.readError)}；正在等待重试。</div>` : ''}<div class="social-progress" role="status">${escape(this.live ? progress : `仅显示 Step ${this.currentStep} 及以前的已提交内容`)}</div><div class="social-stream${forum ? ' forum' : ''}">${messages || '<div class="social-empty">还没有公开发言。智能体可选择发言或保持沉默。</div>'}</div>${this.privatePanel(all)}`;
      if (markup === this.lastMarkup) return;
      this.lastMarkup = markup; this.host.innerHTML = markup;
      const stream = this.host.querySelector('.social-stream'); if (stream) stream.scrollTop = this.live && following && !forum ? stream.scrollHeight : previousScroll;
      const privateContent = this.host.querySelector('.social-private-content'); if (privateContent) privateContent.scrollTop = privateScroll;
    }
    privatePanel(conversations) {
      if (!this.selectedAgentKey) return '';
      const own = conversations.filter(item => item.visibility === 'private' && (item.participant_agent_keys || item.participants || []).includes(this.selectedAgentKey));
      const selected = own.find(item => item.conversation_id === this.privateConversationId);
      const participantNames = item => (item.participant_agent_keys || item.participants || []).filter(key => key !== this.selectedAgentKey).map(key => this.name(key)).join('、');
      let content;
      if (selected) {
        const byId = new Map(selected.messages.map(message => [message.message_id, message]));
        content = `<button class="btn btn-sm" data-social-private-back>‹ 返回会话列表</button><h3>${escape(participantNames(selected))}</h3>${selected.messages.map(message => this.message(message, byId)).join('')}`;
      } else content = own.map(item => `<button class="social-private-choice" data-social-conversation="${escape(item.conversation_id)}"><strong>${escape(participantNames(item))}</strong><span>${escape(item.messages.at(-1)?.content || '')}</span><small>${item.messages.length} 条消息${item.messages.some(message => message.pending) ? ' · 含未提交消息' : ''}</small></button>`).join('') || '<div class="social-empty">当前回放范围内暂无私聊。</div>';
      return `<aside class="social-private-panel" role="dialog" aria-label="${escape(this.name(this.selectedAgentKey))}的私聊"><header><strong>${escape(this.name(this.selectedAgentKey))}的私聊</strong><button class="btn btn-sm" data-social-private-close aria-label="关闭私聊">×</button></header><small class="social-private-hint">观察者查看 · 仅会话参与者能在仿真中读取</small><div class="social-private-content">${content}</div></aside>`;
    }
  }
  return {GASocialReplayPlayer, mergeConversations, forumTree, order};
});
