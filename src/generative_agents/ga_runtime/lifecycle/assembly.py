"""Assemble a Run scheduler from package inputs and verified checkpoints."""
from __future__ import annotations
import copy
from pathlib import Path
from collections.abc import Mapping
from generative_agents.ga_runtime.engine.world import Game
from generative_agents.ga_runtime.engine.configuration import ConfigAdapter
from generative_agents.ga_runtime.storage.checkpoints import CheckpointBundleWriter
from generative_agents.ga_runtime.storage.checkpoints import CheckpointSnapshot
from generative_agents.ga_runtime.storage.commit import FileStepCommitter
from generative_agents.ga_runtime.engine.context import SimulationContext
from generative_agents.ga_runtime.storage.frames import FrameStore
from generative_agents.ga_runtime.storage.projection import FileResultProjector
from generative_agents.ga_runtime.engine.scheduler import SimulationRunner

def apply_checkpoint_state(config: dict, state: Mapping) -> dict:
    """把检查点中的动态状态覆盖到已发布配置副本。

    参数:
        config: 当前组件使用的结构化配置；字段约束由对应配置模型定义。 类型：`dict`。
        state: 检查点保存的动态状态映射，必须包含与已发布配置一致的智能体键集合。 类型：`Mapping`。

    返回:
        返回以字段名或业务键组织的结构化映射。

    异常:
        ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
    """
    restored = copy.deepcopy(config)
    checkpoint_agents = state.get('agents')
    if not isinstance(checkpoint_agents, Mapping):
        raise ValueError('checkpoint state must contain an agents mapping')
    configured_keys = set(restored.get('agents', {}))
    checkpoint_keys = set(checkpoint_agents)
    if checkpoint_keys != configured_keys:
        raise ValueError('checkpoint agent keys do not match the published Revision')

    def overlay(target: dict, source: Mapping) -> None:
        """执行 的`overlay`操作。

        参数:
            target: 当前操作使用的`target`。 类型：`dict`。
            source: 当前操作使用的`source`。 类型：`Mapping`。

        返回:
            无返回值。
        """
        for key, value in source.items():
            if isinstance(value, Mapping) and isinstance(target.get(key), dict):
                overlay(target[key], value)
            else:
                target[key] = copy.deepcopy(value)
    for agent_key, agent_state in checkpoint_agents.items():
        if not isinstance(agent_state, Mapping):
            raise ValueError(f'checkpoint agent state is invalid: {agent_key}')
        overlay(restored['agents'][agent_key], agent_state)
    return restored

def build_file_committer(context: SimulationContext, game: Game, *, checkpoint_retention: int=2) -> FileStepCommitter:
    """构建只写运行目录的提交器，供独立命令行仿真使用。

    参数:
        context: 本次调用共享的运行上下文，包含路径、模型、技能和控制能力等依赖。 类型：`SimulationContext`。
        game: 当前运行私有的仿真世界聚合。 类型：`Game`。

    返回:
        返回按“帧—检查点—文件投影”顺序工作的提交器。
    """
    checkpoint = CheckpointBundleWriter(context.paths, lambda _result: CheckpointSnapshot(state=game.snapshot_state(), conversation=game.conversation, storage_exporters=game.storage_exporters(), runtime_storage_exporters=game.runtime_storage_exporters()), retention=checkpoint_retention)
    from generative_agents.ga_runtime.engine.context import RecoveryPaths
    recovery = CheckpointBundleWriter(RecoveryPaths(root=context.paths.root, run_id=context.paths.run_id), lambda _result: CheckpointSnapshot(state=game.snapshot_state(), conversation=game.conversation, storage_exporters=game.storage_exporters(), runtime_storage_exporters=game.runtime_storage_exporters()), retention=2)
    return FileStepCommitter(FrameStore(context.paths), FileResultProjector(context.paths), checkpoint, recovery)

def build_runner(context: SimulationContext, definition, *, embedding_api_key: str='', checkpoint_state: Mapping | None=None, checkpoint_conversation: Mapping | None=None, storage_root: str | Path | None=None) -> SimulationRunner:
    """构建`runner`。

    参数:
        context: 本次调用共享的运行上下文，包含路径、模型、技能和控制能力等依赖。 类型：`SimulationContext`。
        definition: 已校验的仿真定义，描述地图、智能体、模型与执行参数。
        embedding_api_key: 调用嵌入模型服务使用的 API 密钥；为空时由运行配置解析。 类型：`str`。 默认值：`''`。
        checkpoint_state: 从检查点读取的动态世界状态；为空表示从发布配置开始运行。 类型：`Mapping | None`。 默认值：`None`。
        checkpoint_conversation: 检查点保存的对话上下文；为空表示没有待恢复对话。 类型：`Mapping | None`。 默认值：`None`。
        storage_root: 存储使用的根目录路径。 类型：`str | Path | None`。 默认值：`None`。

    返回:
        返回 `SimulationRunner` 类型的处理结果。
    """
    config = ConfigAdapter().game_config(definition, embedding_api_key=embedding_api_key)
    if checkpoint_state is not None:
        config = apply_checkpoint_state(config, checkpoint_state)
    if storage_root is not None:
        config['storage_root'] = str(Path(storage_root))
    game = Game(config, copy.deepcopy(checkpoint_conversation or {}), context=context)
    if checkpoint_state is not None:
        game.restore_runtime_state(dict(checkpoint_state))
    return SimulationRunner(context=context, game=game, committer=build_file_committer(context, game, checkpoint_retention=definition.simulation.checkpoint_retention), checkpoint_interval_steps=definition.simulation.checkpoint_interval_steps)
