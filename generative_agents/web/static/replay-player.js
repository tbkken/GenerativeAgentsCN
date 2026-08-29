/**
 * 正式 Replay Bundle V2 播放器；Phaser 来自项目内置 vendor 资源。
 *
 * 播放器只消费服务端验证后的清单与步骤窗口，不读取实验草稿。每个实例拥有自己的
 * Phaser Game、缓存窗口和选中智能体；切换 Run 时调用 destroy() 释放旧画布和监听器。
 */
(function replayModule(root, factory) {
  const exported = factory();
  if (typeof module === 'object' && module.exports) module.exports = exported;
  root.GAReplayPlayer = exported.GAReplayPlayer;
})(typeof window !== 'undefined' ? window : globalThis, function createReplayModule() {
  'use strict';

  const TILESET_NAMES = [
    'CuteRPG_Field_B', 'CuteRPG_Field_C', 'CuteRPG_Harbor_C',
    'CuteRPG_Village_B', 'CuteRPG_Forest_B', 'CuteRPG_Desert_C',
    'CuteRPG_Mountains_B', 'CuteRPG_Desert_B', 'CuteRPG_Forest_C',
    'interiors_pt1', 'interiors_pt2', 'interiors_pt3', 'interiors_pt4',
    'interiors_pt5',
  ];
  const LAYERS = [
    'Bottom Ground', 'Exterior Ground', 'Exterior Decoration L1',
    'Exterior Decoration L2', 'Interior Ground', 'Wall',
    'Interior Furniture L1', 'Interior Furniture L2 ',
    'Foreground L1', 'Foreground L2', 'Collisions',
  ];
  const INITIAL_CAMERA_ZOOM = 0.7;
  const DISPLAY_RENDER_RESOLUTION = Math.min(2, Math.max(1, Number(globalThis.devicePixelRatio || 1)));
  const TEXT_RENDER_RESOLUTION = Math.max(2, DISPLAY_RENDER_RESOLUTION);
  const REPLAY_FONT_FAMILY = '"Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif';
  // 名称与动作气泡使用独立偏移，长名称不会和动作表情争夺同一位置。
  const AGENT_NAME_OFFSET = Object.freeze({ x: -18, y: -44 });
  const ACTION_BUBBLE_OFFSET = Object.freeze({ x: 38, y: -24 });
  const SPATIAL_HIERARCHY_DEPTH = Object.freeze({
    WORLD: 1,
    SECTOR: 2,
    ARENA: 3,
    GAME_OBJECT: 4,
  });

  class GAReplayPlayer {
    static resolveAgentSelection(selectedKey, selectedRevisionId, runRevisionId, agents) {
      if (!selectedKey || !selectedRevisionId || selectedRevisionId !== runRevisionId) return null;
      return agents.some(agent => agent.agent_key === selectedKey) ? selectedKey : null;
    }

    static orderedSpatialHierarchyNodes(nodes, maxDepth = 4) {
      return (Array.isArray(nodes) ? [...nodes] : [])
        .filter(node => Number(SPATIAL_HIERARCHY_DEPTH[node?.kind] || 0) <= Number(maxDepth))
        .filter(node => SPATIAL_HIERARCHY_DEPTH[node?.kind] && node?.material_slice_id)
        .sort((left, right) => (
          SPATIAL_HIERARCHY_DEPTH[left.kind] - SPATIAL_HIERARCHY_DEPTH[right.kind]
          || Number(left.sort_order || 0) - Number(right.sort_order || 0)
          || String(left.id || '').localeCompare(String(right.id || ''))
        ));
    }

    constructor(options = {}) {
      this.canvas = options.canvas || null;
      this.onStep = options.onStep || (() => {});
      this.onAgent = options.onAgent || (() => {});
      this.onStatus = options.onStatus || (() => {});
      this.onError = options.onError || (() => {});
      this.fetchImpl = options.fetchImpl || ((...args) => fetch(...args));
      this.windowSize = Math.min(100, Math.max(1, options.windowSize || 100));
      this.runId = null;
      this.manifest = null;
      this.availableStep = 0;
      this.resultVersion = 0;
      this.currentStep = 0;
      this.speed = 1;
      this.timer = null;
      this.ready = false;
      this.seekRequest = 0;
      this.pendingStep = null;
      this.game = null;
      this.scene = null;
      this.abortController = null;
      this.generation = 0;
      this.windows = new Map();
      this.worldStateBefore = new Map();
      this.agentObjects = new Map();
      this.agentDefinitions = new Map();
      this.selectedAgentKey = null;
      this.followedAgentKey = null;
      this.layerVisibility = {
        agentNames: true,
        actionBubbles: true,
        trails: true,
        conversations: true,
        keyEvents: true,
      };
      this.mapLayers = new Map();
      this.worldObjects = new Map();
      this.resizeObserver = null;
    }

    async loadRun(runId, { signal } = {}) {
      this.destroy();
      this.runId = runId;
      const generation = ++this.generation;
      this.abortController = new AbortController();
      this.ready = false;
      this.pendingStep = null;
      if (signal) signal.addEventListener('abort', () => this.abortController?.abort(), { once: true });
      this.onStatus({ state: 'LOADING', runId });
      const manifest = await this._json(`/api/v1/runs/${encodeURIComponent(runId)}/replay/manifest`, generation);
      if (!this._owns(runId, generation)) return;
      this._validateManifest(manifest, runId);
      this.manifest = manifest;
      this.availableStep = manifest.available_step;
      this.agentDefinitions = new Map(manifest.agents.map(item => [item.agent_key, item]));
      await this._createGame(manifest, generation);
      if (!this._owns(runId, generation)) return;
      if (this.availableStep > 0) await this.seek(1);
      if (!this._owns(runId, generation)) return;
      this.ready = true;
      this.onStatus({ state: 'READY', runId, availableStep: this.availableStep, currentStep: this.currentStep, partial: manifest.partial });
    }

    destroy() {
      this.pause({ notify: false });
      this.ready = false;
      this.seekRequest += 1;
      this.pendingStep = null;
      if (this.resizeObserver) this.resizeObserver.disconnect();
      this.resizeObserver = null;
      if (this.abortController) this.abortController.abort();
      this.abortController = null;
      this.windows.clear();
      this.worldStateBefore.clear();
      this.agentObjects.clear();
      this.mapLayers.clear();
      this.worldObjects.clear();
      // `canvas` is the package shell's single long-lived DOM node.  Phaser's
      // removeCanvas=true would detach it and make the next Run impossible to
      // mount.  Destroy the renderer/game state while retaining that owned
      // canvas for the next explicit Phaser.Game instance.
      if (this.game && typeof this.game.destroy === 'function') this.game.destroy(false, false);
      this.game = null;
      this.scene = null;
      this.runId = null;
      this.manifest = null;
      this.currentStep = 0;
    }

    async play() {
      if (this.timer || !this.runId || !this.ready || this.availableStep < 1) return;
      if (this.pendingStep !== null) await this.seek(this.pendingStep);
      if (this.currentStep >= this.availableStep) await this.seek(1);
      if (!this.runId || !this.ready) return;
      const runId = this.runId;
      const generation = this.generation;
      const scheduleNext = () => {
        this.timer = setTimeout(async () => {
          if (!this.timer || !this._owns(runId, generation) || !this.ready) return;
          try {
            await this.stepBy(1);
            if (!this.timer) return;
            if (this.currentStep >= this.availableStep) {
              this.pause();
              return;
            }
            scheduleNext();
          } catch (error) {
            this._fail(error);
          }
        }, Math.max(80, 700 / this.speed));
      };
      this.onStatus({ state: 'PLAYING', runId: this.runId });
      scheduleNext();
    }

    pause({ notify = true } = {}) {
      if (this.timer) clearInterval(this.timer);
      this.timer = null;
      if (notify && this.runId && this.ready) {
        this.onStatus({ state: 'PAUSED', runId: this.runId, availableStep: this.availableStep, currentStep: this.currentStep });
      }
    }

    async stepBy(delta) {
      const base = this.pendingStep ?? this.currentStep;
      return this.seek(Math.max(1, Math.min(this.availableStep, base + Number(delta || 0))));
    }

    setSpeed(value) {
      const next = Number(value);
      if (!Number.isFinite(next) || next <= 0 || next > 16) throw new Error('invalid replay speed');
      const playing = Boolean(this.timer);
      this.pause();
      this.speed = next;
      if (playing) this.play().catch(error => this._fail(error));
    }

    followAgent(agentKey) {
      this.followedAgentKey = agentKey || null;
      const object = this.agentObjects.get(this.followedAgentKey);
      if (this.scene && object?.sprite) this.scene.cameras.main.startFollow(object.sprite, true, 0.12, 0.12);
      else if (this.scene) this.scene.cameras.main.stopFollow();
    }

    toggleAgentFollow(agentKey) {
      const nextKey = this.selectedAgentKey === agentKey ? null : agentKey || null;
      this.followAgent(nextKey);
      this.selectAgent(nextKey);
      return nextKey;
    }

    selectAgent(agentKey) {
      this.selectedAgentKey = agentKey || null;
      this.agentObjects.forEach((value, key) => {
        const sprite = value?.sprite;
        if (!sprite) return;
        if (key === this.selectedAgentKey) {
          if (typeof sprite.setTint === 'function') sprite.setTint(0xffd166);
          else if (typeof sprite.setStrokeStyle === 'function') sprite.setStrokeStyle(3, 0xffd166, 1);
          return;
        }
        if (typeof sprite.clearTint === 'function') sprite.clearTint();
        else if (typeof sprite.setStrokeStyle === 'function') sprite.setStrokeStyle(0, 0xffd166, 0);
      });
      const step = this._cachedStep(this.currentStep);
      const fact = step?.agents.find(item => item.agent_key === this.selectedAgentKey) || null;
      this.onAgent({ selectedAgentKey: this.selectedAgentKey, definition: this.agentDefinitions.get(this.selectedAgentKey) || null, fact, step });
    }

    setLayerVisibility(layer, visible) {
      this.layerVisibility[layer] = Boolean(visible);
      if (this.mapLayers.has(layer)) this.mapLayers.get(layer).setVisible(Boolean(visible));
      this.agentObjects.forEach(object => {
        if (layer === 'agentNames' && object.label) object.label.setVisible(Boolean(visible));
        if (layer === 'actionBubbles' && object.bubble) object.bubble.setVisible(Boolean(visible));
        if (layer === 'trails' && object.trail) object.trail.setVisible(Boolean(visible));
      });
      this.onStep({ step: this._cachedStep(this.currentStep), layers: { ...this.layerVisibility } });
    }

    async refreshAvailable() {
      if (!this.runId) return;
      const runId = this.runId;
      const generation = this.generation;
      const manifest = await this._json(`/api/v1/runs/${encodeURIComponent(runId)}/replay/manifest`, generation);
      if (!this._owns(runId, generation)) return;
      const previousAvailableStep = this.availableStep;
      if (manifest.available_step < this.availableStep) {
        this.windows.clear();
        this.worldStateBefore.clear();
        this.currentStep = Math.min(this.currentStep, manifest.available_step);
      }
      // A RUNNING replay grows inside its last (usually incomplete) cached
      // window.  Drop that tail so a later seek cannot reuse the snapshot that
      // ended at the old available step (for example 1-63 after Step 72 exists).
      if (
        manifest.available_step > previousAvailableStep
        && previousAvailableStep > 0
        && previousAvailableStep % this.windowSize !== 0
      ) {
        const tailFrom = Math.floor((previousAvailableStep - 1) / this.windowSize) * this.windowSize + 1;
        this.windows.delete(tailFrom);
        this.worldStateBefore.delete(tailFrom);
      }
      if (manifest.available_step !== this.availableStep) {
        this.availableStep = manifest.available_step;
        this.manifest = manifest;
        this.onStatus({ state: 'AVAILABLE_STEP', runId, availableStep: this.availableStep, partial: manifest.partial });
      }
    }

    async seek(stepNo) {
      if (!this.runId || this.availableStep < 1) return null;
      const target = Math.max(1, Math.min(this.availableStep, Number(stepNo)));
      const request = ++this.seekRequest;
      this.pendingStep = target;
      const runId = this.runId;
      const generation = this.generation;
      const step = await this._ensureStep(target);
      if (request !== this.seekRequest || !this._owns(runId, generation)) return null;
      this.pendingStep = null;
      if (!step || step.step_no !== target) return null;
      this.currentStep = target;
      this._renderStep(step);
      return step;
    }

    async _ensureStep(stepNo) {
      const from = Math.floor((stepNo - 1) / this.windowSize) * this.windowSize + 1;
      let window = this.windows.get(from);
      // Cached tail pages may have been read while the Run was still growing.
      // If the requested step is now advertised but absent, refetch the page
      // instead of leaving the scene on the last cached frame.
      if (!window || !window.some(item => item.step_no === stepNo)) {
        const runId = this.runId;
        const generation = this.generation;
        const page = await this._json(
          `/api/v1/runs/${encodeURIComponent(runId)}/replay/steps?from_step=${from}&limit=${this.windowSize}`,
          generation,
        );
        if (!this._owns(runId, generation)) return null;
        if (page.run_id !== runId) throw new Error('stale replay window ownership');
        this.availableStep = page.available_step;
        this.resultVersion = page.result_version;
        window = page.steps;
        this.windows.set(from, window);
        this.worldStateBefore.set(from, page.world_state_before || {});
        while (this.windows.size > 5) {
          const oldest = this.windows.keys().next().value;
          this.windows.delete(oldest);
          this.worldStateBefore.delete(oldest);
        }
      }
      return window.find(item => item.step_no === stepNo) || null;
    }

    _cachedStep(stepNo) {
      const from = Math.floor((Math.max(1, stepNo) - 1) / this.windowSize) * this.windowSize + 1;
      return this.windows.get(from)?.find(item => item.step_no === stepNo) || null;
    }

    _renderStep(step) {
      if (!this.scene) return;
      this._renderWorldState(step.step_no);
      step.agents.forEach(fact => {
        const object = this.agentObjects.get(fact.agent_key);
        if (!object) return;
        const motion = fact.decision_context?.motion;
        const coord = motion && Number.isFinite(Number(motion.x_m)) && Number.isFinite(Number(motion.y_m))
          ? [Number(motion.x_m), Number(motion.y_m)]
          : fact.coord;
        const [targetX, targetY] = this._worldPoint(coord);
        if (object.sprite) {
          this.scene.tweens.killTweensOf(object.sprite);
          this.scene.tweens.add({ targets: object.sprite, x: targetX, y: targetY, duration: 140 / this.speed });
        }
        if (object.glyph) {
          this.scene.tweens.killTweensOf(object.glyph);
          this.scene.tweens.add({ targets: object.glyph, x: targetX, y: targetY, duration: 140 / this.speed });
        }
        if (object.label) {
          object.label.setPosition(
            targetX + AGENT_NAME_OFFSET.x,
            targetY + AGENT_NAME_OFFSET.y,
          );
        }
        if (object.bubble) {
          const actionDescription = fact.action?.description || '';
          const actionEmoji = fact.action?.emoji || '';
          object.bubble.setText(
            actionDescription
              ? `${actionEmoji ? `${actionEmoji} ` : ''}${actionDescription}`
              : actionEmoji,
          );
          object.bubble.setPosition(
            targetX + ACTION_BUBBLE_OFFSET.x,
            targetY + ACTION_BUBBLE_OFFSET.y,
          );
        }
        if (object.trail) {
          object.trail.clear();
          object.trail.lineStyle(2, 0x2c7f74, 0.65);
          object.trail.beginPath();
          fact.path.forEach((pathCoord, index) => {
            const [px, py] = this._worldPoint(pathCoord);
            if (index === 0) object.trail.moveTo(px, py); else object.trail.lineTo(px, py);
          });
          object.trail.strokePath();
        }
      });
      if (this.followedAgentKey) this.followAgent(this.followedAgentKey);
      if (this.selectedAgentKey) this.selectAgent(this.selectedAgentKey);
      this.onStep({
        step,
        attempt_boundary: step.attempt_boundary,
        checkpoint: step.checkpoint,
        conversations: step.conversations,
        domain_events: step.domain_events,
        memory_deltas: step.memory_deltas,
        schedule_revisions: step.schedule_revisions,
        effects: step.effects || [],
        availableStep: this.availableStep,
      });
    }

    _renderWorldState(stepNo) {
      const from = Math.floor((Math.max(1, stepNo) - 1) / this.windowSize) * this.windowSize + 1;
      const state = { ...(this.worldStateBefore.get(from) || {}) };
      const window = this.windows.get(from) || [];
      window.forEach(step => {
        if (step.step_no > stepNo) return;
        (step.domain_events || []).forEach(event => {
          if (event.event_type !== 'GAME_OBJECT_STATE_CHANGED') return;
          const payload = event.payload?.structured_payload || {};
          if (payload.object_key && payload.after && typeof payload.after === 'object') {
            state[payload.object_key] = { ...payload.after };
          }
        });
      });
      this.worldObjects.forEach((object, objectKey) => {
        object.state = { ...(object.initialState || {}), ...(state[objectKey] || {}) };
        if (object.glyph) object.glyph.setText(this._appearanceGlyph(object.appearance, object.state));
      });
    }

    async _createGame(manifest, generation) {
      const PhaserRuntime = typeof Phaser !== 'undefined' ? Phaser : null;
      if (!PhaserRuntime?.Game) throw new Error('package-local Phaser runtime is unavailable');
      const player = this;
      const assets = manifest.world.render_asset;
      if (!assets || assets.status !== 'READY') throw new Error(assets?.error_code || 'WORLD_RENDER_ASSET_UNRESOLVED');
      if (assets.renderer === 'SPATIAL_GRID') {
        await this._createSpatialGridGame(manifest, generation);
        return;
      }
      const tileRoot = assets.base_url;
      const host = player.canvas?.parentElement;
      if (!host) throw new Error('REPLAY_CANVAS_HOST_MISSING');
      await new Promise((resolve, reject) => {
        const config = {
          // The in-app browser is a custom environment where Phaser refuses
          // AUTO renderer probing.  Canvas is explicit, local and sufficient
          // for the packaged tilemap/sprite pipeline.
          type: PhaserRuntime.CANVAS,
          parent: host,
          canvas: player.canvas,
          resolution: DISPLAY_RENDER_RESOLUTION,
          backgroundColor: '#a9c991',
          render: { antialias: false, pixelArt: true },
          scale: { mode: PhaserRuntime.Scale.RESIZE },
          scene: {
            preload() {
              this.load.tilemapTiledJSON('ga-world', assets.tilemap_url);
              TILESET_NAMES.forEach(name => {
                const override = assets.texture_overrides?.[name];
                const textureUrl = typeof override === 'string' ? override : override?.url;
                this.load.image(name, textureUrl || `${tileRoot}/${name}.png`);
              });
              this.load.image('blocks', `${tileRoot}/blocks_1.png`);
              this.load.image('Room_Builder_32x32', `${tileRoot}/Room_Builder_32x32.png`);
              manifest.agents.forEach(agent => {
                if (agent.sprite_asset.status === 'READY') {
                  this.load.atlas(`agent:${agent.agent_key}`, agent.sprite_asset.texture_url, agent.sprite_asset.atlas_url);
                }
              });
            },
            create() {
              if (!player._owns(manifest.run_id, generation)) return;
              player.scene = this;
              const map = this.make.tilemap({ key: 'ga-world' });
              const tileSets = TILESET_NAMES.map(name => map.addTilesetImage(name, name)).filter(Boolean);
              const walls = map.addTilesetImage('Room_Builder_32x32', 'Room_Builder_32x32');
              const blocks = map.addTilesetImage('blocks', 'blocks');
              const all = [...tileSets, walls].filter(Boolean);
              LAYERS.forEach((name, index) => {
                try {
                  const layer = map.createLayer(name, name === 'Collisions' ? blocks : all, 0, 0);
                  if (layer) {
                    layer.setDepth(name.startsWith('Foreground') ? 20 : index);
                    if (name === 'Collisions') layer.setVisible(false);
                    player.mapLayers.set(name, layer);
                  }
                } catch (error) {
                  player.onError({ code: 'REPLAY_LAYER_UNAVAILABLE', layer: name, message: String(error) });
                }
              });
              this.cameras.main.setBounds(0, 0, map.widthInPixels, map.heightInPixels);
              this.cameras.main.setZoom(INITIAL_CAMERA_ZOOM);
              let dragX = 0; let dragY = 0;
              this.input.on('pointerdown', pointer => { dragX = pointer.x; dragY = pointer.y; });
              this.input.on('pointermove', pointer => {
                if (!pointer.isDown || player.followedAgentKey) return;
                this.cameras.main.scrollX -= (pointer.x - dragX) / this.cameras.main.zoom;
                this.cameras.main.scrollY -= (pointer.y - dragY) / this.cameras.main.zoom;
                dragX = pointer.x; dragY = pointer.y;
              });
              this.input.on('wheel', (_pointer, _objects, _dx, dy) => {
                this.cameras.main.setZoom(PhaserRuntime.Math.Clamp(this.cameras.main.zoom - dy * 0.0005, 0.25, 2));
              });
              manifest.agents.forEach(agent => player._createAgent(this, agent));
              player._observeHost(host, this.scale);
              resolve();
            },
          },
        };
        try {
          player.game = new PhaserRuntime.Game(config);
          player.game.events.once('destroy', () => reject(new Error('replay game destroyed before ready')));
        } catch (error) { reject(error); }
      });
    }

    async _createSpatialGridGame(manifest, generation) {
      const PhaserRuntime = typeof Phaser !== 'undefined' ? Phaser : null;
      if (!PhaserRuntime?.Game) throw new Error('package-local Phaser runtime is unavailable');
      const player = this;
      const assets = manifest.world.render_asset;
      const definition = manifest.world.definition || {};
      const size = Array.isArray(definition.size) ? definition.size : [];
      const width = Number(definition.width || size[1] || 48);
      const height = Number(definition.height || size[0] || 48);
      const scale = Number(assets.pixels_per_meter || 16);
      const tiles = definition.tiles || [];
      const editorV2 = definition.editor_v2 && typeof definition.editor_v2 === 'object'
        ? definition.editor_v2
        : {};
      const materialSources = Array.isArray(editorV2.material_sources)
        ? editorV2.material_sources
        : [];
      const materialSlices = Array.isArray(editorV2.material_slices)
        ? editorV2.material_slices
        : [];
      const visualLayers = Array.isArray(editorV2.visual_layers)
        ? editorV2.visual_layers
        : [];
      const hierarchyNodes = Array.isArray(editorV2.hierarchy_nodes)
        ? editorV2.hierarchy_nodes
        : [];
      const sourceById = new Map(materialSources.map(item => [String(item.id), item]));
      const sliceById = new Map(materialSlices.map(item => [String(item.id), item]));
      const sliceByGid = new Map(materialSlices
        .filter(item => Number(item.indexed_gid) > 0)
        .map(item => [Number(item.indexed_gid), item]));
      const hierarchyById = new Map(hierarchyNodes.map(item => [String(item.id), item]));
      const sourceTextureKey = sourceId => `world-source:${sourceId}`;
      const sourceTextureUrl = source => source?.asset_id
        ? `/api/v1/assets/${encodeURIComponent(source.asset_id)}/content`
        : (source?.bundled_path
          ? `/generative_agents/frontend/static/assets/village/${source.bundled_path}`
          : '');
      const host = player.canvas?.parentElement;
      if (!host) throw new Error('REPLAY_CANVAS_HOST_MISSING');
      await new Promise((resolve, reject) => {
        const config = {
          type: PhaserRuntime.CANVAS,
          parent: host,
          canvas: player.canvas,
          resolution: DISPLAY_RENDER_RESOLUTION,
          backgroundColor: '#eef3ee',
          render: { antialias: true, pixelArt: false },
          scale: { mode: PhaserRuntime.Scale.RESIZE },
          scene: {
            preload() {
              materialSources.forEach(source => {
                const sourceUrl = sourceTextureUrl(source);
                if (!sourceUrl) return;
                this.load.image(
                  sourceTextureKey(String(source.id)),
                  sourceUrl,
                );
              });
              manifest.agents.forEach(agent => {
                if (agent.sprite_asset.status === 'READY') {
                  this.load.atlas(`agent:${agent.agent_key}`, agent.sprite_asset.texture_url, agent.sprite_asset.atlas_url);
                }
              });
            },
            create() {
              if (!player._owns(manifest.run_id, generation)) return;
              player.scene = this;
              const graphics = this.add.graphics().setDepth(0);
              const drawTile = (paletteKey, x, y) => {
                const appearance = assets.palette?.[paletteKey] || {};
                const rawColor = appearance.color || '#d9e2df';
                const color = PhaserRuntime.Display.Color.HexStringToColor(rawColor).color;
                graphics.fillStyle(color, 1);
                graphics.fillRect(x * scale, y * scale, scale, scale);
              };
              const drawMaterialSlice = (sliceId, x, y, {
                widthInTiles = 1,
                heightInTiles = 1,
                rotationDegrees = null,
                depth = 1,
              } = {}) => {
                const slice = sliceById.get(String(sliceId || ''));
                const source = slice ? sourceById.get(String(slice.source_id || '')) : null;
                const textureKey = source ? sourceTextureKey(String(source.id)) : '';
                const rect = slice?.pixel_rect;
                if (!slice || !sourceTextureUrl(source) || !rect || !this.textures.exists(textureKey)) return false;
                const frameKey = `slice-frame:${slice.id}`;
                const texture = this.textures.get(textureKey);
                if (!texture.has(frameKey)) {
                  texture.add(
                    frameKey,
                    0,
                    Number(rect.x || 0),
                    Number(rect.y || 0),
                    Number(rect.width || source.width_px || scale),
                    Number(rect.height || source.height_px || scale),
                  );
                }
                const image = this.add.image(
                  (Number(x) + Number(widthInTiles) / 2) * scale,
                  (Number(y) + Number(heightInTiles) / 2) * scale,
                  textureKey,
                  frameKey,
                );
                image.setDisplaySize(
                  Math.max(1, Number(widthInTiles)) * scale,
                  Math.max(1, Number(heightInTiles)) * scale,
                );
                image.setAngle(Number(rotationDegrees ?? slice.rotation_degrees ?? 0));
                image.setDepth(depth);
                return true;
              };
              const compositeCanvas = document.createElement('canvas');
              compositeCanvas.width = Math.max(1, width * scale);
              compositeCanvas.height = Math.max(1, height * scale);
              const composite = compositeCanvas.getContext('2d');
              composite.imageSmoothingEnabled = false;
              const drawCompositeSlice = (slice, rawGid, dx, dy, drawWidth, drawHeight, rotationOverride = null) => {
                const source = slice ? sourceById.get(String(slice.source_id || '')) : null;
                const textureKey = source ? sourceTextureKey(String(source.id)) : '';
                const rect = slice?.pixel_rect;
                if (!slice || !sourceTextureUrl(source) || !rect || !this.textures.exists(textureKey)) return false;
                const image = this.textures.get(textureKey).getSourceImage();
                if (!image) return false;
                const raw = Number(rawGid || 0) >>> 0;
                const horizontal = Boolean(raw & 0x80000000);
                const vertical = Boolean(raw & 0x40000000);
                const diagonal = Boolean(raw & 0x20000000);
                const requestedRotation = Number(rotationOverride);
                const sliceRotation = Number(slice.rotation_degrees || 0);
                const rotation = [0, 90, 180, 270].includes(requestedRotation)
                  ? requestedRotation
                  : ([0, 90, 180, 270].includes(sliceRotation) ? sliceRotation : 0);
                const sourceWidth = rotation % 180 === 90 ? drawHeight : drawWidth;
                const sourceHeight = rotation % 180 === 90 ? drawWidth : drawHeight;
                composite.save();
                composite.translate(dx + drawWidth / 2, dy + drawHeight / 2);
                if (rotation) composite.rotate(rotation * Math.PI / 180);
                if (diagonal) composite.transform(0, 1, 1, 0, 0, 0);
                composite.scale(horizontal ? -1 : 1, vertical ? -1 : 1);
                composite.drawImage(
                  image,
                  Number(rect.x || 0),
                  Number(rect.y || 0),
                  Number(rect.width || scale),
                  Number(rect.height || scale),
                  -sourceWidth / 2,
                  -sourceHeight / 2,
                  sourceWidth,
                  sourceHeight,
                );
                composite.restore();
                return true;
              };
              let detailedWorldDrawn = false;
              visualLayers.filter(layer => layer?.visible !== false).forEach(layer => {
                composite.globalAlpha = Number.isFinite(Number(layer.opacity)) ? Number(layer.opacity) : 1;
                (layer.raw_gids || []).forEach((raw, index) => {
                  const gid = Number(raw) & 0x1fffffff;
                  const slice = sliceByGid.get(gid);
                  if (!slice) return;
                  detailedWorldDrawn = drawCompositeSlice(
                    slice,
                    raw,
                    (index % width) * scale,
                    Math.floor(index / width) * scale,
                    scale,
                    scale,
                  ) || detailedWorldDrawn;
                });
              });
              composite.globalAlpha = 1;
              const overrideIndexes = new Set([
                ...Object.keys(editorV2.tile_overrides || {}),
                ...Object.keys(editorV2.tile_override_layers || {}),
              ]);
              overrideIndexes.forEach(indexText => {
                const index = Number(indexText);
                const stacked = editorV2.tile_override_layers?.[indexText];
                const entries = Array.isArray(stacked) && stacked.length
                  ? stacked
                  : [{
                    slice_id: editorV2.tile_overrides?.[indexText],
                    part: editorV2.tile_override_parts?.[indexText] || null,
                  }];
                entries.filter(entry => entry?.slice_id).forEach(entry => {
                  const slice = sliceById.get(String(entry.slice_id));
                  const part = entry.part;
                  const dx = (index % width) * scale;
                  const dy = Math.floor(index / width) * scale;
                  if (!part) {
                    detailedWorldDrawn = drawCompositeSlice(slice, 0, dx, dy, scale, scale) || detailedWorldDrawn;
                    return;
                  }
                  const columns = Math.max(1, Number(part.columns) || 1);
                  const rows = Math.max(1, Number(part.rows) || 1);
                  const column = Math.max(0, Number(part.column) || 0);
                  const row = Math.max(0, Number(part.row) || 0);
                  composite.save();
                  composite.beginPath();
                  composite.rect(dx, dy, scale, scale);
                  composite.clip();
                  detailedWorldDrawn = drawCompositeSlice(
                    slice,
                    0,
                    dx - column * scale,
                    dy - row * scale,
                    columns * scale,
                    rows * scale,
                    part.rotation_degrees,
                  ) || detailedWorldDrawn;
                  composite.restore();
                });
              });
              // Hierarchy-bound materials are the semantic map layers.  The
              // editor renders them parent-first, and replay must preserve the
              // same frozen World -> Sector -> Arena -> Game Object ordering.
              // Game Objects remain live Phaser objects below, so the static
              // composite owns only L1-L3 and the object pass owns L4.
              GAReplayPlayer.orderedSpatialHierarchyNodes(hierarchyNodes, 3).forEach(node => {
                const bounds = node.bounds || {};
                const slice = sliceById.get(String(node.material_slice_id || ''));
                detailedWorldDrawn = drawCompositeSlice(
                  slice,
                  0,
                  Number(bounds.x || 0) * scale,
                  Number(bounds.y || 0) * scale,
                  Math.max(1, Number(bounds.width || 1)) * scale,
                  Math.max(1, Number(bounds.height || 1)) * scale,
                ) || detailedWorldDrawn;
              });
              if (detailedWorldDrawn) {
                this.textures.addCanvas('world-composite', compositeCanvas);
                this.add.image(0, 0, 'world-composite').setOrigin(0).setDepth(1);
              } else if (Array.isArray(tiles[0])) {
                tiles.forEach((row, y) => row.forEach((paletteKey, x) => {
                  drawTile(paletteKey, x, y);
                }));
              } else {
                tiles.forEach(tile => {
                  const coord = Array.isArray(tile?.coord) ? tile.coord : [];
                  const x = Number(coord[0] || 0);
                  const y = Number(coord[1] || 0);
                  const sliceId = tile?.visual_slice_id || tile?.tile;
                  const rotationDegrees = tile?.visual_slice_part?.rotation_degrees;
                  if (!drawMaterialSlice(sliceId, x, y, { rotationDegrees })) {
                    drawTile(tile?.tile || tile?.palette_key || 'ground', x, y);
                  }
                });
              }
              const gridGraphics = this.add.graphics().setDepth(2);
              gridGraphics.lineStyle(1, 0xffffff, 0.08);
              for (let x = 0; x <= width; x += 1) gridGraphics.lineBetween(x * scale, 0, x * scale, height * scale);
              for (let y = 0; y <= height; y += 1) gridGraphics.lineBetween(0, y * scale, width * scale, y * scale);

              [...(assets.objects || [])].sort((left, right) => {
                const leftNode = hierarchyById.get(String(left.instance_key || ''));
                const rightNode = hierarchyById.get(String(right.instance_key || ''));
                return Number(leftNode?.sort_order || 0) - Number(rightNode?.sort_order || 0)
                  || String(left.instance_key || '').localeCompare(String(right.instance_key || ''));
              }).forEach(item => {
                const hierarchyNode = hierarchyById.get(String(item.instance_key || ''));
                const bounds = hierarchyNode?.bounds || {};
                const materialDrawn = hierarchyNode?.material_slice_id && drawMaterialSlice(
                  hierarchyNode.material_slice_id,
                  Number(bounds.x ?? item.x_m ?? item.x ?? 0),
                  Number(bounds.y ?? item.y_m ?? item.y ?? 0),
                  {
                    widthInTiles: Number(bounds.width || 1),
                    heightInTiles: Number(bounds.height || 1),
                    depth: 10,
                  },
                );
                const [px, py] = player._worldPoint([
                  item.x_m ?? item.x,
                  item.y_m ?? item.y,
                ]);
                const glyph = materialDrawn ? null : this.add.text(
                    px,
                    py,
                    player._appearanceGlyph(item.appearance, item.state),
                    { fontFamily: REPLAY_FONT_FAMILY, fontSize: `${Math.max(14, scale)}px` },
                  ).setOrigin(0.5).setDepth(10);
                if (typeof glyph?.setResolution === 'function') glyph.setResolution(TEXT_RENDER_RESOLUTION);
                player.worldObjects.set(item.instance_key, {
                  glyph,
                  appearance: item.appearance || {},
                  initialState: { ...(item.state || {}) },
                  state: { ...(item.state || {}) },
                });
              });

              this.cameras.main.setBounds(0, 0, width * scale, height * scale);
              const fitZoom = Math.min(
                (host.clientWidth || width * scale) / Math.max(1, width * scale),
                (host.clientHeight || height * scale) / Math.max(1, height * scale),
              ) * 0.9;
              this.cameras.main.setZoom(Math.min(1.8, Math.max(0.35, fitZoom)));
              this.cameras.main.centerOn(width * scale / 2, height * scale / 2);
              let dragX = 0; let dragY = 0;
              this.input.on('pointerdown', pointer => { dragX = pointer.x; dragY = pointer.y; });
              this.input.on('pointermove', pointer => {
                if (!pointer.isDown || player.followedAgentKey) return;
                this.cameras.main.scrollX -= (pointer.x - dragX) / this.cameras.main.zoom;
                this.cameras.main.scrollY -= (pointer.y - dragY) / this.cameras.main.zoom;
                dragX = pointer.x; dragY = pointer.y;
              });
              this.input.on('wheel', (_pointer, _objects, _dx, dy) => {
                this.cameras.main.setZoom(PhaserRuntime.Math.Clamp(this.cameras.main.zoom - dy * 0.0005, 0.25, 3));
              });
              manifest.agents.forEach(agent => player._createAgent(this, agent));
              player._observeHost(host, this.scale);
              resolve();
            },
          },
        };
        try {
          player.game = new PhaserRuntime.Game(config);
          player.game.events.once('destroy', () => reject(new Error('replay game destroyed before ready')));
        } catch (error) { reject(error); }
      });
    }

    _worldPoint(coord) {
      const spatial = this.manifest?.world?.render_asset?.renderer === 'SPATIAL_GRID';
      const scale = spatial ? Number(this.manifest.world.render_asset.pixels_per_meter || 16) : 32;
      return [Number(coord?.[0] || 0) * scale + scale / 2, Number(coord?.[1] || 0) * scale + scale / 2];
    }

    _appearanceGlyph(appearance = {}, state = {}) {
      const value = String(state.state || state.signal || '').toUpperCase();
      if (value.includes('RED')) return '🔴';
      if (value.includes('YELLOW') || value.includes('AMBER')) return '🟡';
      if (value.includes('GREEN')) return '🟢';
      const variants = appearance.state_variants || {};
      const stableState = String(state.state || '').toLowerCase().replaceAll('_', '-');
      const variant = variants[state.state] || variants[stableState] || {};
      return variant.emoji || appearance.emoji || '◆';
    }

    _observeHost(host, scaleManager) {
      if (typeof ResizeObserver !== 'function') return;
      if (this.resizeObserver) this.resizeObserver.disconnect();
      this.resizeObserver = new ResizeObserver(entries => {
        const rect = entries[0]?.contentRect;
        const width = Math.floor(rect?.width || host.clientWidth || 0);
        const height = Math.floor(rect?.height || host.clientHeight || 0);
        if (width > 0 && height > 0 && typeof scaleManager?.resize === 'function') {
          scaleManager.resize(width, height);
        }
      });
      this.resizeObserver.observe(host);
    }

    _createAgent(scene, definition) {
      const [x, y] = definition.initial_coord;
      const [px, py] = this._worldPoint([x, y]);
      let sprite;
      let glyph = null;
      if (definition.sprite_asset.status === 'READY') {
        sprite = scene.add.sprite(px, py, `agent:${definition.agent_key}`, 'down-walk.000').setDepth(15).setInteractive();
      } else {
        const roleGlyph = definition.role === 'DRIVER' ? '🚗' : definition.role === 'PEDESTRIAN' ? '🚶' : '!';
        const size = 26;
        sprite = scene.add.circle(px, py, size / 2, definition.role === 'DRIVER' ? 0x315d8a : 0xb64b4b).setDepth(15).setInteractive();
        glyph = scene.add.text(px, py, roleGlyph, { fontFamily: REPLAY_FONT_FAMILY, fontSize: '17px' }).setOrigin(0.5).setDepth(16);
        if (typeof glyph.setResolution === 'function') glyph.setResolution(TEXT_RENDER_RESOLUTION);
        if (!definition.role) {
          this.onError({
            code: definition.sprite_asset.error_code || 'AGENT_SPRITE_MAPPING_MISSING',
            agent_key: definition.agent_key,
          });
        }
      }
      sprite.on('pointerdown', () => this.toggleAgentFollow(definition.agent_key));
      const label = scene.add.text(
        px + AGENT_NAME_OFFSET.x,
        py + AGENT_NAME_OFFSET.y,
        definition.display_name,
        {
        color: '#17352f', backgroundColor: '#fffffff2', fontFamily: REPLAY_FONT_FAMILY, fontSize: '11px', padding: { x: 3, y: 1 },
        },
      ).setOrigin(0.5).setDepth(30).setVisible(this.layerVisibility.agentNames);
      if (typeof label.setResolution === 'function') label.setResolution(TEXT_RENDER_RESOLUTION);
      const bubble = scene.add.text(
        px + ACTION_BUBBLE_OFFSET.x,
        py + ACTION_BUBBLE_OFFSET.y,
        '',
        {
          color: '#17352f', backgroundColor: '#fffdf2f2', fontFamily: REPLAY_FONT_FAMILY, fontSize: '11px', padding: { x: 3, y: 1 },
        },
      ).setDepth(31).setVisible(this.layerVisibility.actionBubbles);
      if (typeof bubble.setResolution === 'function') bubble.setResolution(TEXT_RENDER_RESOLUTION);
      const trail = scene.add.graphics().setDepth(14).setVisible(this.layerVisibility.trails);
      this.agentObjects.set(definition.agent_key, { sprite, glyph, label, bubble, trail });
    }

    async _json(url, generation) {
      const response = await this.fetchImpl(url, { signal: this.abortController?.signal, headers: { Accept: 'application/json' } });
      if (generation !== this.generation) throw new DOMException('stale replay request', 'AbortError');
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error?.code || `HTTP_${response.status}`);
      }
      return response.json();
    }

    _validateManifest(manifest, runId) {
      if (manifest.schema_version !== 2 || manifest.run_id !== runId || !Array.isArray(manifest.agents)) {
        throw new Error('INVALID_REPLAY_V2_MANIFEST');
      }
    }

    _owns(runId, generation) {
      return this.runId === runId && this.generation === generation && !this.abortController?.signal.aborted;
    }

    _fail(error) {
      if (error?.name === 'AbortError') return;
      this.pause();
      this.onError({ code: error?.message || 'REPLAY_PLAYER_ERROR' });
    }
  }

  return { GAReplayPlayer };
});
