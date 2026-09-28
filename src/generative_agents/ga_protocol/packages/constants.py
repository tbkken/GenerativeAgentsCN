"""Stable names shared by Studio, Runtime, and Replay.

This module deliberately contains no application or persistence imports.  The
file formats are the integration boundary between the four GA modules.
"""

from __future__ import annotations

PACKAGE_PROTOCOL = "ga-package"
EXPERIMENT_PROTOCOL = PACKAGE_PROTOCOL
RUN_PROTOCOL = PACKAGE_PROTOCOL
PROTOCOL_VERSION = 2

CONFIG_ARCHIVE_SUFFIX = ".gaconfig"

EXPERIMENT_ARCHIVE_SUFFIX = ".gaexp"
RUN_ARCHIVE_SUFFIX = ".garun"

EXPERIMENT_MANIFEST = "manifest.json"
RUN_MANIFEST = "run.json"
RUN_STATUS = "status.json"
INTEGRITY_MANIFEST = "integrity/sha256.json"

DEFAULT_EXPERIMENT_ENTRYPOINTS = {
    "resources": "resources/index.json",
    "assembly": "runtime/assembly.json",
}
