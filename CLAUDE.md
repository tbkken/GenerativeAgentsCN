# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A Chinese-localized, refactored fork of the Stanford "Generative Agents" (smallville)
simulation. It has grown well beyond the original: the simulation engine now runs as an
**experiment-isolated web service** — a FastAPI "experiment console" where each experiment is
a versioned, immutable **Revision** executed by an isolated worker subprocess, with full
observability (attempts, logs, model traces, checkpoints) and replay.

The original smallville engine lives in `generative_agents/modules/` (Game / Agent / Maze /
memory / LLM). Everything around it — versioning, process isolation, persistence, REST API,
UI — is the "capability composition platform" built on top of it.

- Bilingual: code & comments in English; user-facing strings, UI text, and API error messages
  in Chinese (`zh-CN`).
- Python ≥3.11 (dev on 3.13). No console entry points; run via `python -m`.

## Commands

All commands run from the repo root (`E:\GenerativeAgentsCN`).

### Install

```bash
python -m pip install -r generative_agents/requirements.txt
python -m pip install -r generative_agents/requirements-dev.txt   # dev/test only (pytest, httpx, playwright)
```

### Run the web service (primary interface)

```bash
python -m generative_agents.web.main \
  --database-url sqlite:///var/generative-agents.db \
  --var-dir var \
  --host 127.0.0.1 --port 8000 --max-concurrent-runs 2
```

- Console UI: `http://127.0.0.1:8000/`
- Health: `http://127.0.0.1:8000/api/v1/health/ready`, capacity at `/api/v1/runtime/capacity`
- On Windows, `restart-web.bat` kills the old `generative_agents.web.main` process on port 8000,
  relaunches it in the background, and polls the health endpoint. Logs go to `var/logs/`.
- **Uvicorn runs `workers=1` by design.** Concurrency comes from the supervisor spawning
  child worker processes — do NOT scale by adding web workers (they would fight over
  supervisor ownership).

The worker that actually executes a Run is launched by the supervisor as:

```bash
python -m generative_agents.runtime.worker --database-url ... --var-dir var --run-id <uuid> --attempt-id <uuid> --start-step N
```

(You normally don't invoke this directly — it's spawned per Run. `PYTHONUTF8=1` is set to keep
logs UTF-8 on Windows.)

### Tests

No `pytest.ini`/`pyproject` pytest config; run plain `pytest` from the repo root. Tests build a
temp SQLite DB via fixtures (do not substitute `:memory:` for concurrency tests).

```bash
pytest                                            # full suite
pytest tests/foundation/test_config_schema.py     # one file
pytest "tests/foundation/test_web_api.py::test_name"   # one test
pytest tests/architecture                         # structural "red line" tests
pytest tests/runtime                              # runtime contracts, asset store, secret protection
pytest tests/legacy                               # pre-refactor behavior characterization
```

Test layout: `tests/architecture/` = red-line/structural invariants (expected to fail on the
old baseline, each assertion tied to a DEF id); `tests/foundation/` = unit/DB/service;
`tests/runtime/` = runtime contracts; `tests/legacy/` = old-behavior characterization.
`playwright` is used for browser tests.

## Architecture

Layered, all under `generative_agents/`:

- **`config/`** — strict Pydantic v2 schemas (`ExperimentDefinition`, `ToolContract`,
  `SpatialAssetContract`, `AgentTemplateDefinition`), canonical-JSON hashing
  (`canonical_json_bytes`/`definition_hash`), and `validate_for_publish`. This is the contract
  layer; `StrictModel` uses `extra="forbid"`. Algorithm profile is pinned to `ga-cn-v1`.
- **`modules/`** — the smallville domain engine: `Game`, `Agent` (percept/think/chat, LLM
  driven), `Maze`, `memory/` (event, spatial, schedule, associate), `model/llm_model.py`,
  `prompt/`. The LLM brain of the simulation.
- **`runtime/`** — run orchestration & durability: `supervisor.py` (spawns/reaps worker
  subprocesses, enforces run slots), `scheduler.py`, `checkpoint.py`, `commit.py`,
  `frame_store.py`, `manifest.py`, `recovery.py`, `result_projector.py`, `replay_v2.py`.
- **`services/`** — REST service logic: `ExperimentService`, `RunService`, `WorldMapService`,
  `CrowdService`, `SpatialAssetService`, `ToolService`, `ArtifactService`, `CheckpointService`,
  `ReplayService`, `ModelProbeService`, `ResultQueryService`, `LogService`.
- **`persistence/`** — SQLAlchemy 2 models + **Alembic migrations `0001`→`0023`**. Web startup
  runs `upgrade_database`. Published Revisions are immutable **at the DB level** (unique
  partial indexes, CHECK constraints, triggers).
- **`web/`** — FastAPI app. `app.py` is a large route table under `/api/v1/...`; `main.py` is
  the uvicorn entry; `skill_api.py`, `replay_schemas.py`, `observability_schemas.py`.
- **`skills/`** — file-backed agent Skills + an MCP server (`SkillMCPServer`) + `MemoryStream`
  (durable per-agent SQLite memory).
- **`start.py`** — `SimulationRunner`, the per-Run simulation loop (steps the Game, moves
  agents, commits results/checkpoints each step), plus an import-safe worker CLI adapter.
- **`frontend/`, `web/static/`** — console UI (`experiment-console.html`) and a Phaser-based
  replay player; village assets under `frontend/static/assets/village/`. These must ship in any
  release build.

### Core flow

`create experiment (draft)` → `validate` → `publish (immutable Revision)` → `run`
(supervisor claims a slot and spawns a `runtime.worker` subprocess) → **per-step commit**
(frames + checkpoints) → `results` (query / replay / artifacts) → `observability`
(attempts, logs, model traces, checkpoints). Runs support pause/resume/cancel and checkpoint
recovery.

### Invariants ("red lines" — enforced by `tests/architecture/`)

- **Experiment isolation**: one experiment's draft/publish/run/resume/query/export must never
  mutate another experiment's facts.
- **Process isolation**: web `workers=1`; concurrent experiments are separate child processes,
  one per slot; no double-claiming a Run slot.
- **No global singletons** for Game/Timer/Model; no LlamaIndex global `Settings` writes.
- **Runtime must not read** bootstrap config / prompts / agents / maps from the shared `data/`
  directory during a Run.
- **Path isolation**: physical run directories use system-generated IDs (UUIDs), never
  experiment/agent display names or user paths.
- **Result integrity**: domain code writes results only through `StepResultBuilder`;
  manifest/Frame/checkpoint size+SHA are verified — any mismatch is rejected.
- **Secrets** are encrypted with `var/master.key`; API responses are scrubbed.
- **Determinism**: no module-level RNG; all RNG must be inside checkpoints.

### Two external model services (both OpenAI-compatible)

- **Chat model** — e.g. `http://127.0.0.1:5001/v1` (vLLM/Qwen).
- **Embedding model** — e.g. `http://127.0.0.1:5002/v1` (`qwen3-embedding-0.6b`).

Config schema supports providers `vllm` / `openai` / `ollama`. `model: "auto"` resolves to the
first model from `/v1/models` at publish time. `generative_agents/data/config.json` is the
legacy standalone engine config (provider/base_url/model for think + embedding); the web path
uses the DB-backed experiment definition instead.

### Data layout (runtime, under `var/`)

```
var/
├─ generative-agents.db   # SQLite (WAL)
├─ master.key             # secret-encryption key
├─ assets/sha256/
├─ runs/<run-uuid>/{manifest.json, attempts/<uuid>/, frames/, checkpoints/, artifacts/}
└─ logs/
```

Do not hand-edit Run dirs / manifest / frames / checkpoints — they're integrity-checked.

## Key reference docs

- `docs/operations-runbook.md` — install, model-server startup, service launch, health checks.
- `docs/test-strategy.md` — test layers, severity model, and the red-line invariants above.
- `docs/experiment-web-service-technical-design.md`, `docs/capability-composition-platform-design.md`.
- `docs/vllm.md`, `docs/embedding.md`, `docs/ollama.md` — model-server configuration.
