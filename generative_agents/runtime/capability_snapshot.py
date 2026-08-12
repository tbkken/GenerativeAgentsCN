"""Build the immutable, transitive input snapshot for a composed simulation.

The capability editor stores references to published revisions.  A worker must
never resolve those references from the live catalog because doing so would make
resume and replay depend on mutable database state.  This module expands every
reference needed by an experiment revision into one canonical document which is
then sealed inside the Run manifest.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy.orm import Session

from generative_agents.config.brain_capabilities import BrainCapabilityExtension
from generative_agents.config.capabilities import (
    CapabilityBundleContract,
    CapabilityContract,
)
from generative_agents.config.hashing import canonical_json_bytes
from generative_agents.config.scenarios import ExperimentCapabilityExtension
from generative_agents.config.spatial_assets import (
    SpatialAssetContract,
    SpatialSceneExtension,
)
from generative_agents.config.tools import AgentCapabilityExtension, ToolContract
from generative_agents.persistence.models import (
    AgentRevisionExtension,
    AgentTemplateRevision,
    BrainRevision,
    BrainRevisionExtension,
    CapabilityBundleRevision,
    CapabilityRevision,
    ExperimentRevision,
    ExperimentRevisionCapability,
    SpatialAssetRevision,
    ToolRevision,
    WorldMapRevision,
)


CAPABILITY_SNAPSHOT_SCHEMA_VERSION = "ga-capability-runtime-snapshot/v1"


def capability_snapshot_hash(document: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def _published(session: Session, model, revision_id: str, label: str):
    row = session.get(model, revision_id)
    if row is None or row.state != "PUBLISHED":
        raise RuntimeError(f"composed scenario references unavailable {label} {revision_id}")
    return row


def build_capability_runtime_snapshot(
    session: Session,
    revision: ExperimentRevision,
) -> dict[str, Any] | None:
    """Expand a published experiment extension into a transitive run snapshot."""

    extension_row = session.get(ExperimentRevisionCapability, revision.id)
    if extension_row is None:
        return None
    extension = ExperimentCapabilityExtension.model_validate(
        extension_row.extension_json
    )
    if extension.mode == "LEGACY_TOWN":
        return None

    map_revision = _published(
        session, WorldMapRevision, extension.map_revision_id, "map revision"
    )
    scene_raw = (map_revision.world_json.get("definition") or {}).get(
        "spatial_scene"
    )
    scene = SpatialSceneExtension.model_validate(scene_raw or {})

    spatial_revision_ids = set(scene.palette_refs.values())
    spatial_revision_ids.update(
        placement.spatial_asset_revision_id for placement in scene.placements
    )
    tool_revision_ids = {
        instance.tool_revision_id for instance in extension.tool_instances
    }
    agent_revision_ids = {
        actor.agent_revision_id
        for actor in extension.actors
        if actor.agent_revision_id
    }
    bundle_revision_ids = {
        mount.capability_bundle_revision_id
        for mount in extension.capability_mounts
        if mount.enabled
    }
    capability_revision_ids: set[str] = set()

    spatial_documents: dict[str, dict[str, Any]] = {}
    for revision_id in sorted(spatial_revision_ids):
        row = _published(
            session, SpatialAssetRevision, revision_id, "spatial asset revision"
        )
        contract = SpatialAssetContract.model_validate(row.contract_json)
        spatial_documents[revision_id] = {
            "revision_id": row.id,
            "contract_hash": row.contract_hash,
            "contract": contract.model_dump(mode="json", exclude_none=False),
        }
        for attachment in contract.capability_attachments:
            if not attachment.enabled:
                continue
            if attachment.capability_revision_id:
                capability_revision_ids.add(attachment.capability_revision_id)
            if attachment.capability_bundle_revision_id:
                bundle_revision_ids.add(attachment.capability_bundle_revision_id)

    tool_documents: dict[str, dict[str, Any]] = {}

    def add_tool(revision_id: str) -> None:
        if revision_id in tool_documents:
            return
        row = _published(session, ToolRevision, revision_id, "tool revision")
        contract = ToolContract.model_validate(row.contract_json)
        tool_documents[revision_id] = {
            "revision_id": row.id,
            "contract_hash": row.contract_hash,
            "contract": contract.model_dump(mode="json", exclude_none=False),
        }
        for attachment in contract.capability_attachments:
            if not attachment.enabled:
                continue
            if attachment.capability_revision_id:
                capability_revision_ids.add(attachment.capability_revision_id)
            if attachment.capability_bundle_revision_id:
                bundle_revision_ids.add(attachment.capability_bundle_revision_id)

    for revision_id in sorted(tool_revision_ids):
        add_tool(revision_id)

    agent_documents: dict[str, dict[str, Any]] = {}
    agent_extension_documents: dict[str, dict[str, Any]] = {}
    for revision_id in sorted(agent_revision_ids):
        row = _published(
            session, AgentTemplateRevision, revision_id, "agent revision"
        )
        agent_documents[revision_id] = {
            "revision_id": row.id,
            "definition_hash": row.definition_hash,
            "definition": row.definition_json,
        }
        extension_record = session.get(AgentRevisionExtension, revision_id)
        if extension_record is None:
            continue
        agent_extension = AgentCapabilityExtension.model_validate(
            extension_record.extension_json
        )
        agent_extension_documents[revision_id] = {
            "extension_hash": extension_record.extension_hash,
            "extension": agent_extension.model_dump(
                mode="json", exclude_none=False
            ),
        }
        bundle_revision_ids.update(
            agent_extension.capability_bundle_revision_ids
        )
        for grant in agent_extension.tool_grants:
            tool_revision_ids.add(grant.tool_revision_id)
        mobility = agent_extension.mobility_choice
        if mobility.decision_capability_revision_id:
            capability_revision_ids.add(mobility.decision_capability_revision_id)
        if mobility.decision_bundle_revision_id:
            bundle_revision_ids.add(mobility.decision_bundle_revision_id)

    # Agent extensions may introduce tool revisions after the first pass.
    for revision_id in sorted(tool_revision_ids):
        add_tool(revision_id)

    brain_document: dict[str, Any] | None = None
    brain_revision_id = (revision.provenance_json or {}).get("brain_revision_id")
    if brain_revision_id:
        brain = _published(session, BrainRevision, brain_revision_id, "brain revision")
        brain_document = {
            "revision_id": brain.id,
            "bundle_hash": brain.bundle_hash,
            "prompts": brain.prompts_json,
        }
        brain_extension_row = session.get(BrainRevisionExtension, brain.id)
        if brain_extension_row is not None:
            brain_extension = BrainCapabilityExtension.model_validate(
                brain_extension_row.extension_json
            )
            brain_document["extension_hash"] = brain_extension_row.extension_hash
            brain_document["extension"] = brain_extension.model_dump(
                mode="json", exclude_none=False
            )
            bundle_revision_ids.update(
                mount.capability_bundle_revision_id
                for mount in brain_extension.mounts
                if mount.enabled
            )

    bundle_documents: dict[str, dict[str, Any]] = {}
    capability_documents: dict[str, dict[str, Any]] = {}

    # Resolve transitively because bundles add capability revisions and
    # capabilities may declare direct revision dependencies.
    while True:
        unresolved_bundles = sorted(set(bundle_revision_ids) - set(bundle_documents))
        for revision_id in unresolved_bundles:
            row = _published(
                session,
                CapabilityBundleRevision,
                revision_id,
                "capability bundle revision",
            )
            composition = CapabilityBundleContract.model_validate(
                row.composition_json
            )
            bundle_documents[revision_id] = {
                "revision_id": row.id,
                "composition_hash": row.composition_hash,
                "composition": composition.model_dump(
                    mode="json", exclude_none=False
                ),
            }
            capability_revision_ids.update(
                instance.capability_revision_id
                for instance in composition.instances
                if instance.enabled
            )

        unresolved_capabilities = sorted(
            set(capability_revision_ids) - set(capability_documents)
        )
        for revision_id in unresolved_capabilities:
            row = _published(
                session, CapabilityRevision, revision_id, "capability revision"
            )
            contract = CapabilityContract.model_validate(row.contract_json)
            capability_documents[revision_id] = {
                "revision_id": row.id,
                "contract_hash": row.contract_hash,
                "contract": contract.model_dump(mode="json", exclude_none=False),
            }
            capability_revision_ids.update(
                dependency.capability_revision_id
                for dependency in contract.dependencies
                if dependency.capability_revision_id and not dependency.optional
            )

        if not unresolved_bundles and not unresolved_capabilities:
            break

    return {
        "schema_version": CAPABILITY_SNAPSHOT_SCHEMA_VERSION,
        "experiment_extension_hash": extension_row.extension_hash,
        "experiment_extension": extension.model_dump(
            mode="json", exclude_none=False
        ),
        "map_revision": {
            "revision_id": map_revision.id,
            "world_hash": map_revision.world_hash,
            "world": map_revision.world_json,
        },
        "spatial_assets": spatial_documents,
        "tools": tool_documents,
        "agents": agent_documents,
        "agent_extensions": agent_extension_documents,
        "brain": brain_document,
        "capability_bundles": bundle_documents,
        "capabilities": capability_documents,
    }


__all__ = [
    "CAPABILITY_SNAPSHOT_SCHEMA_VERSION",
    "build_capability_runtime_snapshot",
    "capability_snapshot_hash",
]
