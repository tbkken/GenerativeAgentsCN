"""Runtime contracts for isolated experiment workers."""

from .algorithm import AlgorithmProfile, get_algorithm_profile
from .brain import BrainRuntime
from .capabilities import PlannedWorldAction, SimulationMCPServer
from .checkpoint import CheckpointBundleWriter, CheckpointSnapshot
from .context import (
    RunControl,
    RunPaths,
    FileSkillInstructionRepository,
    SnapshotSkillInstructionRepository,
    SimulationClock,
    SimulationContext,
)
from .frame_store import FrameConflictError, FrameStore
from .iteration import IterationContext
from .manifest import RunManifestStore, VerifiedRunManifest, build_manifest_document
from .model_trace import (
    ModelTraceEvent,
    ModelTraceEventType,
    ModelTraceStatus,
    ModelTraceWriter,
)
from .results import (
    ActionSnapshot,
    ActivityKind,
    AgentStepResult,
    ConversationMessage,
    ConversationRecord,
    DomainEventRecord,
    MemoryDelta,
    MemoryDeltaKind,
    ModelUsageDelta,
    ScheduleRevisionRecord,
    StepEffectKind,
    StepEffectRecord,
    StepResult,
    StepResultBuilder,
)
__all__ = [
    "ActionSnapshot",
    "ActivityKind",
    "AgentStepResult",
    "AlgorithmProfile",
    "BrainRuntime",
    "CheckpointBundleWriter",
    "CheckpointSnapshot",
    "ConversationMessage",
    "ConversationRecord",
    "DomainEventRecord",
    "FrameConflictError",
    "FrameStore",
    "MemoryDelta",
    "MemoryDeltaKind",
    "IterationContext",
    "ModelTraceEvent",
    "ModelTraceEventType",
    "ModelTraceStatus",
    "ModelTraceWriter",
    "ModelTraceProjectionError",
    "ModelTraceProjector",
    "ModelUsageDelta",
    "PlannedWorldAction",
    "RunControl",
    "RunManifestStore",
    "RunPaths",
    "FileSkillInstructionRepository",
    "SnapshotSkillInstructionRepository",
    "ResultProjectionError",
    "ScheduleRevisionRecord",
    "StepEffectKind",
    "StepEffectRecord",
    "SimulationClock",
    "SimulationContext",
    "SimulationMCPServer",
    "StepResult",
    "StepResultBuilder",
    "SqliteResultProjector",
    "VerifiedRunManifest",
    "build_manifest_document",
    "get_algorithm_profile",
]


def __getattr__(name: str):
    """Load legacy Studio database projectors only when explicitly requested.

    The portable Runtime is file-only.  This compatibility shim prevents a plain
    Runtime import from importing SQLAlchemy while the old Studio worker is being
    retired.
    """

    if name in {"ResultProjectionError", "SqliteResultProjector"}:
        from .result_projector import ResultProjectionError, SqliteResultProjector

        return {
            "ResultProjectionError": ResultProjectionError,
            "SqliteResultProjector": SqliteResultProjector,
        }[name]
    if name in {"ModelTraceProjectionError", "ModelTraceProjector"}:
        from .trace_projector import ModelTraceProjectionError, ModelTraceProjector

        return {
            "ModelTraceProjectionError": ModelTraceProjectionError,
            "ModelTraceProjector": ModelTraceProjector,
        }[name]
    raise AttributeError(name)
