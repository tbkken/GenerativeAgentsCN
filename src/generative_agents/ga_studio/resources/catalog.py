"""Mutable public author resources owned exclusively by Studio."""

from __future__ import annotations

import copy
import hashlib
import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select

from generative_agents.ga_protocol.packages.hashing import canonical_json_bytes
from generative_agents.ga_protocol.schemas.experiment import AgentPerceptionLimits
from generative_agents.ga_protocol.schemas.experiment import AgentScratch
from generative_agents.ga_studio.storage.models import StudioAgent
from generative_agents.ga_studio.storage.models import StudioCrowd
from generative_agents.ga_studio.storage.models import StudioEvaluator
from generative_agents.ga_studio.storage.models import StudioModelPreset


class StudioResourceError(ValueError):
    """A mutable Studio resource is missing, invalid, or concurrently changed."""


class StudioAgentDefinition(BaseModel):
    """Reusable Agent content with no map coordinate or semantic address."""

    model_config = ConfigDict(extra="forbid")

    agent_key: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            pattern=r"^[a-z0-9][a-z0-9-]{1,63}$",
        ),
    ]
    enabled: bool = True
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    portrait_asset_id: Annotated[str, StringConstraints(min_length=1, max_length=36)] | None = None
    sprite_asset_id: Annotated[str, StringConstraints(min_length=1, max_length=36)] | None = None
    sprite_layout: Literal["4x3", "4x4"] = "4x4"
    sprite_display_tiles: float | None = Field(default=None, ge=0.5, le=6.0)
    model_override: str | None = None
    tags: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    currently: str = ""
    scratch: AgentScratch
    perception: AgentPerceptionLimits = Field(default_factory=AgentPerceptionLimits)


def _now() -> datetime:
    return datetime.now(UTC)


def _hash(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes({"content": value})).hexdigest()


def _key(value: str, *, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().casefold()).strip("-")
    normalized = normalized[:54].strip("-") or fallback
    if len(normalized) < 2:
        normalized = f"{fallback}-{normalized}"
    return normalized


class StudioResourceService:
    """Direct CRUD for resources whose current row is the authoring truth.

    ``content_hash`` is an integrity/cache value.  It is never selected as a
    version and is never referenced by Runtime or Replay.
    """

    def __init__(self, database) -> None:
        self.database = database

    def create_agent(
        self,
        definition: StudioAgentDefinition | dict[str, Any],
        *,
        description: str = "",
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        parsed = StudioAgentDefinition.model_validate(definition)
        payload = parsed.model_dump(mode="json")
        with self.database.session_factory.begin() as session:
            if session.scalar(select(StudioAgent.id).where(StudioAgent.agent_key == parsed.agent_key)):
                raise StudioResourceError(f"Agent key already exists: {parsed.agent_key}")
            row = StudioAgent(
                id=agent_id or str(uuid4()),
                agent_key=parsed.agent_key,
                name=parsed.name,
                description=description.strip(),
                definition_json=payload,
                content_hash=_hash(payload),
            )
            session.add(row)
            session.flush()
            return self._agent(row)

    def save_agent(
        self,
        agent_id: str,
        definition: StudioAgentDefinition | dict[str, Any],
        *,
        expected_row_version: int,
        description: str | None = None,
    ) -> dict[str, Any]:
        parsed = StudioAgentDefinition.model_validate(definition)
        payload = parsed.model_dump(mode="json")
        with self.database.session_factory.begin() as session:
            row = session.get(StudioAgent, agent_id)
            self._current(row, "Agent", agent_id)
            if row.row_version != expected_row_version:
                raise StudioResourceError(
                    f"Agent changed: expected row {expected_row_version}, found {row.row_version}"
                )
            duplicate = session.scalar(
                select(StudioAgent.id).where(
                    StudioAgent.agent_key == parsed.agent_key,
                    StudioAgent.id != agent_id,
                )
            )
            if duplicate:
                raise StudioResourceError(f"Agent key already exists: {parsed.agent_key}")
            row.agent_key = parsed.agent_key
            row.name = parsed.name
            row.definition_json = payload
            row.content_hash = _hash(payload)
            if description is not None:
                row.description = description.strip()
            row.row_version += 1
            row.updated_at = _now()
            session.flush()
            return self._agent(row)

    def get_agent(self, agent_id: str, *, include_archived: bool = False) -> dict[str, Any]:
        with self.database.session_factory() as session:
            row = session.get(StudioAgent, agent_id)
            self._available(row, "Agent", agent_id, include_archived=include_archived)
            return self._agent(row)

    def list_agents(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        with self.database.session_factory() as session:
            statement = select(StudioAgent)
            if not include_archived:
                statement = statement.where(StudioAgent.archived_at.is_(None))
            rows = session.scalars(statement.order_by(StudioAgent.updated_at.desc(), StudioAgent.id))
            return [self._agent(row) for row in rows]

    def create_crowd(
        self,
        *,
        name: str,
        agent_ids: list[str],
        description: str = "",
        crowd_key: str | None = None,
        crowd_id: str | None = None,
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise StudioResourceError("Crowd name is required")
        key = _key(crowd_key, fallback="crowd") if crowd_key else f"{_key(name, fallback="crowd")[:100]}-{uuid4().hex[:12]}"
        members = list(dict.fromkeys(agent_ids))
        with self.database.session_factory.begin() as session:
            self._require_agents(session, members)
            if session.scalar(select(StudioCrowd.id).where(StudioCrowd.crowd_key == key)):
                raise StudioResourceError(f"Crowd key already exists: {key}")
            row = StudioCrowd(
                id=crowd_id or str(uuid4()),
                crowd_key=key,
                name=name,
                description=description.strip(),
                agent_ids_json=members,
                content_hash=_hash(members),
            )
            session.add(row)
            session.flush()
            return self._crowd(row)

    def save_crowd(
        self,
        crowd_id: str,
        *,
        agent_ids: list[str],
        expected_row_version: int,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        members = list(dict.fromkeys(agent_ids))
        with self.database.session_factory.begin() as session:
            row = session.get(StudioCrowd, crowd_id)
            self._current(row, "Crowd", crowd_id)
            if row.row_version != expected_row_version:
                raise StudioResourceError(
                    f"Crowd changed: expected row {expected_row_version}, found {row.row_version}"
                )
            self._require_agents(session, members)
            if name is not None:
                row.name = name.strip() or row.name
            if description is not None:
                row.description = description.strip()
            row.agent_ids_json = members
            row.content_hash = _hash(members)
            row.row_version += 1
            row.updated_at = _now()
            session.flush()
            return self._crowd(row)

    def get_crowd(self, crowd_id: str, *, include_archived: bool = False) -> dict[str, Any]:
        with self.database.session_factory() as session:
            row = session.get(StudioCrowd, crowd_id)
            self._available(row, "Crowd", crowd_id, include_archived=include_archived)
            return self._crowd(row)

    def list_crowds(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        with self.database.session_factory() as session:
            statement = select(StudioCrowd)
            if not include_archived:
                statement = statement.where(StudioCrowd.archived_at.is_(None))
            rows = session.scalars(statement.order_by(StudioCrowd.updated_at.desc(), StudioCrowd.id))
            return [self._crowd(row) for row in rows]

    def create_model_preset(
        self,
        *,
        name: str,
        config: dict[str, Any],
        description: str = "",
        preset_key: str | None = None,
    ) -> dict[str, Any]:
        return self._create_document_resource(
            StudioModelPreset,
            key_field="preset_key",
            key=_key(preset_key or name, fallback="model"),
            name=name,
            description=description,
            document_field="config_json",
            document=config,
        )

    def get_model_preset(self, preset_id: str) -> dict[str, Any]:
        return self._get_document_resource(
            StudioModelPreset,
            preset_id,
            key_field="preset_key",
            document_field="config_json",
        )

    def list_model_presets(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        return self._list_document_resources(
            StudioModelPreset,
            key_field="preset_key",
            document_field="config_json",
            include_archived=include_archived,
        )

    def save_model_preset(
        self,
        preset_id: str,
        *,
        config: dict[str, Any],
        expected_row_version: int,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        return self._save_document_resource(
            StudioModelPreset,
            preset_id,
            key_field="preset_key",
            document_field="config_json",
            document=config,
            expected_row_version=expected_row_version,
            name=name,
            description=description,
        )

    def create_evaluator(
        self,
        *,
        name: str,
        config: dict[str, Any],
        description: str = "",
        evaluator_key: str | None = None,
    ) -> dict[str, Any]:
        return self._create_document_resource(
            StudioEvaluator,
            key_field="evaluator_key",
            key=_key(evaluator_key or name, fallback="evaluator"),
            name=name,
            description=description,
            document_field="config_json",
            document=config,
        )

    def get_evaluator(self, evaluator_id: str) -> dict[str, Any]:
        return self._get_document_resource(
            StudioEvaluator,
            evaluator_id,
            key_field="evaluator_key",
            document_field="config_json",
        )

    def list_evaluators(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        return self._list_document_resources(
            StudioEvaluator,
            key_field="evaluator_key",
            document_field="config_json",
            include_archived=include_archived,
        )

    def save_evaluator(
        self,
        evaluator_id: str,
        *,
        config: dict[str, Any],
        expected_row_version: int,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        return self._save_document_resource(
            StudioEvaluator,
            evaluator_id,
            key_field="evaluator_key",
            document_field="config_json",
            document=config,
            expected_row_version=expected_row_version,
            name=name,
            description=description,
        )

    def archive(self, kind: str, resource_id: str, *, archived: bool = True) -> None:
        model = self._model(kind)
        with self.database.session_factory.begin() as session:
            row = session.get(model, resource_id)
            self._current(row, kind, resource_id)
            row.archived_at = _now() if archived else None
            row.row_version += 1
            row.updated_at = _now()

    def delete(self, kind: str, resource_id: str) -> None:
        model = self._model(kind)
        with self.database.session_factory.begin() as session:
            row = session.get(model, resource_id)
            self._current(row, kind, resource_id)
            session.delete(row)

    @staticmethod
    def _model(kind: str):
        models = {
            "agent": StudioAgent,
            "crowd": StudioCrowd,
            "model": StudioModelPreset,
            "evaluator": StudioEvaluator,
        }
        try:
            return models[kind]
        except KeyError as exc:
            raise StudioResourceError(f"Unknown Studio resource kind: {kind}") from exc

    @staticmethod
    def _current(row, kind: str, resource_id: str) -> None:
        if row is None:
            raise StudioResourceError(f"{kind} does not exist: {resource_id}")

    @classmethod
    def _available(cls, row, kind: str, resource_id: str, *, include_archived: bool) -> None:
        cls._current(row, kind, resource_id)
        if row.archived_at is not None and not include_archived:
            raise StudioResourceError(f"{kind} is archived: {resource_id}")

    @classmethod
    def _require_agents(cls, session, agent_ids: list[str]) -> None:
        for agent_id in agent_ids:
            row = session.get(StudioAgent, agent_id)
            cls._available(row, "Agent", agent_id, include_archived=False)

    @staticmethod
    def _agent(row: StudioAgent) -> dict[str, Any]:
        return {
            "id": row.id,
            "agent_key": row.agent_key,
            "name": row.name,
            "description": row.description,
            "definition": copy.deepcopy(row.definition_json),
            "content_hash": row.content_hash,
            "row_version": row.row_version,
            "archived_at": row.archived_at.isoformat() if row.archived_at else None,
            "updated_at": row.updated_at.isoformat(),
        }

    @staticmethod
    def _crowd(row: StudioCrowd) -> dict[str, Any]:
        return {
            "id": row.id,
            "crowd_key": row.crowd_key,
            "name": row.name,
            "description": row.description,
            "agent_ids": list(row.agent_ids_json or []),
            "content_hash": row.content_hash,
            "row_version": row.row_version,
            "archived_at": row.archived_at.isoformat() if row.archived_at else None,
            "updated_at": row.updated_at.isoformat(),
        }

    def _create_document_resource(
        self,
        model,
        *,
        key_field: str,
        key: str,
        name: str,
        description: str,
        document_field: str,
        document: dict[str, Any],
    ) -> dict[str, Any]:
        payload = copy.deepcopy(document)
        with self.database.session_factory.begin() as session:
            if session.scalar(select(model.id).where(getattr(model, key_field) == key)):
                raise StudioResourceError(f"Resource key already exists: {key}")
            row = model(
                **{
                    key_field: key,
                    "name": name.strip(),
                    "description": description.strip(),
                    document_field: payload,
                    "content_hash": _hash(payload),
                }
            )
            session.add(row)
            session.flush()
            return self._document(row, key_field=key_field, document_field=document_field)

    def _get_document_resource(self, model, resource_id, *, key_field, document_field):
        with self.database.session_factory() as session:
            row = session.get(model, resource_id)
            self._available(row, model.__name__, resource_id, include_archived=False)
            return self._document(row, key_field=key_field, document_field=document_field)

    def _list_document_resources(
        self,
        model,
        *,
        key_field: str,
        document_field: str,
        include_archived: bool,
    ):
        with self.database.session_factory() as session:
            statement = select(model)
            if not include_archived:
                statement = statement.where(model.archived_at.is_(None))
            rows = session.scalars(statement.order_by(model.updated_at.desc(), model.id))
            return [
                self._document(row, key_field=key_field, document_field=document_field)
                for row in rows
            ]

    def _save_document_resource(
        self,
        model,
        resource_id: str,
        *,
        key_field: str,
        document_field: str,
        document: dict[str, Any],
        expected_row_version: int,
        name: str | None,
        description: str | None,
    ):
        payload = copy.deepcopy(document)
        with self.database.session_factory.begin() as session:
            row = session.get(model, resource_id)
            self._current(row, model.__name__, resource_id)
            if row.row_version != expected_row_version:
                raise StudioResourceError(
                    f"Resource changed: expected row {expected_row_version}, found {row.row_version}"
                )
            setattr(row, document_field, payload)
            row.content_hash = _hash(payload)
            if name is not None:
                row.name = name.strip() or row.name
            if description is not None:
                row.description = description.strip()
            row.row_version += 1
            row.updated_at = _now()
            session.flush()
            return self._document(row, key_field=key_field, document_field=document_field)

    @staticmethod
    def _document(row, *, key_field, document_field):
        return {
            "id": row.id,
            "key": getattr(row, key_field),
            "name": row.name,
            "description": row.description,
            "config": copy.deepcopy(getattr(row, document_field)),
            "content_hash": row.content_hash,
            "row_version": row.row_version,
            "archived_at": row.archived_at.isoformat() if row.archived_at else None,
            "updated_at": row.updated_at.isoformat(),
        }


__all__ = [
    "StudioAgentDefinition",
    "StudioResourceError",
    "StudioResourceService",
]
