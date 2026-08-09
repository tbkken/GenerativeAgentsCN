/* Formal Replay Bundle V2 player. Phaser is supplied by the package-local vendor asset. */
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

  class GAReplayPlayer {
    static resolveAgentSelection(selectedKey, selectedRevisionId, runRevisionId, agents) {
      if (!selectedKey || !selectedRevisionId || selectedRevisionId !== runRevisionId) return null;
      return agents.some(agent => agent.agent_key === selectedKey) ? selectedKey : null;
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
      this.game = null;
      this.scene = null;
      this.abortController = null;
      this.generation = 0;
      this.windows = new Map();
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
      this.resizeObserver = null;
    }

    async loadRun(runId, { signal } = {}) {
      this.destroy();
      this.runId = runId;
      const generation = ++this.generation;
      this.abortController = new AbortController();
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
      if (this.availableStep > 0) await this.seek(this.availableStep);
      this.onStatus({ state: 'READY', runId, availableStep: this.availableStep, partial: manifest.partial });
    }

    destroy() {
      this.pause();
      if (this.resizeObserver) this.resizeObserver.disconnect();
      this.resizeObserver = null;
      if (this.abortController) this.abortController.abort();
      this.abortController = null;
      this.windows.clear();
      this.agentObjects.clear();
      this.mapLayers.clear();
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

    play() {
      if (this.timer || !this.runId) return;
      this.timer = setInterval(() => {
        if (this.currentStep >= this.availableStep) {
          this.pause();
          return;
        }
        this.stepBy(1).catch(error => this._fail(error));
      }, Math.max(80, 700 / this.speed));
      this.onStatus({ state: 'PLAYING', runId: this.runId });
    }

    pause() {
      if (this.timer) clearInterval(this.timer);
      this.timer = null;
      if (this.runId) this.onStatus({ state: 'PAUSED', runId: this.runId });
    }

    async stepBy(delta) {
      return this.seek(Math.max(1, Math.min(this.availableStep, this.currentStep + Number(delta || 0))));
    }

    setSpeed(value) {
      const next = Number(value);
      if (!Number.isFinite(next) || next <= 0 || next > 16) throw new Error('invalid replay speed');
      const playing = Boolean(this.timer);
      this.pause();
      this.speed = next;
      if (playing) this.play();
    }

    followAgent(agentKey) {
      this.followedAgentKey = agentKey || null;
      const object = this.agentObjects.get(this.followedAgentKey);
      if (this.scene && object?.sprite) this.scene.cameras.main.startFollow(object.sprite, true, 0.12, 0.12);
      else if (this.scene) this.scene.cameras.main.stopFollow();
    }

    selectAgent(agentKey) {
      this.selectedAgentKey = agentKey || null;
      const object = this.agentObjects.get(this.selectedAgentKey);
      if (object?.sprite) object.sprite.setTint(0xffd166);
      this.agentObjects.forEach((value, key) => {
        if (key !== this.selectedAgentKey && value.sprite?.clearTint) value.sprite.clearTint();
      });
      const step = this._cachedStep(this.currentStep);
      const fact = step?.agents.find(item => item.agent_key === this.selectedAgentKey) || null;
      this.onAgent({ definition: this.agentDefinitions.get(this.selectedAgentKey) || null, fact, step });
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
      if (manifest.available_step < this.availableStep) {
        this.windows.clear();
        this.currentStep = Math.min(this.currentStep, manifest.available_step);
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
      const step = await this._ensureStep(target);
      if (!step || step.step_no !== target) return null;
      this.currentStep = target;
      this._renderStep(step);
      return step;
    }

    async _ensureStep(stepNo) {
      const from = Math.floor((stepNo - 1) / this.windowSize) * this.windowSize + 1;
      if (!this.windows.has(from)) {
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
        this.windows.set(from, page.steps);
        while (this.windows.size > 5) this.windows.delete(this.windows.keys().next().value);
      }
      return this.windows.get(from).find(item => item.step_no === stepNo) || null;
    }

    _cachedStep(stepNo) {
      const from = Math.floor((Math.max(1, stepNo) - 1) / this.windowSize) * this.windowSize + 1;
      return this.windows.get(from)?.find(item => item.step_no === stepNo) || null;
    }

    _renderStep(step) {
      if (!this.scene) return;
      step.agents.forEach(fact => {
        const object = this.agentObjects.get(fact.agent_key);
        if (!object) return;
        const [x, y] = fact.coord;
        const targetX = x * 32 + 16;
        const targetY = y * 32 + 16;
        if (object.sprite) {
          this.scene.tweens.killTweensOf(object.sprite);
          this.scene.tweens.add({ targets: object.sprite, x: targetX, y: targetY, duration: 140 / this.speed });
        }
        if (object.label) object.label.setPosition(targetX, targetY - 28);
        if (object.bubble) {
          object.bubble.setText(fact.action?.emoji || fact.action?.description || '');
          object.bubble.setPosition(targetX + 15, targetY - 24);
        }
        if (object.trail) {
          object.trail.clear();
          object.trail.lineStyle(2, 0x2c7f74, 0.65);
          object.trail.beginPath();
          fact.path.forEach((coord, index) => {
            const px = coord[0] * 32 + 16; const py = coord[1] * 32 + 16;
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
        availableStep: this.availableStep,
      });
    }

    async _createGame(manifest, generation) {
      const PhaserRuntime = typeof Phaser !== 'undefined' ? Phaser : null;
      if (!PhaserRuntime?.Game) throw new Error('package-local Phaser runtime is unavailable');
      const player = this;
      const assets = manifest.world.render_asset;
      if (!assets || assets.status !== 'READY') throw new Error(assets?.error_code || 'WORLD_RENDER_ASSET_UNRESOLVED');
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
              this.cameras.main.setZoom(0.65);
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
      const px = x * 32 + 16; const py = y * 32 + 16;
      let sprite;
      if (definition.sprite_asset.status === 'READY') {
        sprite = scene.add.sprite(px, py, `agent:${definition.agent_key}`, 'down-walk.000').setDepth(15).setInteractive();
      } else {
        sprite = scene.add.rectangle(px, py, 26, 30, 0xb64b4b).setDepth(15).setInteractive();
        scene.add.text(px, py, '!', { color: '#fff', fontSize: '16px', fontStyle: 'bold' }).setOrigin(0.5).setDepth(16);
        this.onError({
          code: definition.sprite_asset.error_code || 'AGENT_SPRITE_MAPPING_MISSING',
          agent_key: definition.agent_key,
        });
      }
      sprite.on('pointerdown', () => this.selectAgent(definition.agent_key));
      const label = scene.add.text(px, py - 28, definition.display_name, {
        color: '#17352f', backgroundColor: '#ffffffdd', fontSize: '12px', padding: { x: 4, y: 2 },
      }).setOrigin(0.5).setDepth(30).setVisible(this.layerVisibility.agentNames);
      const bubble = scene.add.text(px + 15, py - 24, '', {
        color: '#17352f', backgroundColor: '#fff7d6ee', fontSize: '12px', padding: { x: 3, y: 2 },
      }).setDepth(31).setVisible(this.layerVisibility.actionBubbles);
      const trail = scene.add.graphics().setDepth(14).setVisible(this.layerVisibility.trails);
      this.agentObjects.set(definition.agent_key, { sprite, label, bubble, trail });
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
