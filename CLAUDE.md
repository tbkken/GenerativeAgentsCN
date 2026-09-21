# Repository working guide

Read [AGENTS.md](AGENTS.md) first. It defines architecture and case acceptance boundaries. This file provides navigation, not a second set of design rules.

- [Documentation index](docs/README.md), [architecture](docs/capability-composition-platform-design.md), [source organization](docs/source-organization-design.md).
- [Code guide](docs/code-guide-cn.md), [operations](docs/operations-runbook.md), [model configuration](docs/model-configuration.md), [tests](docs/test-strategy.md).

All product code is under `src/generative_agents/`. `ga_protocol` owns file contracts; `ga_studio` alone owns database access and author resources; `ga_runtime` owns execution and Run writes; `ga_replay` reads committed files. `adapters` contains CLI and Web presentation. Adapters call named operations in module `api.py` files. No retired top-level import paths or compatibility shims exist.

Studio copies selected resource closures into independent DRAFT experiments. Sealing creates `.gaexp`; Runtime embeds it in a new Run. Replay reads Run files or `.garun`. Manifest IDs are authoritative. Natural-language Brain Skills choose behavior; the kernel owns identity, input validation, world commit, recovery and supervision.

```bash
python -m pip install -e ".[runtime,studio,web,dev]"
ga studio serve
python tools/check_source_boundaries.py
python -m pytest tests -q -p no:cacheprovider
node --test tests/frontend/*.test.cjs
```

The sole Web application is `adapters/web/app.py`; CLI parsing is in `adapters/cli/main.py`. Health is `/api/studio/health`. Run workers operate on directories and never receive author registries or database sessions. Skill trials follow the same boundary: Studio prepares files, Runtime executes them.

Run focused checks and the release gates in the test guide. Update README, the code guide and the documentation index when changing entry points. Do not preserve tests requiring retired cognition or SQLite memory; maintain the valid isolation, safety and recovery contracts on current entry points.

For teaching cases, follow AGENTS.md: browser authoring, explicit approval for newly found case defects, service restart and original-path acceptance after approved fixes. Case documents and original exports are scoped evidence and must not be rewritten to claim validation of new code.
