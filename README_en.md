[简体中文](README.md)

# GenerativeAgentsCN

A simulation project in which natural-language Brain Skills define agent behavior and Game Object Skills define object behavior. Studio edits author resources and experiments; Runtime executes self-contained packages; Replay reads committed facts.

Start with the [documentation index](docs/README.md). [AGENTS.md](AGENTS.md) defines repository constraints and the current architecture.

**Install and run**

Use Python 3.11 or later and run these commands from the repository root. Create and activate a virtual environment first; see the [Chinese quick start](README.md) for Windows and Unix activation commands.

~~~bash
python -m pip install -r generative_agents/requirements.txt
python -m pip install -e .
python -m generative_agents.web.main
~~~

Open [Studio](http://127.0.0.1:8000/). The health endpoint is [api/studio/health](http://127.0.0.1:8000/api/studio/health). Defaults are var/generative-agents.db for Studio author data and var/ for local files.

The editable install registers the ga command. It does not install the full runtime dependencies declared in requirements.txt; the current uv.lock is not a complete runtime dependency lock.

**Configure an experiment**

Create a user map and agents, choose or author a Brain and its dependent Skills, and configure chat and embedding models in the model center. Provide explicit model IDs and test the connections. See [model configuration](docs/model-configuration.md).

Creating an experiment physically copies the selected map, assets, agents, model configuration and complete Skill dependencies. Editing public resources later does not update existing experiments. Validate the experiment draft and seal it before running. A sealed experiment is immutable; changes require a new independent experiment copy.

The system has no default map or built-in public Agent catalog. Agent placement belongs to the experiment. Brain Skills decide the task sequence; the kernel validates perception, memory access and world actions.

**Portable packages**

| Module | Responsibility |
| --- | --- |
| ga_protocol | Package schemas, identity, integrity, safe archives and file contracts |
| ga_studio | Author resources, experiment editing, package building and catalog |
| ga_runtime | Execution, supervision, checkpoints and recovery |
| ga_replay | Read-only reconstruction from committed Run facts |

Studio owns the author database. Runtime and Replay consume files and do not depend on Studio database state. The [code guide](docs/code-guide-cn.md) also identifies shared components still used from older directories.

For an existing complete experiment directory:

~~~bash
ga experiment seal ./my-experiment ./my-experiment.gaexp
ga experiment validate ./my-experiment.gaexp
ga run start ./my-experiment.gaexp ./my-run --steps 24
ga run status ./my-run
ga replay state ./my-run 12
ga run seal ./my-run ./my-run.garun
~~~

Resume a paused Run with ga run resume ./my-run. Rerun a completed Run with ga run rerun ./my-run ./another-run. Resuming a .garun archive requires --destination for the writable directory. Model credentials are supplied on the execution host, not embedded in packages.

See the [operations guide](docs/operations-runbook.md), [tests](docs/test-strategy.md) and [teaching cases](docs/book/sample/README.md). Code and automated checks are not a substitute for the documented browser and model validation of each case.

Research origins: [Generative Agents](https://github.com/joonspk-research/generative_agents) and [wounderland](https://github.com/Archermmt/wounderland). See [LICENSE](LICENSE).
