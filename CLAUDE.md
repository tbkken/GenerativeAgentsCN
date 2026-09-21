# Repository working guide

Read [AGENTS.md](AGENTS.md) first. It is the repository authority for architecture, editing boundaries, case construction and acceptance. This file is a navigation guide and does not introduce a second set of design rules.

**Current documentation**

- [Documentation index](docs/README.md): current contracts, operating guides and case evidence.
- [Architecture](docs/capability-composition-platform-design.md) and [Studio UX](docs/experiment-resource-composition-ux.md).
- [Code guide](docs/code-guide-cn.md), [operations](docs/operations-runbook.md), [model configuration](docs/model-configuration.md) and [tests](docs/test-strategy.md).

Obsolete designs and prototypes have been removed. Follow the current contracts; do not restore author-resource Revision workflows, database Run queues/projections, the former Prompt workflow editor or the old smallville CLI.

**Actual entry points**

- Studio: python -m generative_agents.web.main → ga_studio.web.create_studio_app.
- CLI: ga, or python -m generative_agents.cli.main.
- Runtime: ga_runtime.service / executor; Studio's FileRunSupervisor launches the CLI against a Run directory.
- Replay: ga_replay.reader.ReplayReader.
- Browser shell: generative_agents/web/static/experiment-console.html.

Studio is the only database-owning business module. Experiments follow DRAFT → SEALED and own physical copies of their inputs. Runtime consumes the Run's embedded experiment; Replay reads committed Run facts. Package manifests determine experiment_id, run_id and attempt_id.

Shared kernel code remains in start.py, modules/, runtime/, config/ and skills/, with Studio support in services/ and persistence/. The old web.app, database Run services/workers/projectors, revision Manifest serializer, publication preflight and demo routes have been removed. Package initializers expose only current support; persistence/models.py maps exactly the 11 Studio tables. Follow ga_studio.web.create_studio_app and ga CLI for application startup.

**Commands from the repository root**

~~~bash
python -m pip install -r generative_agents/requirements.txt
python -m pip install -r generative_agents/requirements-dev.txt
python -m pip install -e .
python -m generative_agents.web.main
ga --help
python -m pytest -q -p no:cacheprovider tests/architecture/test_portable_module_boundaries.py tests/test_portable_package_protocol.py
node --test tests/frontend/*.test.cjs
~~~

Health: /api/studio/health. The Web entry point uses one Uvicorn worker; Runtime supervision owns the execution processes. A model service must already be available before a real simulation; the Web launcher does not manage model servers.

Use focused checks from the test guide. Historical tests may still assert superseded database contracts; document those failures rather than restoring the old design or claiming the entire suite is green.

For teaching cases, follow the browser-only authoring and acceptance rules in AGENTS.md, including the explicit approval boundary for newly found defects and the restart/retest requirement after approved fixes. A real case's exact Skill, package copies, verification records and export hashes are evidence, not disposable fixtures.
