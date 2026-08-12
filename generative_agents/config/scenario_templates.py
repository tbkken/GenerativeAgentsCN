"""Versioned, reusable blueprints for capability-composed experiments."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from .capabilities import StableKey
from .schema import StrictModel
from .scenarios import ExperimentCapabilityExtension


class ScenarioTemplateActorSlot(StrictModel):
    slot_key: StableKey
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    role: Literal["DRIVER", "PEDESTRIAN", "CYCLIST", "OBSERVER", "OTHER"]
    description: Annotated[str, StringConstraints(max_length=2_000)] = ""


class ScenarioTemplateContract(StrictModel):
    schema_version: Literal["ga-scenario-template/v1"] = "ga-scenario-template/v1"
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    summary: Annotated[str, StringConstraints(max_length=2_000)] = ""
    tags: list[StableKey] = Field(default_factory=list, max_length=100)
    actor_slots: list[ScenarioTemplateActorSlot] = Field(
        min_length=1, max_length=1_000
    )
    blueprint: ExperimentCapabilityExtension

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("scenario template tags must be unique")
        return value

    @model_validator(mode="after")
    def validate_blueprint(self) -> "ScenarioTemplateContract":
        if self.blueprint.mode != "CAPABILITY_COMPOSED":
            raise ValueError("scenario template blueprint must be CAPABILITY_COMPOSED")
        slot_keys = [slot.slot_key for slot in self.actor_slots]
        if len(slot_keys) != len(set(slot_keys)):
            raise ValueError("scenario template actor slot keys must be unique")
        actors = {actor.actor_key: actor for actor in self.blueprint.actors}
        if set(slot_keys) != set(actors):
            raise ValueError("actor slots must exactly match blueprint actor keys")
        for slot in self.actor_slots:
            if actors[slot.slot_key].role != slot.role:
                raise ValueError(
                    f"actor slot {slot.slot_key} role does not match blueprint"
                )
        return self


__all__ = ["ScenarioTemplateActorSlot", "ScenarioTemplateContract"]
