"""Pure mapping from a published ExperimentDefinition to legacy domain inputs."""

from __future__ import annotations

import copy

from generative_agents.config import ExperimentDefinition


class ConfigAdapter:
    """No file, database or environment reads are permitted in this adapter."""

    def game_config(
        self,
        definition: ExperimentDefinition,
        *,
        embedding_api_key: str = "",
    ) -> dict:
        """执行 `ConfigAdapter` 的仿真世界配置操作。

        参数:
            definition: 已校验的仿真定义，描述地图、智能体、模型与执行参数。 类型：`ExperimentDefinition`。
            embedding_api_key: 调用嵌入模型服务使用的 API 密钥；为空时由运行配置解析。 类型：`str`。 默认值：`''`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        behavior = definition.behavior
        embedding = definition.models.embedding.model_dump(
            mode="json", exclude_none=False
        )
        embedding_config = {
            "provider": embedding["provider"],
            "model": embedding.get("resolved_model") or embedding["model"],
            "base_url": embedding.get("base_url", ""),
            "api_key": embedding_api_key,
            "timeout": embedding["timeout_seconds"],
            "max_retries": embedding["transport_retry_attempts"],
            "index_operation_retry_attempts": embedding[
                "index_operation_retry_attempts"
            ],
            "retry_backoff_seconds": embedding["retry_backoff_seconds"],
        }
        chat = definition.models.chat.model_dump(mode="json", exclude_none=False)
        chat_config = {
            "provider": chat["provider"],
            "model": chat.get("resolved_model") or chat["model"],
            "base_url": chat.get("base_url", ""),
            "api_key": "",
            "timeout": chat["timeout_seconds"],
            "max_tokens": chat["max_tokens"],
            "temperature": chat["temperature"],
            "enable_thinking": chat["enable_thinking"],
            "retry_attempts": chat["retry_attempts"],
            "retry_backoff_seconds": chat["retry_backoff_seconds"],
        }
        base = {
            "percept": {
                "mode": behavior.percept.mode,
                "vision_r": behavior.percept.vision_radius,
                "att_bandwidth": behavior.percept.attention_bandwidth,
            },
            "think": {
                "poignancy_max": behavior.think.poignancy_max,
                "reflection_focus_count": behavior.think.reflection_focus_count,
                "reflection_insight_count": behavior.think.reflection_insight_count,
                "llm": chat_config,
            },
            "chat": behavior.chat.model_dump(mode="json"),
            "chat_iter": behavior.chat.max_iterations,
            "associate": {
                "embedding": embedding_config,
                "retention": behavior.memory.retention,
                "max_memory": behavior.memory.max_memories_per_type,
                "max_importance": behavior.memory.reflection_memory_limit,
                "recency_decay": behavior.memory.recency_decay,
                "recency_weight": behavior.memory.recency_weight,
                "relevance_weight": behavior.memory.relevance_weight,
                "importance_weight": behavior.memory.importance_weight,
                "memory": {"event": [], "thought": [], "chat": []},
            },
            "schedule": {
                "daily_schedule": [],
                "diversity": behavior.schedule.diversity,
                "max_try": behavior.schedule.max_try,
            },
        }
        agents = {}
        for agent in definition.agents:
            if not agent.enabled:
                continue
            agents[agent.agent_key] = {
                **copy.deepcopy(base),
                "name": agent.name,
                "coord": list(agent.coord),
                "currently": agent.currently,
                "scratch": agent.scratch.model_dump(mode="json"),
                "spatial": agent.spatial.model_dump(mode="json"),
            }
        return {
            "record_interval_minutes": definition.simulation.record_interval_minutes,
            "maze": copy.deepcopy(definition.world.definition),
            "agents": agents,
        }
