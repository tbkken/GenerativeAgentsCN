"""Pure mapping from a published ExperimentDefinition to runtime domain inputs."""

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
        # These values initialize internal state containers still owned by Agent.
        # They are deliberately not part of the product configuration contract:
        # Brain Skill + public MCP decide the user-visible reasoning workflow.
        runtime_base = {
            "think": {
                "poignancy_max": 150,
                "reflection_focus_count": 3,
                "reflection_insight_count": 5,
                "llm": chat_config,
            },
            "chat": {
                "max_iterations": 4,
                "stop_after_hour": 23,
                "cooldown_minutes": 60,
                "repeat_detection_enabled": True,
            },
            "chat_iter": 4,
            "associate": {
                "embedding": embedding_config,
                "retention": 8,
                "max_memory": -1,
                "max_importance": 10,
                "recency_decay": 0.995,
                "recency_weight": 0.5,
                "relevance_weight": 3.0,
                "importance_weight": 2.0,
                "memory": {"event": [], "thought": [], "chat": []},
            },
            "schedule": {
                "daily_schedule": [],
                "diversity": 5,
                "max_try": 5,
            },
        }
        agents = {}
        for agent in definition.agents:
            if not agent.enabled:
                continue
            perception = agent.perception
            agents[agent.agent_key] = {
                **copy.deepcopy(runtime_base),
                "percept": {
                    "mode": perception.mode,
                    "vision_r": perception.vision_radius,
                    "att_bandwidth": perception.attention_bandwidth,
                },
                "name": agent.name,
                "coord": list(agent.coord),
                "currently": agent.currently,
                "scratch": agent.scratch.model_dump(mode="json"),
                "spatial": agent.spatial.model_dump(mode="json"),
            }
        return {
            "maze": copy.deepcopy(definition.world.definition),
            "agents": agents,
        }
