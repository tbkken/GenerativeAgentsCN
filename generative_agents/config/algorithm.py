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
    chat_chars_per_minute: int
    default_event_poignancy: int

    def as_dict(self) -> dict[str, int]:
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
    chat_chars_per_minute=240,
    default_event_poignancy=1,
)

ALGORITHM_PROFILES: Mapping[str, AlgorithmProfile] = MappingProxyType(
    {"ga-cn-v1": GA_CN_V1}
)


def get_algorithm_profile(version: str) -> AlgorithmProfile:
    """Return a supported immutable profile or fail closed."""

    try:
        return ALGORITHM_PROFILES[version]
    except KeyError as exc:
        raise ValueError(f"unsupported algorithm_version: {version}") from exc
