"""Stable names shared by Studio, Runtime, and Replay.

This module deliberately contains no application or persistence imports.  The
file formats are the integration boundary between the four GA modules.
"""

from __future__ import annotations

EXPERIMENT_PROTOCOL = "ga-experiment"
RUN_PROTOCOL = "ga-run"
PROTOCOL_VERSION = 1

EXPERIMENT_ARCHIVE_SUFFIX = ".gaexp"
RUN_ARCHIVE_SUFFIX = ".garun"

EXPERIMENT_MANIFEST = "manifest.json"
RUN_MANIFEST = "run.json"
RUN_STATUS = "status.json"
INTEGRITY_MANIFEST = "integrity/sha256.json"

DEFAULT_EXPERIMENT_ENTRYPOINTS = {
    "world": "world/world.json",
    "semantic_index": "world/semantic-index.json",
    "agents": "agents/index.json",
    "skills": "skills/registry.json",
    "models": "models/models.json",
    "simulation": "runtime/simulation.json",
    "engine": "runtime/engine.json",
    "evaluation": "evaluation/evaluators.json",
}
