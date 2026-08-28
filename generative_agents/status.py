"""系统跨模块共享的持久化状态枚举与状态集合。

这些枚举的 ``value`` 是数据库、API 和运行目录之间的稳定协议值。业务代码应引用
枚举成员，只有在 SQL 文本、JSON Schema 或其他纯字符串边界才读取 ``.value``，
避免同一状态散落为无法统一修改的魔法字符串。
"""

from __future__ import annotations

from enum import StrEnum
from collections.abc import Iterable
from typing import TypeVar


class ExperimentStatus(StrEnum):
    """实验聚合状态；它是最新草稿和最新 Run 状态的面向产品投影。"""

    DRAFT = "DRAFT"  # 存在可编辑草稿，当前没有更高优先级的运行状态。
    QUEUED = "QUEUED"  # 最新 Run 正在等待本机仿真槽位。
    RUNNING = "RUNNING"  # 最新 Run 正在启动、执行或处理停止请求。
    PAUSED = "PAUSED"  # 最新 Run 已在可恢复边界暂停。
    COMPLETED = "COMPLETED"  # 最新 Run 已完成全部请求步骤且没有新草稿。
    CANCELLED = "CANCELLED"  # 最新 Run 已取消且没有新草稿。
    FAILED = "FAILED"  # 最新 Run 失败或中断且没有新草稿。


class ExperimentStatusFilter(StrEnum):
    """实验列表 API 接受的状态筛选值。"""

    DRAFT = ExperimentStatus.DRAFT.value  # 仅草稿态实验。
    QUEUED = ExperimentStatus.QUEUED.value  # 仅排队中的实验。
    RUNNING = ExperimentStatus.RUNNING.value  # 仅运行中的实验。
    PAUSED = ExperimentStatus.PAUSED.value  # 仅暂停实验。
    COMPLETED = ExperimentStatus.COMPLETED.value  # 仅完成实验。
    CANCELLED = ExperimentStatus.CANCELLED.value  # 仅取消实验。
    FAILED = ExperimentStatus.FAILED.value  # 仅失败实验。
    ABNORMAL = "ABNORMAL"  # 产品聚合筛选：FAILED 或 CANCELLED。


class RevisionState(StrEnum):
    """实验及目录资源版本的可变性状态。"""

    DRAFT = "DRAFT"  # 仍可编辑，不能直接作为可重复执行输入。
    PUBLISHED = "PUBLISHED"  # 内容和哈希已冻结，可被 Run 引用。


class RunStatus(StrEnum):
    """一次仿真 Run 的持久化生命周期状态。"""

    QUEUED = "QUEUED"  # 已进入 FIFO 队列，尚未占用本机槽位。
    STARTING = "STARTING"  # 已分配槽位并创建 Attempt，Worker 尚未完成注册。
    RUNNING = "RUNNING"  # Worker 已登记 PID 并持有当前 Attempt。
    PAUSE_REQUESTED = "PAUSE_REQUESTED"  # 已请求软暂停，等待完整步骤边界。
    PAUSED = "PAUSED"  # Worker 已退出且保留可恢复检查点，不占用槽位。
    CANCEL_REQUESTED = "CANCEL_REQUESTED"  # 已请求取消，等待软停止或强制终止。
    CANCELLED = "CANCELLED"  # 终态；保留已提交结果但禁止原地恢复。
    COMPLETED = "COMPLETED"  # 终态；已提交 requested_steps 指定的全部步骤。
    FAILED = "FAILED"  # Worker 捕获错误或启动失败；有检查点时允许重试。
    INTERRUPTED = "INTERRUPTED"  # Worker 消失或心跳超时；有检查点时允许重试。


class RunAttemptStatus(StrEnum):
    """Run 的单次 Worker 尝试状态。"""

    SPAWNING = "SPAWNING"  # Attempt 已占用槽位，子进程尚未注册。
    RUNNING = "RUNNING"  # 子进程已注册并正在执行。
    ENDED = "ENDED"  # Attempt 已结束；结果由 stop_reason 进一步说明。


class RunQueueReason(StrEnum):
    """Run 进入 FIFO 队列的原因。"""

    NEW = "NEW"  # 首次从已发布版本创建 Run。
    RESUME = "RESUME"  # 用户恢复 PAUSED Run。
    RETRY = "RETRY"  # FAILED/INTERRUPTED 恢复，或调度器修复缺失队列行。


class AttemptStopReason(StrEnum):
    """RunAttempt 结束原因；该值用于审计，不替代 RunStatus。"""

    COMPLETED = "COMPLETED"  # 已完成 Run 请求的全部步骤。
    PAUSED = "PAUSED"  # 在步骤边界响应了软暂停。
    CANCELLED = "CANCELLED"  # 在步骤边界响应了软取消。
    FORCE_CANCELLED = "FORCE_CANCELLED"  # Supervisor 验证进程身份后强制终止。
    START_FAILED = "START_FAILED"  # Manifest 物化、进程启动或注册阶段失败。
    EARLY_EXIT = "EARLY_EXIT"  # 进程正常退出，但 completed_steps 尚未达到目标。
    WORKER_ERROR = "WORKER_ERROR"  # Worker 捕获异常并以非零状态退出。
    WEB_RECONCILE = "WEB_RECONCILE"  # Web 对账发现进程消失或心跳失效。


class ArtifactJobStatus(StrEnum):
    """持久化制品任务状态。"""

    QUEUED = "QUEUED"  # 等待独立 Artifact Worker。
    RUNNING = "RUNNING"  # 已被 Artifact Scheduler 认领。
    SUCCEEDED = "SUCCEEDED"  # 制品文件与数据库记录均已提交。
    FAILED = "FAILED"  # 构建失败或超过重试次数。
    CANCELLED = "CANCELLED"  # 任务被取消，不再调度。


class ArtifactState(StrEnum):
    """RunArtifact 文件记录状态。"""

    BUILDING = "BUILDING"  # 文件正在生成，尚不可下载。
    READY = "READY"  # 文件哈希和元数据已验证，可读取。
    FAILED = "FAILED"  # 对应构建失败，记录保留用于诊断。
    STALE = "STALE"  # Run 投影已回退，制品引用了旧结果分支。


class ArtifactJobType(StrEnum):
    """支持的派生制品任务类型。"""

    BUILD_REPLAY = "BUILD_REPLAY"  # 构建前端可播放的 Replay V2 JSON。
    RESULT_BUNDLE = "RESULT_BUNDLE"  # 打包锁定结果边界内的完整结果。
    FILTERED_MEMORIES = "FILTERED_MEMORIES"  # 导出满足筛选条件的记忆。
    FILTERED_CONVERSATIONS = "FILTERED_CONVERSATIONS"  # 导出满足筛选条件的会话。
    CHECKPOINT_BUNDLE = "CHECKPOINT_BUNDLE"  # 受控打包检查点及其存储。
    BUILD_REPORT = "BUILD_REPORT"  # 构建 Markdown 运行报告。


class ArtifactType(StrEnum):
    """已生成 RunArtifact 的内容类型。"""

    REPLAY = "REPLAY"  # Replay V2 JSON。
    REPORT = "REPORT"  # Markdown 运行报告。
    MEMORY_EXPORT = "MEMORY_EXPORT"  # 筛选后的记忆 JSON。
    CONVERSATION_EXPORT = "CONVERSATION_EXPORT"  # 筛选后的会话 JSON。
    CHECKPOINT_BUNDLE = "CHECKPOINT_BUNDLE"  # 检查点 ZIP 包。
    RESULT_BUNDLE = "RESULT_BUNDLE"  # 结果 ZIP 包。


class ArtifactSourceKind(StrEnum):
    """制品内容相对于仿真提交事实的来源类型。"""

    RAW = "RAW"  # Worker 直接提交的原始事实。
    DERIVED = "DERIVED"  # 从锁定 source_step 的原始事实派生。


class ArtifactScope(StrEnum):
    """产物相对于运行目标步骤的结果范围。"""

    PARTIAL = "PARTIAL"  # 产物基于尚未达到 requested_steps 的已提交边界。
    FINAL = "FINAL"  # 产物基于完整结果边界。


class ModelProbeState(StrEnum):
    """模型连接探测状态。"""

    UNTESTED = "UNTESTED"  # 当前配置尚未探测。
    CHECKING = "CHECKING"  # 探测请求执行中。
    ONLINE = "ONLINE"  # 最近一次探测成功且配置未变化。
    OFFLINE = "OFFLINE"  # 最近一次探测失败。
    STALE = "STALE"  # 探测后配置发生变化，旧结论不可作为发布依据。


class ResultCompleteness(StrEnum):
    """Run 查询结果相对于请求步数的完整性。"""

    EMPTY = "EMPTY"  # 尚无任何成功投影的步骤。
    PARTIAL = "PARTIAL"  # 至少有一步可读，但尚未达到 requested_steps。
    COMPLETE = "COMPLETE"  # available_step 已达到 requested_steps。
    CORRUPTED = "CORRUPTED"  # 已检测到不可安全读取的结果完整性错误。


class MemoryState(StrEnum):
    """运行记忆在持久化存储中的生命周期状态。"""

    ACTIVE = "ACTIVE"  # 记忆仍可被检索、访问和用于智能体决策。
    EXPIRED = "EXPIRED"  # 记忆已到达过期条件，不再参与后续检索。
    EVICTED = "EVICTED"  # 记忆因容量或保留策略被淘汰。
    SUPERSEDED = "SUPERSEDED"  # 记忆已被一个更正或更新版本替代。
    INVALIDATED = "INVALIDATED"  # 记忆被明确判定为无效，不再参与检索。


class MemoryDeltaKind(StrEnum):
    """单步结果中记录的记忆变化类型。"""

    CREATED = "CREATED"  # 当前步骤创建了一条新记忆。
    ACCESSED = "ACCESSED"  # 当前步骤读取并使用了一条现有记忆。
    EXPIRED = "EXPIRED"  # 当前步骤确认记忆自然过期。
    EVICTED = "EVICTED"  # 当前步骤按保留策略淘汰记忆。
    SUPERSEDED = "SUPERSEDED"  # 当前步骤用一个新记忆版本替代旧版本。
    INVALIDATED = "INVALIDATED"  # 当前步骤明确撤销一条错误或失效记忆。


class MemorySnapshotState(StrEnum):
    """按历史步骤导出记忆时使用的简化可见状态。"""

    ACTIVE = "ACTIVE"  # 在指定 source_step 仍然有效。
    REMOVED = "REMOVED"  # 在指定 source_step 已经过期或被淘汰。


OPEN_RUN_STATUSES = frozenset(
    {
        RunStatus.QUEUED,
        RunStatus.STARTING,
        RunStatus.RUNNING,
        RunStatus.PAUSE_REQUESTED,
        RunStatus.PAUSED,
        RunStatus.CANCEL_REQUESTED,
    }
)
"""同一实验至多允许存在一个的开放 Run 状态集合。"""

SLOT_OWNING_RUN_STATUSES = frozenset(
    {
        RunStatus.STARTING,
        RunStatus.RUNNING,
        RunStatus.PAUSE_REQUESTED,
        RunStatus.CANCEL_REQUESTED,
    }
)
"""必须同时持有 ``slot_no`` 和 ``current_attempt_id`` 的 Run 状态。"""

WORKER_OWNED_RUN_STATUSES = frozenset(
    {
        RunStatus.RUNNING,
        RunStatus.PAUSE_REQUESTED,
        RunStatus.CANCEL_REQUESTED,
    }
)
"""必须登记 Worker PID 且允许心跳续租的 Run 状态。"""

RESUMABLE_RUN_STATUSES = frozenset(
    {RunStatus.PAUSED, RunStatus.FAILED, RunStatus.INTERRUPTED}
)
"""在 ``recoverable_step`` 有效时允许重新入队的 Run 状态。"""

TERMINAL_RUN_STATUSES = frozenset({RunStatus.COMPLETED, RunStatus.CANCELLED})
"""不允许原地恢复的 Run 终态。"""

ACTIVE_ARTIFACT_JOB_STATUSES = frozenset(
    {ArtifactJobStatus.QUEUED, ArtifactJobStatus.RUNNING}
)
"""用于制品任务幂等约束的非终态集合。"""


_EnumT = TypeVar("_EnumT", bound=StrEnum)


def sql_enum_values(enum_type: type[_EnumT]) -> str:
    """把一个字符串枚举转换为可嵌入 SQL `IN` 约束的值列表。

    参数:
        enum_type: `StrEnum` 子类。成员的 `value` 必须是不含单引号的稳定协议字符串。

    返回:
        返回按字典序排列并完成单引号包裹的 SQL 片段，例如 `'DRAFT','PUBLISHED'`。

    异常:
        ValueError: 任一枚举值包含 SQL 单引号时抛出。

    说明:
        本函数只用于由代码内枚举生成数据库约束，不接受用户输入；业务查询仍应使用参数绑定。
    """

    return sql_values(member.value for member in enum_type)


def sql_values(values: Iterable[str | StrEnum]) -> str:
    """把一组受信任协议值转换为顺序稳定的 SQL 字符串列表。

    参数:
        values: 字符串或 `StrEnum` 成员的可迭代对象；每个值都必须是不含单引号的内部协议值。

    返回:
        返回去除输入顺序差异、按字典序排列的 SQL `IN` 列表内容。

    异常:
        ValueError: 任一值包含 SQL 单引号时抛出，防止生成无效或可注入的约束文本。

    说明:
        该函数服务于 SQLAlchemy 模型元数据构建，不应替代运行时查询的参数化 SQL。
    """

    normalized = sorted(str(value) for value in values)
    if any("'" in value for value in normalized):
        raise ValueError("状态值不能包含 SQL 单引号")
    return ",".join(f"'{value}'" for value in normalized)


__all__ = [
    "ACTIVE_ARTIFACT_JOB_STATUSES",
    "ArtifactJobStatus",
    "ArtifactJobType",
    "ArtifactScope",
    "ArtifactState",
    "ArtifactSourceKind",
    "ArtifactType",
    "AttemptStopReason",
    "ExperimentStatus",
    "ExperimentStatusFilter",
    "ModelProbeState",
    "MemoryDeltaKind",
    "MemorySnapshotState",
    "MemoryState",
    "OPEN_RUN_STATUSES",
    "RESUMABLE_RUN_STATUSES",
    "ResultCompleteness",
    "RevisionState",
    "RunAttemptStatus",
    "RunQueueReason",
    "RunStatus",
    "SLOT_OWNING_RUN_STATUSES",
    "TERMINAL_RUN_STATUSES",
    "WORKER_OWNED_RUN_STATUSES",
    "sql_enum_values",
    "sql_values",
]
