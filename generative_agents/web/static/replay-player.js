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
  const INITIAL_CAMERA_ZOOM = 0.7;
  const DISPLAY_RENDER_RESOLUTION = Math.min(2, Math.max(1, Number(globalThis.devicePixelRatio || 1)));
  const TEXT_RENDER_RESOLUTION = Math.max(2, DISPLAY_RENDER_RESOLUTION);
  const REPLAY_FONT_FAMILY = '"Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif';
  // Keep the two overlays visually independent: a long display name should
  // sit above-left of the sprite instead of competing with the action emoji.
  const AGENT_NAME_OFFSET = Object.freeze({ x: -18, y: -44 });
  const ACTION_BUBBLE_OFFSET = Object.freeze({ x: 38, y: -24 });

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
      this.ready = false;
      this.seekRequest = 0;
      this.pendingStep = null;
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
      this.capabilityObjects = new Map();
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
      this.agentObjects.clear();
      this.mapLayers.clear();
      this.capabilityObjects.clear();
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
        while (this.windows.size > 5) this.windows.delete(this.windows.keys().next().value);
      }
      return window.find(item => item.step_no === stepNo) || null;
    }

    _cachedStep(stepNo) {
      const from = Math.floor((Math.max(1, stepNo) - 1) / this.windowSize) * this.windowSize + 1;
      return this.windows.get(from)?.find(item => item.step_no === stepNo) || null;
    }

    _renderStep(step) {
      if (!this.scene) return;
      const snapshot = (step.domain_events || []).find(event => event.event_type === 'capability.snapshot')?.payload || null;
      const trajectories = snapshot?.trajectory_samples || [];
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
          object.bubble.setText(fact.action?.emoji || fact.action?.description || '');
          object.bubble.setPosition(
            targetX + ACTION_BUBBLE_OFFSET.x,
            targetY + ACTION_BUBBLE_OFFSET.y,
          );
        }
        if (object.trail) {
          object.trail.clear();
          object.trail.lineStyle(2, 0x2c7f74, 0.65);
          object.trail.beginPath();
          const definition = this.agentDefinitions.get(fact.agent_key);
          const entityRefs = new Set([
            definition?.actor_key ? `actor:${definition.actor_key}` : null,
            definition?.active_tool_instance_key ? `tool:${definition.active_tool_instance_key}` : null,
          ].filter(Boolean));
          const samples = trajectories
            .filter(item => entityRefs.has(item.entity_ref))
            .map(item => [item.x_m, item.y_m]);
          const path = samples.length ? samples : fact.path;
          path.forEach((pathCoord, index) => {
            const [px, py] = this._worldPoint(pathCoord);
            if (index === 0) object.trail.moveTo(px, py); else object.trail.lineTo(px, py);
          });
          object.trail.strokePath();
        }
      });
      if (snapshot?.placements) {
        Object.entries(snapshot.placements).forEach(([instanceKey, placement]) => {
          const object = this.capabilityObjects.get(instanceKey);
          if (!object?.glyph) return;
          const state = placement?.state || {};
          object.glyph.setText(this._appearanceGlyph(object.appearance, state));
        });
      }
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
              if (Array.isArray(tiles[0])) {
                tiles.forEach((row, y) => row.forEach((paletteKey, x) => {
                  drawTile(paletteKey, x, y);
                }));
              } else {
                tiles.forEach(tile => {
                  const coord = Array.isArray(tile?.coord) ? tile.coord : [];
                  drawTile(
                    tile?.tile || tile?.palette_key || 'ground',
                    Number(coord[0] || 0),
                    Number(coord[1] || 0),
                  );
                });
              }
              graphics.lineStyle(1, 0xffffff, 0.08);
              for (let x = 0; x <= width; x += 1) graphics.lineBetween(x * scale, 0, x * scale, height * scale);
              for (let y = 0; y <= height; y += 1) graphics.lineBetween(0, y * scale, width * scale, y * scale);

              (assets.objects || []).forEach(item => {
                const [px, py] = player._worldPoint([
                  item.x_m ?? item.x,
                  item.y_m ?? item.y,
                ]);
                const glyph = this.add.text(
                  px,
                  py,
                  player._appearanceGlyph(item.appearance, item.state),
                  { fontFamily: REPLAY_FONT_FAMILY, fontSize: `${Math.max(14, scale)}px` },
                ).setOrigin(0.5).setDepth(10);
                if (typeof glyph.setResolution === 'function') glyph.setResolution(TEXT_RENDER_RESOLUTION);
                player.capabilityObjects.set(item.instance_key, { glyph, appearance: item.appearance || {} });
              });

              this.cameras.main.setBounds(0, 0, width * scale, height * scale);
              this.cameras.main.setZoom(Math.min(1.2, Math.max(0.35, INITIAL_CAMERA_ZOOM)));
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
        const size = this.manifest?.execution_mode === 'CAPABILITY_COMPOSED' ? 22 : 26;
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
