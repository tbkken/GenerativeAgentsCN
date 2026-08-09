"""Compatibility import for the single versioned algorithm registry."""

from generative_agents.config.algorithm import (
    ALGORITHM_PROFILES,
    AlgorithmProfile,
    get_algorithm_profile,
)

__all__ = ["ALGORITHM_PROFILES", "AlgorithmProfile", "get_algorithm_profile"]
