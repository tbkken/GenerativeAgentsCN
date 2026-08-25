"""单个隔离实验工作进程的入口与生命周期管理。"""

from __future__ import annotations

import argparse
import json
import logging
import random
import shutil
import threading
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from filelock import FileLock
from generative_agents.config import get_algorithm_profile
from generative_agents.persistence import create_database
from generative_agents.persistence.models import Run
from generative_agents.security import MasterKeyStore, SecretCipher
from generative_agents.skills import (
    MemoryStream,
    SkillMCPServer,
    SnapshotPassiveSkillRuntime,
)
from generative_agents.status import RunStatus

from .checkpoint import CheckpointBundleWriter, CheckpointSnapshot
from .commit import FileStepCommitter
from .context import (
    RunControl,
    RunPaths,
    SnapshotSkillInstructionRepository,
    SimulationClock,
    SimulationContext,
)
from .frame_store import FrameStore
from .manifest import RunManifestStore
from .model_trace import ModelTraceWriter
from .scheduler import LocalRunSchedulerRepository
from .sqlite_result_projector import SqliteResultProjector
from .trace_projector import ModelTraceProjector

if TYPE_CHECKING:
    from generative_agents.start import SimulationRunner


class ModelFactoryRegistry:
    """为每个智能体创建带调用轨迹的模型，且不修改 SDK 全局配置。"""

    def __init__(self, config: dict, recorder: ModelTraceWriter, *, control, logger):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            config: 当前组件使用的结构化配置；字段约束由对应配置模型定义。 类型：`dict`。
            recorder: 接收模型调用、步骤副作用或诊断事件的记录器。 类型：`ModelTraceWriter`。
            control: 运行控制器，用于在安全边界检测暂停、取消或终止请求。
            logger: 记录运行诊断信息的日志器。

        返回:
            无返回值。
        """
        self._config = config
        self._recorder = recorder
        self._control = control
        self._logger = logger

    def get(self, purpose: str):
        """执行 `ModelFactoryRegistry` 的`get`操作。

        参数:
            purpose: 模型用途键，用于从运行私有模型注册表选择对应模型。 类型：`str`。

        返回:
            返回函数计算得到的结果。

        异常:
            KeyError: 当必需的键或映射项不存在时抛出。
        """
        if purpose != "chat":
            raise KeyError(f"unsupported model purpose: {purpose}")
        # 该导入会加载模型 SDK 栈，因此故意延迟到心跳线程启动之后。
        # 在仅 CPU 的 Windows 主机上，导入 LlamaIndex/OpenAI 可能耗时数十秒。
        from generative_agents.modules.model.llm_model import create_llm_model

        return create_llm_model(
            dict(self._config),
            recorder=self._recorder,
            control=self._control,
            logger=self._logger,
        )


def _parser() -> argparse.ArgumentParser:
    """执行`parser`的内部处理，供当前模块或类复用。

    返回:
        返回 `argparse.ArgumentParser` 类型的处理结果。
    """
    parser = argparse.ArgumentParser(description="run one experiment attempt")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--var-dir", required=True)
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--attempt-id", type=UUID, required=True)
    parser.add_argument("--start-step", type=int, required=True)
    return parser


def _secret_value(definition, purpose: str, cipher: SecretCipher, database) -> str:
    """执行密钥`value`的内部处理，供当前模块或类复用。

    参数:
        definition: 已校验的仿真定义，描述地图、智能体、模型与执行参数。
        purpose: 模型用途键，用于从运行私有模型注册表选择对应模型。 类型：`str`。
        cipher: 负责密钥加密和解密的密码组件。 类型：`SecretCipher`。
        database: 持久化数据库访问对象或会话工厂。

    返回:
        返回处理后的文本或稳定标识。

    异常:
        RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
    """
    model = getattr(definition.models, purpose)
    secret_id = model.secret_ref
    if not secret_id:
        return ""
    from generative_agents.persistence.models import Secret

    with database.session_factory() as session:
        secret = session.get(Secret, secret_id)
        if secret is None:
            raise RuntimeError(f"{purpose} model secret_ref does not exist")
        encrypted = secret.encrypted_value
    return cipher.decrypt(encrypted)


def _logger(run_id: UUID, level: str) -> logging.LoggerAdapter:
    """执行`logger`的内部处理，供当前模块或类复用。

    参数:
        run_id: 仿真运行的唯一标识。 类型：`UUID`。
        level: 日志级别、树层级或重要性等级。 类型：`str`。

    返回:
        返回 `logging.LoggerAdapter` 类型的处理结果。
    """
    logger = logging.getLogger(f"generative_agents.worker.{run_id}")
    logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s run=%(run_id)s %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logging.LoggerAdapter(logger, {"run_id": str(run_id)})


def _install_sqlite_committer(
    runner,
    database,
    var_dir: Path,
    *,
    checkpoint_retention: int,
    trace_writer: ModelTraceWriter,
) -> None:
    """执行`install``sqlite``committer`的内部处理，供当前模块或类复用。

    参数:
        runner: 负责按步骤推进仿真世界并提交结果的运行器。
        database: 持久化数据库访问对象或会话工厂。
        var_dir: 运行时可变数据根目录，用于保存数据库、帧、检查点和产物。 类型：`Path`。
        checkpoint_retention: 每个运行最多保留的检查点数量；超出部分按策略清理。 类型：`int`。
        trace_writer: 把模型物理调用与逻辑调用事实追加到轨迹文件的写入器。 类型：`ModelTraceWriter`。

    返回:
        无返回值。
    """
    checkpoint = CheckpointBundleWriter(
        runner.context.paths,
        lambda _result: CheckpointSnapshot(
            state=runner.game.snapshot_state(),
            conversation=runner.game.conversation,
            storage_exporters=runner.game.storage_exporters(),
            runtime_storage_exporters=runner.game.runtime_storage_exporters(),
        ),
        retention=checkpoint_retention,
    )
    result_projection = SqliteResultProjector(database, var_dir=var_dir)
    trace_projection = ModelTraceProjector(database, var_dir=var_dir)
    relative_trace_path = trace_writer.path.resolve().relative_to(var_dir).as_posix()

    class StepAndTraceProjection:
        """Project the durable frame first, then every complete trace record."""

        def commit_step(self, result, *, frame, checkpoint_path):
            """原子提交单步查询投影，并返回更新后的结果版本号。

            参数:
                result: 当前仿真步或上游组件产生的结构化结果。
                frame: 当前仿真步已经落盘且内容不可变的帧记录。
                checkpoint_path: 当前步骤对应的检查点目录；未生成检查点时为 `None`。

            返回:
                返回函数计算得到的结果。
            """
            version = result_projection.commit_step(
                result,
                frame=frame,
                checkpoint_path=checkpoint_path,
            )
            trace_projection.project(
                run_id=str(result.run_id),
                attempt_id=str(result.attempt_id),
                relative_path=relative_trace_path,
            )
            return version

    runner.committer = FileStepCommitter(
        FrameStore(runner.context.paths),
        StepAndTraceProjection(),
        checkpoint,
    )


def main(argv=None) -> int:
    """解析启动参数并执行当前模块的主流程。

    参数:
        argv: 命令行参数序列；为 `None` 时读取当前进程的命令行。 默认值：`None`。

    返回:
        返回计算得到的整数值或版本号。

    异常:
        RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
    """
    args = _parser().parse_args(argv)
    var_dir = Path(args.var_dir).resolve()
    database = create_database(args.database_url)
    repository = LocalRunSchedulerRepository(database)
    paths = RunPaths.under(var_dir, args.run_id)
    paths.ensure()
    worker_lock = FileLock(str(paths.worker_lock), timeout=0)
    worker_lock.acquire()
    control = RunControl()
    stop_monitor = threading.Event()
    monitor = threading.Thread(
        target=_control_monitor,
        args=(
            repository,
            str(args.run_id),
            str(args.attempt_id),
            control,
            stop_monitor,
        ),
        name="run-control-monitor",
        daemon=True,
    )
    exit_code = 1
    worker_error_code: str | None = None
    worker_error_message: str | None = None
    logger = _logger(args.run_id, "INFO")
    recorder: ModelTraceWriter | None = None
    try:
        # 导入旧仿真引擎前先续租持久化所有权；这些导入在 Windows 上可能超过常规心跳期限。
        if repository.heartbeat(str(args.run_id), str(args.attempt_id)) is None:
            raise RuntimeError("worker does not own the current Run attempt")
        monitor.start()

        manifest = RunManifestStore(paths).load_verified()
        definition = manifest.definition
        logger = _logger(args.run_id, definition.simulation.log_level)
        cipher = SecretCipher(MasterKeyStore(var_dir).load_or_create())
        recorder = ModelTraceWriter(
            paths,
            run_id=args.run_id,
            attempt_id=args.attempt_id,
            attempt_no=_attempt_no(database, str(args.attempt_id)),
            capture_payloads=definition.results.capture_model_payloads,
        )
        chat_config = definition.models.chat.model_dump(mode="json", exclude_none=False)
        chat_config["api_key"] = _secret_value(definition, "chat", cipher, database)
        embedding_key = _secret_value(definition, "embedding", cipher, database)
        skills = SnapshotSkillInstructionRepository(
            manifest.skill_bundle,
            brain=str(
                manifest.document.get("brain_skill") or definition.engine.brain_skill
            ),
        )
        checkpoint_state, checkpoint_conversation, attempt_storage = (
            _prepare_attempt_state(
                database,
                paths,
                run_id=str(args.run_id),
                attempt_id=str(args.attempt_id),
                start_step=args.start_step,
                stride_minutes=definition.simulation.stride_minutes,
            )
        )
        start_time = (
            datetime.fromisoformat(checkpoint_state["virtual_time"])
            + timedelta(minutes=definition.simulation.stride_minutes)
            if checkpoint_state is not None
            else definition.simulation.start_time
        )
        simulation_clock = SimulationClock(start_time)
        memory_stream = MemoryStream(
            attempt_storage.parent
            / "runtime-storage"
            / "skill-memory"
            / "memory.sqlite",
            run_id=args.run_id,
            attempt_id=args.attempt_id,
            clock=lambda: simulation_clock.get_date(),
        )
        context = SimulationContext(
            run_id=args.run_id,
            experiment_id=UUID(manifest.document["experiment_id"]),
            revision_id=UUID(manifest.document["revision_id"]),
            attempt_id=args.attempt_id,
            definition_hash=manifest.document["definition_hash"],
            algorithm=get_algorithm_profile(definition.engine.algorithm_version),
            clock=simulation_clock,
            random=random.Random(definition.simulation.random_seed),
            paths=paths,
            skills=skills,
            models=ModelFactoryRegistry(
                chat_config,
                recorder,
                control=control,
                logger=logger,
            ),
            control=control,
            logger=logger,
            passive_skills=SnapshotPassiveSkillRuntime(manifest.skill_bundle),
            memory_stream=memory_stream,
            skill_mcp=SkillMCPServer(memory_stream),
            metadata={
                "model_trace": recorder,
                "manifest_hash": manifest.manifest_hash,
                "execution_mode": "SKILL_BRAIN",
                "brain_skill": skills.brain,
            },
        )
        # 昂贵的世界引擎导入必须放在有效心跳之后；认知调用从当前运行选定的文件化大脑解析。
        from generative_agents.start import build_runner

        runner = build_runner(
            context,
            definition,
            embedding_api_key=embedding_key,
            checkpoint_state=checkpoint_state,
            checkpoint_conversation=checkpoint_conversation,
            storage_root=attempt_storage,
        )
        runner.completed_steps = args.start_step - 1
        _install_sqlite_committer(
            runner,
            database,
            var_dir,
            checkpoint_retention=definition.simulation.checkpoint_retention,
            trace_writer=recorder,
        )
        with database.session_factory() as session:
            run_record = session.get(Run, str(args.run_id))
            if run_record is None:
                raise RuntimeError("Run disappeared before execution")
            requested_steps = run_record.requested_steps
        remaining = requested_steps - runner.completed_steps
        if remaining > 0:
            runner.run(remaining, stride_minutes=definition.simulation.stride_minutes)
        exit_code = 0
    except Exception as exc:
        worker_error_code = getattr(exc, "code", "WORKER_EXECUTION_FAILED")
        worker_error_message = str(exc) or exc.__class__.__name__
        logger.exception("worker attempt failed")
    finally:
        stop_monitor.set()
        if monitor.is_alive():
            monitor.join(timeout=2)
        if recorder is not None and recorder.path.is_file():
            try:
                ModelTraceProjector(database, var_dir=var_dir).project(
                    run_id=str(args.run_id),
                    attempt_id=str(args.attempt_id),
                    relative_path=recorder.path.resolve()
                    .relative_to(var_dir)
                    .as_posix(),
                )
            except Exception:
                exit_code = 1
                if worker_error_code is None:
                    worker_error_code = "MODEL_TRACE_PROJECTION_FAILED"
                    worker_error_message = "final model trace projection failed"
                logger.exception("final model trace projection failed")
        repository.finish_worker(
            str(args.run_id),
            str(args.attempt_id),
            exit_code=exit_code,
            error_code=worker_error_code,
            error_message=worker_error_message,
        )
        worker_lock.release()
        database.close()
    return exit_code


def _attempt_no(database, attempt_id: str) -> int:
    """执行执行尝试`no`的内部处理，供当前模块或类复用。

    参数:
        database: 持久化数据库访问对象或会话工厂。
        attempt_id: 执行尝试的唯一标识，用于区分同一运行的重试或恢复批次。 类型：`str`。

    返回:
        返回计算得到的整数值或版本号。

    异常:
        RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
    """
    from generative_agents.persistence.models import RunAttempt

    with database.session_factory() as session:
        attempt = session.get(RunAttempt, attempt_id)
        if attempt is None:
            raise RuntimeError("attempt was not registered in the scheduler database")
        return attempt.attempt_no


def _prepare_attempt_state(
    database,
    paths: RunPaths,
    *,
    run_id: str,
    attempt_id: str,
    start_step: int,
    stride_minutes: int,
) -> tuple[dict | None, dict | None, Path]:
    """执行`prepare`执行尝试状态的内部处理，供当前模块或类复用。

    参数:
        database: 持久化数据库访问对象或会话工厂。
        paths: 传入当前算法的`paths`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`RunPaths`。
        run_id: 仿真运行的唯一标识。 类型：`str`。
        attempt_id: 执行尝试的唯一标识，用于区分同一运行的重试或恢复批次。 类型：`str`。
        start_step: 读取、导出或处理范围的起始仿真步编号。 类型：`int`。
        stride_minutes: 每个仿真步推进的虚拟分钟数。 类型：`int`。

    返回:
        返回目标文件或目录路径。 没有可用结果时返回 `None`。

    异常:
        RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
        ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
    """

    if start_step < 1:
        raise ValueError("start_step must be positive")
    with database.session_factory() as session:
        run = session.get(Run, run_id)
        if run is None or run.current_attempt_id != attempt_id:
            raise RuntimeError("worker does not own the current Run attempt")
        if start_step != run.recoverable_step + 1:
            raise RuntimeError("attempt start_step does not match recoverable_step")

    attempt_root = paths.root / "attempts" / attempt_id
    storage_root = attempt_root / "storage"
    if attempt_root.exists():
        raise RuntimeError("attempt work directory already exists")
    if start_step == 1:
        storage_root.mkdir(parents=True, exist_ok=False)
        return None, None, storage_root

    reader = CheckpointBundleWriter(
        paths,
        lambda _: CheckpointSnapshot(state={}, conversation={}),
    )
    expected_step = start_step - 1
    with reader.access():
        checkpoint = reader.select_for_recovery(
            expected_step,
            orphan_root=paths.orphaned / f"attempt-{attempt_id}" / "checkpoints",
        )
        bundle = json.loads(
            (checkpoint.path / "bundle.json").read_text(encoding="utf-8")
        )
        storage_root.mkdir(parents=True, exist_ok=False)
        state = json.loads((checkpoint.path / "state.json").read_text(encoding="utf-8"))
        conversation = json.loads(
            (checkpoint.path / "conversation.json").read_text(encoding="utf-8")
        )
        virtual_time = datetime.fromisoformat(state.get("virtual_time", ""))
        if virtual_time.tzinfo is None:
            raise RuntimeError("checkpoint virtual_time is not timezone-aware")
        source_storage = checkpoint.path / "storage"
        if source_storage.exists():
            for agent_dir in source_storage.iterdir():
                if not agent_dir.is_dir() or agent_dir.is_symlink():
                    raise RuntimeError("checkpoint storage contains an unsafe member")
                shutil.copytree(agent_dir, storage_root / agent_dir.name)
        source_runtime_storage = checkpoint.path / "runtime-storage"
        if source_runtime_storage.exists():
            target_runtime_storage = attempt_root / "runtime-storage"
            for storage_dir in source_runtime_storage.iterdir():
                if not storage_dir.is_dir() or storage_dir.is_symlink():
                    raise RuntimeError(
                        "checkpoint runtime storage contains an unsafe member"
                    )
                target_runtime_storage.mkdir(parents=True, exist_ok=True)
                shutil.copytree(storage_dir, target_runtime_storage / storage_dir.name)
    return state, conversation, storage_root


def _control_monitor(
    repository: LocalRunSchedulerRepository,
    run_id: str,
    attempt_id: str,
    control: RunControl,
    stop: threading.Event,
) -> None:
    """续租工作进程所有权，并把持久化控制状态转换为本地控制信号。

    参数:
        repository: 传入当前算法的`repository`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`LocalRunSchedulerRepository`。
        run_id: 仿真运行的唯一标识。 类型：`str`。
        attempt_id: 执行尝试的唯一标识，用于区分同一运行的重试或恢复批次。 类型：`str`。
        control: 运行控制器，用于在安全边界检测暂停、取消或终止请求。 类型：`RunControl`。
        stop: 用于通知后台监控线程退出的线程事件。 类型：`threading.Event`。

    返回:
        无返回值。

    说明:
        监控线程只设置进程内控制信号，不直接提交仿真结果；最后一个可见步骤仍由提交器按固定顺序发布。
    """
    while not stop.wait(0.5):
        status = repository.heartbeat(run_id, attempt_id)
        if status is None:
            control.request_cancel()
            return
        if status == RunStatus.PAUSE_REQUESTED:
            control.request_pause()
        elif status == RunStatus.CANCEL_REQUESTED:
            control.request_cancel()


if __name__ == "__main__":
    raise SystemExit(main())
