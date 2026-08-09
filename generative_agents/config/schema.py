"""Strict Pydantic v2 schema for isolated experiment definitions."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal, Union
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .hashing import content_hash

Key = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")]
SecretRef = Annotated[str, StringConstraints(min_length=1, max_length=64)]
ModelName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def _normalize_v1_url(value: AnyHttpUrl | str) -> str:
    parsed = urlsplit(str(value).rstrip("/"))
    path = parsed.path.rstrip("/")
    if not path.endswith("/v1"):
        path = f"{path}/v1" if path else "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class ExperimentMetadata(StrictModel):
    key: Key
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    goal: Annotated[str, StringConstraints(max_length=10_000)] = ""
    timezone: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)] = "Asia/Shanghai"

    @field_validator("timezone")
    @classmethod
    def timezone_must_exist(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown IANA timezone") from exc
        return value


class EngineConfig(StrictModel):
    algorithm_version: Literal["ga-cn-v1"] = "ga-cn-v1"


class SimulationConfig(StrictModel):
    start_time: datetime
    stride_minutes: int = Field(default=10, ge=1, le=1440)
    max_steps: int = Field(default=1000, ge=1, le=1_000_000)
    checkpoint_interval_steps: int = Field(default=1, ge=1)
    checkpoint_retention: int = Field(default=2, ge=2, le=20)
    record_interval_minutes: int = Field(default=30, ge=1, le=1440)
    random_seed: int = Field(default=42, ge=-(2**63), le=2**63 - 1)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("start_time")
    @classmethod
    def require_aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("start_time must include a UTC offset")
        return value

    @model_validator(mode="after")
    def checkpoint_within_run(self) -> "SimulationConfig":
        if self.checkpoint_interval_steps > self.max_steps:
            raise ValueError("checkpoint_interval_steps must not exceed max_steps")
        return self


class ResultsConfig(StrictModel):
    agent_step_projection_interval_steps: int = Field(default=1, ge=1, le=100)
    replay_interpolation_frames: int = Field(default=60, ge=1, le=120)
    capture_model_payloads: bool = False


class ChatTransport(StrictModel):
    model: ModelName
    resolved_model: ModelName | None = None
    context_window: int | None = Field(default=None, ge=1, le=10_000_000)
    timeout_seconds: int = Field(default=300, ge=1, le=1800)
    max_tokens: int = Field(default=2048, ge=1, le=131_072)
    temperature: float = Field(default=0.5, ge=0, le=2)
    enable_thinking: bool = False
    retry_attempts: int = Field(default=10, ge=1, le=20)
    retry_backoff_seconds: float = Field(default=5, ge=0, le=300)


class ChatVLLMConfig(ChatTransport):
    provider: Literal["vllm"]
    base_url: AnyHttpUrl
    secret_ref: SecretRef | None = None

    @field_validator("base_url")
    @classmethod
    def normalize_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        return AnyHttpUrl(_normalize_v1_url(value))


class ChatOpenAIConfig(ChatTransport):
    provider: Literal["openai"]
    base_url: AnyHttpUrl = "https://api.openai.com/v1"
    secret_ref: SecretRef

    @field_validator("model")
    @classmethod
    def no_auto(cls, value: str) -> str:
        if value.casefold() == "auto":
            raise ValueError("openai chat requires an explicit model")
        return value

    @field_validator("base_url")
    @classmethod
    def normalize_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        return AnyHttpUrl(_normalize_v1_url(value))


class ChatOllamaConfig(ChatTransport):
    provider: Literal["ollama"]
    base_url: AnyHttpUrl
    secret_ref: SecretRef | None = None

    @field_validator("model")
    @classmethod
    def no_auto(cls, value: str) -> str:
        if value.casefold() == "auto":
            raise ValueError("ollama chat requires an explicit model")
        return value


ChatModelConfig = Annotated[
    Union[ChatVLLMConfig, ChatOpenAIConfig, ChatOllamaConfig],
    Field(discriminator="provider"),
]


class EmbeddingTransport(StrictModel):
    model: ModelName
    resolved_model: ModelName | None = None
    timeout_seconds: int = Field(default=120, ge=1, le=1800)
    transport_retry_attempts: int = Field(default=3, ge=1, le=20)
    index_operation_retry_attempts: int = Field(default=10, ge=1, le=20)
    retry_backoff_seconds: float = Field(default=5, ge=0, le=300)


class EmbeddingOpenAICompatibleConfig(EmbeddingTransport):
    provider: Literal["openai_compatible"]
    base_url: AnyHttpUrl
    secret_ref: SecretRef | None = None

    @field_validator("base_url")
    @classmethod
    def normalize_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        return AnyHttpUrl(_normalize_v1_url(value))


class EmbeddingOpenAIConfig(EmbeddingTransport):
    provider: Literal["openai"]
    base_url: AnyHttpUrl = "https://api.openai.com/v1"
    secret_ref: SecretRef

    @field_validator("model")
    @classmethod
    def no_auto(cls, value: str) -> str:
        if value.casefold() == "auto":
            raise ValueError("openai embedding requires an explicit model")
        return value

    @field_validator("base_url")
    @classmethod
    def normalize_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        return AnyHttpUrl(_normalize_v1_url(value))


class EmbeddingOllamaConfig(EmbeddingTransport):
    provider: Literal["ollama"]
    base_url: AnyHttpUrl
    secret_ref: SecretRef | None = None

    @field_validator("model")
    @classmethod
    def no_auto(cls, value: str) -> str:
        if value.casefold() == "auto":
            raise ValueError("ollama embedding requires an explicit model")
        return value


class EmbeddingHuggingFaceConfig(EmbeddingTransport):
    provider: Literal["hugging_face"]

    @field_validator("model")
    @classmethod
    def no_auto(cls, value: str) -> str:
        if value.casefold() == "auto":
            raise ValueError("hugging_face embedding requires an explicit model")
        return value


EmbeddingModelConfig = Annotated[
    Union[
        EmbeddingOpenAICompatibleConfig,
        EmbeddingOpenAIConfig,
        EmbeddingOllamaConfig,
        EmbeddingHuggingFaceConfig,
    ],
    Field(discriminator="provider"),
]


class ModelsConfig(StrictModel):
    chat: ChatModelConfig
    embedding: EmbeddingModelConfig


class PerceptConfig(StrictModel):
    mode: Literal["box"] = "box"
    vision_radius: int = Field(default=8, ge=1, le=10_000)
    attention_bandwidth: int = Field(default=8, ge=1, le=1_000_000)


class ScheduleConfig(StrictModel):
    max_try: int = Field(default=5, ge=1, le=100)
    diversity: int = Field(default=5, ge=1, le=100)


class ThinkConfig(StrictModel):
    poignancy_max: int = Field(default=150, ge=1, le=1_000_000)
    reflection_focus_count: int = Field(default=3, ge=1, le=20)
    reflection_insight_count: int = Field(default=5, ge=1, le=20)


class ChatBehaviorConfig(StrictModel):
    max_iterations: int = Field(default=4, ge=1, le=100)
    stop_after_hour: int = Field(default=23, ge=0, le=23)
    cooldown_minutes: int = Field(default=60, ge=0, le=10_080)
    repeat_detection_enabled: bool = True


class MemoryConfig(StrictModel):
    retention: int = Field(default=8, ge=1, le=100_000)
    max_memories_per_type: int = -1
    reflection_memory_limit: int = Field(default=10, ge=1, le=100)
    recency_decay: float = Field(default=0.995, gt=0, le=1)
    recency_weight: float = Field(default=0.5, ge=0)
    relevance_weight: float = Field(default=3.0, ge=0)
    importance_weight: float = Field(default=2.0, ge=0)
    default_expire_days: int = Field(default=30, ge=1, le=36_500)

    @field_validator("max_memories_per_type")
    @classmethod
    def unlimited_or_positive(cls, value: int) -> int:
        if value != -1 and value <= 0:
            raise ValueError("max_memories_per_type must be -1 or a positive integer")
        return value

    @model_validator(mode="after")
    def at_least_one_weight(self) -> "MemoryConfig":
        if self.recency_weight + self.relevance_weight + self.importance_weight <= 0:
            raise ValueError("at least one memory score weight must be greater than zero")
        return self


class BehaviorConfig(StrictModel):
    percept: PerceptConfig = Field(default_factory=PerceptConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    think: ThinkConfig = Field(default_factory=ThinkConfig)
    chat: ChatBehaviorConfig = Field(default_factory=ChatBehaviorConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)


class AssetReference(StrictModel):
    logical_path: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    asset_hash: Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
    media_type: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    size: int = Field(ge=0)

    @field_validator("logical_path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError("logical_path must be a safe relative path")
        return normalized


class WorldConfig(StrictModel):
    world_key: Key
    world_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    definition: dict[str, Any] = Field(default_factory=dict)
    assets: list[AssetReference] = Field(default_factory=list)


class AgentScratch(StrictModel):
    age: int = Field(ge=0, le=200)
    innate: str = ""
    learned: str = ""
    lifestyle: str = ""
    daily_plan: str = ""


class AgentSpatial(StrictModel):
    address: dict[str, Any] = Field(default_factory=dict)
    tree: dict[str, Any] = Field(default_factory=dict)


class AgentDefinition(StrictModel):
    agent_key: Key
    enabled: bool = True
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    portrait_asset: str | None = None
    coord: tuple[int, int]
    currently: str = ""
    scratch: AgentScratch
    spatial: AgentSpatial = Field(default_factory=AgentSpatial)


class PromptDefinition(StrictModel):
    content: str
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None

    @model_validator(mode="after")
    def calculate_server_hash(self) -> "PromptDefinition":
        expected = content_hash(self.content)
        if self.sha256 is not None and self.sha256 != expected:
            raise ValueError("prompt sha256 does not match normalized content")
        object.__setattr__(self, "sha256", expected)
        return self


class ExperimentDefinition(StrictModel):
    schema_version: Literal[1] = 1
    experiment: ExperimentMetadata
    engine: EngineConfig = Field(default_factory=EngineConfig)
    simulation: SimulationConfig
    results: ResultsConfig = Field(default_factory=ResultsConfig)
    models: ModelsConfig
    behavior: BehaviorConfig = Field(default_factory=BehaviorConfig)
    world: WorldConfig
    agents: list[AgentDefinition] = Field(default_factory=list)
    prompts: dict[str, PromptDefinition] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_internal_relations(self) -> "ExperimentDefinition":
        if self.results.agent_step_projection_interval_steps > self.simulation.max_steps:
            raise ValueError(
                "agent_step_projection_interval_steps must not exceed max_steps"
            )
        agent_keys = [agent.agent_key for agent in self.agents]
        if len(agent_keys) != len(set(agent_keys)):
            raise ValueError("agent_key must be unique within an experiment")
        return self


REQUIRED_PROMPT_KEYS = frozenset(
    """base_desc decide_chat decide_chat_terminate decide_wait decide_wait_example
    describe_emoji describe_event describe_object determine_arena determine_object
    determine_sector generate_chat generate_chat_check_repeat poignancy_chat
    poignancy_event reflect_chat_memory reflect_chat_planing reflect_focus
    reflect_insights retrieve_currently retrieve_plan retrieve_thought schedule_daily
    schedule_decompose schedule_init schedule_revise summarize_chats
    summarize_relation wake_up""".split()
)


def make_blank_definition(*, key: str, name: str, goal: str = "") -> ExperimentDefinition:
    """Create an explicit draft starter; publishing still requires real catalog data."""

    prompt_map = {prompt_key: {"content": ""} for prompt_key in REQUIRED_PROMPT_KEYS}
    return ExperimentDefinition.model_validate(
        {
            "schema_version": 1,
            "experiment": {
                "key": key,
                "name": name,
                "goal": goal,
                "timezone": "Asia/Shanghai",
            },
            "engine": {"algorithm_version": "ga-cn-v1"},
            "simulation": {
                "start_time": "2026-02-13T00:00:00+08:00",
                "stride_minutes": 10,
                "max_steps": 1000,
                "checkpoint_interval_steps": 1,
                "checkpoint_retention": 2,
                "record_interval_minutes": 30,
                "random_seed": 42,
                "log_level": "INFO",
            },
            "results": {},
            "models": {
                "chat": {
                    "provider": "vllm",
                    "model": "auto",
                    "base_url": "http://127.0.0.1:5001",
                },
                "embedding": {
                    "provider": "openai_compatible",
                    "model": "auto",
                    "base_url": "http://127.0.0.1:5002",
                },
            },
            "behavior": {},
            "world": {
                "world_key": "blank-world",
                "world_name": "未配置世界",
                "definition": {},
                "assets": [],
            },
            "agents": [],
            "prompts": prompt_map,
        }
    )
