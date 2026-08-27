"""实验定义的严格 Pydantic 模型。

本模块是“用户草稿”和“可执行运行清单”之间的第一道边界：未知字段会被拒绝，
跨字段关系会在发布前校验，运行时因此不需要猜测缺失值或兼容任意输入。
"""

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

Key = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")]
SecretRef = Annotated[str, StringConstraints(min_length=1, max_length=64)]
ModelName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)
]


class StrictModel(BaseModel):
    """所有配置模型的共同基类：禁止额外字段，并在赋值时持续校验。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def _normalize_v1_url(value: AnyHttpUrl | str) -> str:
    """规范化`v1``url`。

    参数:
        value: 当前操作使用的`value`。 类型：`AnyHttpUrl | str`。

    返回:
        返回处理后的文本或稳定标识。
    """
    parsed = urlsplit(str(value).rstrip("/"))
    path = parsed.path.rstrip("/")
    if not path.endswith("/v1"):
        path = f"{path}/v1" if path else "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class ExperimentMetadata(StrictModel):
    """实验的展示信息、所有者、标签和用于解释虚拟时间的时区。"""
    key: Key
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    goal: Annotated[str, StringConstraints(max_length=10_000)] = ""
    timezone: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
    ] = "Asia/Shanghai"

    @field_validator("timezone")
    @classmethod
    def timezone_must_exist(cls, value: str) -> str:
        """执行 `ExperimentMetadata` 的`timezone``must``exist`操作。

        参数:
            value: 当前操作使用的`value`。 类型：`str`。

        返回:
            返回处理后的文本或稳定标识。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown IANA timezone") from exc
        return value


class EngineConfig(StrictModel):
    """固定底层仿真算法版本，避免同一 Revision 被不同算法解释。"""
    algorithm_version: Literal["ga-cn-v1"] = "ga-cn-v1"
    brain_skill: Key = "stanford-town-brain"


class SimulationConfig(StrictModel):
    """控制虚拟起点、步数、步长、随机种子和检查点频率。"""
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
        """执行 `SimulationConfig` 的`require``aware``datetime`操作。

        参数:
            value: 当前操作使用的`value`。 类型：`datetime`。

        返回:
            返回 `datetime` 类型的处理结果。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("start_time must include a UTC offset")
        return value

    @model_validator(mode="after")
    def checkpoint_within_run(self) -> "SimulationConfig":
        """执行 `SimulationConfig` 的检查点`within`运行操作。

        返回:
            返回 `'SimulationConfig'` 类型的处理结果。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if self.checkpoint_interval_steps > self.max_steps:
            raise ValueError("checkpoint_interval_steps must not exceed max_steps")
        return self


class ResultsConfig(StrictModel):
    """声明运行结束后需要保留和生成的结果类型。"""
    agent_step_projection_interval_steps: int = Field(default=1, ge=1, le=100)
    replay_interpolation_frames: int = Field(default=60, ge=1, le=120)
    capture_model_payloads: bool = False


class ChatTransport(StrictModel):
    """所有聊天模型传输配置共享的超时、重试和输出限制。"""
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
    """连接 vLLM 或其他本地 OpenAI 兼容聊天端点的配置。"""
    provider: Literal["vllm"]
    base_url: AnyHttpUrl
    secret_ref: SecretRef | None = None

    @field_validator("base_url")
    @classmethod
    def normalize_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        """规范化`url`。

        参数:
            value: 当前操作使用的`value`。 类型：`AnyHttpUrl`。

        返回:
            返回 `AnyHttpUrl` 类型的处理结果。
        """
        return AnyHttpUrl(_normalize_v1_url(value))


class ChatOpenAIConfig(ChatTransport):
    """连接显式指定模型的 OpenAI 兼容聊天端点配置。"""
    provider: Literal["openai"]
    base_url: AnyHttpUrl = "https://api.openai.com/v1"
    secret_ref: SecretRef

    @field_validator("model")
    @classmethod
    def no_auto(cls, value: str) -> str:
        """执行 `ChatOpenAIConfig` 的`no``auto`操作。

        参数:
            value: 当前操作使用的`value`。 类型：`str`。

        返回:
            返回处理后的文本或稳定标识。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if value.casefold() == "auto":
            raise ValueError("openai chat requires an explicit model")
        return value

    @field_validator("base_url")
    @classmethod
    def normalize_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        """规范化`url`。

        参数:
            value: 当前操作使用的`value`。 类型：`AnyHttpUrl`。

        返回:
            返回 `AnyHttpUrl` 类型的处理结果。
        """
        return AnyHttpUrl(_normalize_v1_url(value))


class ChatOllamaConfig(ChatTransport):
    """连接 Ollama 原生聊天接口的配置。"""
    provider: Literal["ollama"]
    base_url: AnyHttpUrl
    secret_ref: SecretRef | None = None

    @field_validator("model")
    @classmethod
    def no_auto(cls, value: str) -> str:
        """执行 `ChatOllamaConfig` 的`no``auto`操作。

        参数:
            value: 当前操作使用的`value`。 类型：`str`。

        返回:
            返回处理后的文本或稳定标识。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if value.casefold() == "auto":
            raise ValueError("ollama chat requires an explicit model")
        return value


ChatModelConfig = Annotated[
    Union[ChatVLLMConfig, ChatOpenAIConfig, ChatOllamaConfig],
    Field(discriminator="provider"),
]


class EmbeddingTransport(StrictModel):
    """所有向量模型传输方式共享的超时和批处理配置。"""
    model: ModelName
    resolved_model: ModelName | None = None
    timeout_seconds: int = Field(default=120, ge=1, le=1800)
    transport_retry_attempts: int = Field(default=3, ge=1, le=20)
    index_operation_retry_attempts: int = Field(default=10, ge=1, le=20)
    retry_backoff_seconds: float = Field(default=5, ge=0, le=300)


class EmbeddingOpenAICompatibleConfig(EmbeddingTransport):
    """连接本地 OpenAI 兼容向量端点的配置。"""
    provider: Literal["openai_compatible"]
    base_url: AnyHttpUrl
    secret_ref: SecretRef | None = None

    @field_validator("base_url")
    @classmethod
    def normalize_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        """规范化`url`。

        参数:
            value: 当前操作使用的`value`。 类型：`AnyHttpUrl`。

        返回:
            返回 `AnyHttpUrl` 类型的处理结果。
        """
        return AnyHttpUrl(_normalize_v1_url(value))


class EmbeddingOpenAIConfig(EmbeddingTransport):
    """连接需要凭据引用的 OpenAI 兼容向量端点配置。"""
    provider: Literal["openai"]
    base_url: AnyHttpUrl = "https://api.openai.com/v1"
    secret_ref: SecretRef

    @field_validator("model")
    @classmethod
    def no_auto(cls, value: str) -> str:
        """执行 `EmbeddingOpenAIConfig` 的`no``auto`操作。

        参数:
            value: 当前操作使用的`value`。 类型：`str`。

        返回:
            返回处理后的文本或稳定标识。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if value.casefold() == "auto":
            raise ValueError("openai embedding requires an explicit model")
        return value

    @field_validator("base_url")
    @classmethod
    def normalize_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        """规范化`url`。

        参数:
            value: 当前操作使用的`value`。 类型：`AnyHttpUrl`。

        返回:
            返回 `AnyHttpUrl` 类型的处理结果。
        """
        return AnyHttpUrl(_normalize_v1_url(value))


class EmbeddingOllamaConfig(EmbeddingTransport):
    """连接 Ollama 向量接口的配置。"""
    provider: Literal["ollama"]
    base_url: AnyHttpUrl
    secret_ref: SecretRef | None = None

    @field_validator("model")
    @classmethod
    def no_auto(cls, value: str) -> str:
        """执行 `EmbeddingOllamaConfig` 的`no``auto`操作。

        参数:
            value: 当前操作使用的`value`。 类型：`str`。

        返回:
            返回处理后的文本或稳定标识。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if value.casefold() == "auto":
            raise ValueError("ollama embedding requires an explicit model")
        return value


class EmbeddingHuggingFaceConfig(EmbeddingTransport):
    """在本地进程中加载 Hugging Face 向量模型的配置。"""
    provider: Literal["hugging_face"]

    @field_validator("model")
    @classmethod
    def no_auto(cls, value: str) -> str:
        """执行 `EmbeddingHuggingFaceConfig` 的`no``auto`操作。

        参数:
            value: 当前操作使用的`value`。 类型：`str`。

        返回:
            返回处理后的文本或稳定标识。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
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
    """把聊天模型与向量模型组合成一次实验使用的模型集合。"""
    chat: ChatModelConfig
    embedding: EmbeddingModelConfig


class PerceptConfig(StrictModel):
    """控制智能体每次感知的空间范围和事件数量。"""
    mode: Literal["box"] = "box"
    vision_radius: int = Field(default=8, ge=1, le=10_000)
    attention_bandwidth: int = Field(default=8, ge=1, le=1_000_000)


class ScheduleConfig(StrictModel):
    """控制日程拆解粒度和计划修订行为。"""
    max_try: int = Field(default=5, ge=1, le=100)
    diversity: int = Field(default=5, ge=1, le=100)


class ThinkConfig(StrictModel):
    """控制反思触发阈值及认知检索规模。"""
    poignancy_max: int = Field(default=150, ge=1, le=1_000_000)
    reflection_focus_count: int = Field(default=3, ge=1, le=20)
    reflection_insight_count: int = Field(default=5, ge=1, le=20)


class ChatBehaviorConfig(StrictModel):
    """控制对话持续时间、冷却时间和重复内容限制。"""
    max_iterations: int = Field(default=4, ge=1, le=100)
    stop_after_hour: int = Field(default=23, ge=0, le=23)
    cooldown_minutes: int = Field(default=60, ge=0, le=10_080)
    repeat_detection_enabled: bool = True


class MemoryConfig(StrictModel):
    """控制记忆容量、检索权重和遗忘策略。"""
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
        """执行 `MemoryConfig` 的`unlimited``or``positive`操作。

        参数:
            value: 当前操作使用的`value`。 类型：`int`。

        返回:
            返回计算得到的整数值或版本号。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if value != -1 and value <= 0:
            raise ValueError("max_memories_per_type must be -1 or a positive integer")
        return value

    @model_validator(mode="after")
    def at_least_one_weight(self) -> "MemoryConfig":
        """执行 `MemoryConfig` 的`at``least``one``weight`操作。

        返回:
            返回 `'MemoryConfig'` 类型的处理结果。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if self.recency_weight + self.relevance_weight + self.importance_weight <= 0:
            raise ValueError(
                "at least one memory score weight must be greater than zero"
            )
        return self


class BehaviorConfig(StrictModel):
    """汇总感知、日程、思考、对话和记忆等智能体行为参数。"""
    percept: PerceptConfig = Field(default_factory=PerceptConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    think: ThinkConfig = Field(default_factory=ThinkConfig)
    chat: ChatBehaviorConfig = Field(default_factory=ChatBehaviorConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)


class AssetReference(StrictModel):
    """引用已上传资产，并限制路径只能落在受控的相对目录中。"""
    logical_path: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    asset_hash: Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
    media_type: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    size: int = Field(ge=0)

    @field_validator("logical_path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        """执行 `AssetReference` 的`safe``relative`路径操作。

        参数:
            value: 当前操作使用的`value`。 类型：`str`。

        返回:
            返回处理后的文本或稳定标识。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError("logical_path must be a safe relative path")
        return normalized


class WorldOverlayConfig(StrictModel):
    """Experiment-owned changes layered over one immutable public map revision."""

    definition_patch: dict[str, Any] = Field(default_factory=dict)
    asset_additions: list[AssetReference] = Field(default_factory=list)
    removed_asset_paths: list[str] = Field(default_factory=list)

    @field_validator("removed_asset_paths")
    @classmethod
    def safe_removed_paths(cls, values: list[str]) -> list[str]:
        """执行 `WorldOverlayConfig` 的`safe``removed``paths`操作。

        参数:
            values: 需要规范化、校验、拼接或批量处理的值集合。 类型：`list[str]`。

        返回:
            返回按接口约定组织的结果集合。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        normalized: list[str] = []
        for value in values:
            path = value.replace("\\", "/")
            if not path or path.startswith("/") or ".." in path.split("/"):
                raise ValueError("removed asset paths must be safe relative paths")
            normalized.append(path)
        if len(normalized) != len(set(normalized)):
            raise ValueError("removed asset paths must be unique")
        return normalized


class WorldConfig(StrictModel):
    """运行世界的权威定义：地图身份、Tile、资源和编辑器扩展。"""
    world_key: Key
    world_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    definition: dict[str, Any] = Field(default_factory=dict)
    assets: list[AssetReference] = Field(default_factory=list)
    map_id: str | None = None
    map_revision_id: str | None = None
    map_revision_hash: (
        Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None
    ) = None
    overlay: WorldOverlayConfig = Field(default_factory=WorldOverlayConfig)

    @model_validator(mode="after")
    def validate_map_reference(self) -> "WorldConfig":
        """校验地图`reference`。

        返回:
            返回 `'WorldConfig'` 类型的处理结果。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        reference = (self.map_id, self.map_revision_id, self.map_revision_hash)
        if any(reference) and not all(reference):
            raise ValueError(
                "map_id, map_revision_id and map_revision_hash must be set together"
            )
        if not self.map_revision_id and (
            self.overlay.definition_patch
            or self.overlay.asset_additions
            or self.overlay.removed_asset_paths
        ):
            raise ValueError("world overlay requires a public map revision")
        return self


class AgentScratch(StrictModel):
    """智能体在运行开始时使用的身份、当前状态与初始计划。"""
    age: int = Field(ge=0, le=200)
    innate: str = ""
    learned: str = ""
    lifestyle: str = ""
    daily_plan: str = ""


class AgentSpatial(StrictModel):
    """智能体的出生坐标和必须属于当前地图的语义地址。"""
    address: dict[str, Any] = Field(default_factory=dict)
    tree: dict[str, Any] = Field(default_factory=dict)


class AgentTemplateDefinition(StrictModel):
    """Versioned public Agent data copied into experiment-owned Agents."""

    agent_key: Key
    enabled: bool = True
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    portrait_asset: str | None = None
    sprite_asset: str | None = None
    model_override: str | None = None
    tags: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    coord: tuple[int, int] = (0, 0)
    currently: str = ""
    scratch: AgentScratch
    spatial: AgentSpatial = Field(default_factory=AgentSpatial)


class AgentDefinition(StrictModel):
    """单个智能体的完整发布配置，包含认知状态和空间状态。"""
    agent_key: Key
    enabled: bool = True
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    portrait_asset: str | None = None
    sprite_asset: str | None = None
    model_override: str | None = None
    tags: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    coord: tuple[int, int]
    currently: str = ""
    scratch: AgentScratch
    spatial: AgentSpatial = Field(default_factory=AgentSpatial)


class ExperimentDefinition(StrictModel):
    """一次实验 Revision 的完整、可哈希、可冻结定义。"""
    schema_version: Literal[1] = 1
    experiment: ExperimentMetadata
    engine: EngineConfig = Field(default_factory=EngineConfig)
    simulation: SimulationConfig
    results: ResultsConfig = Field(default_factory=ResultsConfig)
    models: ModelsConfig
    behavior: BehaviorConfig = Field(default_factory=BehaviorConfig)
    world: WorldConfig
    agents: list[AgentDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_internal_relations(self) -> "ExperimentDefinition":
        """校验`internal``relations`。

        返回:
            返回 `'ExperimentDefinition'` 类型的处理结果。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if (
            self.results.agent_step_projection_interval_steps
            > self.simulation.max_steps
        ):
            raise ValueError(
                "agent_step_projection_interval_steps must not exceed max_steps"
            )
        agent_keys = [agent.agent_key for agent in self.agents]
        if len(agent_keys) != len(set(agent_keys)):
            raise ValueError("agent_key must be unique within an experiment")
        return self


REQUIRED_ATOMIC_SKILLS = frozenset(
    """base_desc decide_chat decide_chat_terminate decide_wait decide_wait_example
    decide_game_object_interaction decide_game_object_response
    describe_emoji describe_event describe_object determine_arena determine_object
    determine_sector generate_chat generate_chat_check_repeat poignancy_chat
    poignancy_event reflect_chat_memory reflect_chat_planing reflect_focus
    reflect_insights retrieve_currently retrieve_plan retrieve_thought schedule_daily
    schedule_decompose schedule_init schedule_revise summarize_chats
    summarize_relation wake_up""".split()
)


def make_blank_definition(
    *, key: str, name: str, goal: str = ""
) -> ExperimentDefinition:
    """执行 的`make``blank`仿真定义操作。

    参数:
        key: 用于定位目标记录、配置项或技能的稳定键。 类型：`str`。
        name: 目标对象的人类可读名称。 类型：`str`。
        goal: 路径搜索、计划或推理任务需要达到的目标。 类型：`str`。 默认值：`''`。

    返回:
        返回 `ExperimentDefinition` 类型的处理结果。
    """

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
                    "model": "qwen3.8:27b-q4_K_M",
                    "base_url": "http://127.0.0.1:11434/v1",
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
        }
    )
