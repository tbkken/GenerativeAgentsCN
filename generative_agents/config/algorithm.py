"""Read-only algorithm profiles recorded by every published revision."""

from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class AlgorithmProfile:
    sentence_chunk_size: int
    sentence_chunk_overlap: int
    llama_num_output: int
    llama_context_window: int
    similarity_top_k: int
    focus_retrieve_max: int
    schedule_decompose_threshold_minutes: int
    path_target_sample_limit: int
    movement_tiles_per_minute: int
    chat_chars_per_minute: int
    default_event_poignancy: int

    def as_dict(self) -> dict[str, int]:
        """执行 `AlgorithmProfile` 的`as``dict`操作。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        return asdict(self)


GA_CN_V1 = AlgorithmProfile(
    sentence_chunk_size=512,
    sentence_chunk_overlap=64,
    llama_num_output=1024,
    llama_context_window=4096,
    similarity_top_k=5,
    focus_retrieve_max=30,
    schedule_decompose_threshold_minutes=60,
    path_target_sample_limit=4,
    movement_tiles_per_minute=4,
    chat_chars_per_minute=240,
    default_event_poignancy=1,
)

ALGORITHM_PROFILES: Mapping[str, AlgorithmProfile] = MappingProxyType(
    {"ga-cn-v1": GA_CN_V1}
)


def get_algorithm_profile(version: str) -> AlgorithmProfile:
    """获取`algorithm``profile`。

    参数:
        version: 当前数据、协议或生成器使用的版本号。 类型：`str`。

    返回:
        返回 `AlgorithmProfile` 类型的处理结果。

    异常:
        ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
    """

    try:
        return ALGORITHM_PROFILES[version]
    except KeyError as exc:
        raise ValueError(f"unsupported algorithm_version: {version}") from exc
