"""Build replay/report artifacts strictly from one Run's manifest and frames."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from generative_agents.runtime.context import RunPaths
from generative_agents.runtime.manifest import RunManifestStore, VerifiedRunManifest
from generative_agents.runtime.replay_v2 import build_replay_v2
from generative_agents.runtime.results import StepResult
from generative_agents.status import ArtifactSourceKind

_FRAME_NAME = re.compile(r"^step-([0-9]{6})\.json\.gz$")
_SAFE_ARTIFACT = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")


@dataclass(frozen=True, slots=True)
class BuiltArtifact:
    """压缩任务生成的产物类型、文件路径和内容摘要。"""

    path: Path
    sha256: str
    size_bytes: int
    source_step: int
    source_kind: str = ArtifactSourceKind.DERIVED.value


def _atomic_write(path: Path, content: bytes) -> None:
    """执行`atomic``write`的内部处理，供当前模块或类复用。

    参数:
        path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。
        content: 待解析、写入、哈希或发送给下游组件的正文内容。 类型：`bytes`。

    返回:
        无返回值。
    """
    temporary = path.with_name(f".{path.name}-{uuid4()}.tmp")
    try:
        with temporary.open("xb") as file_handle:
            file_handle.write(content)
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_frames(paths: RunPaths) -> list[dict]:
    """只读取已被提交投影确认、可以对外暴露的仿真帧。

    参数:
        paths: 传入当前算法的`paths`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`RunPaths`。

    返回:
        返回以字段名或业务键组织的结构化映射。

    异常:
        RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
        ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
    """

    projection_path = paths.root / "projection.json"
    if not projection_path.is_file():
        raise RuntimeError(
            "standalone compression requires projection.json; database-backed "
            "runs must use the artifact job pipeline"
        )
    projection = json.loads(projection_path.read_text(encoding="utf-8"))
    if projection.get("run_id") != str(paths.run_id):
        raise ValueError("projection belongs to another run")
    available_step = int(projection.get("available_step") or 0)
    committed = projection.get("steps") or {}
    frames: list[tuple[int, dict]] = []
    for step_no in range(1, available_step + 1):
        record = committed.get(str(step_no))
        if not isinstance(record, dict):
            raise ValueError(f"projection is missing committed step {step_no}")
        frame_path = (paths.root / str(record.get("frame") or "")).resolve()
        if (
            frame_path.parent != paths.frames.resolve()
            or not _FRAME_NAME.fullmatch(frame_path.name)
            or not frame_path.is_file()
            or frame_path.is_symlink()
        ):
            raise ValueError(f"unsafe committed frame path at step {step_no}")
        digest = hashlib.sha256(frame_path.read_bytes()).hexdigest()
        if digest != record.get("frame_sha256"):
            raise ValueError(f"committed frame hash mismatch at step {step_no}")
        with gzip.open(frame_path, "rt", encoding="utf-8") as file_handle:
            document = json.load(file_handle)
        result = document.get("result")
        if not isinstance(result, dict):
            raise ValueError(f"frame has no result: {frame_path.name}")
        if (
            result.get("run_id") != str(paths.run_id)
            or result.get("step_no") != step_no
        ):
            raise ValueError(f"frame ownership mismatch: {frame_path.name}")
        frames.append((step_no, result))
    return [result for _step, result in frames]


def build_replay(
    paths: RunPaths,
    manifest: VerifiedRunManifest,
    *,
    logical_name: str = "replay-v2.json",
) -> BuiltArtifact:
    """构建`replay`。

    参数:
        paths: 传入当前算法的`paths`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`RunPaths`。
        manifest: 已经构建或验证的不可变运行清单。 类型：`VerifiedRunManifest`。
        logical_name: 产物或资源在业务层使用的稳定逻辑名称。 类型：`str`。 默认值：`'replay-v2.json'`。

    返回:
        返回 `BuiltArtifact` 类型的处理结果。

    异常:
        ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
    """
    if not _SAFE_ARTIFACT.fullmatch(logical_name):
        raise ValueError("unsafe artifact logical_name")
    if manifest.document.get("run_id") != str(paths.run_id):
        raise ValueError("manifest belongs to another run")
    frames = _read_frames(paths)
    results = [StepResult.from_dict(frame) for frame in frames]
    checkpoint_steps = {
        int(path.name[5:11])
        for path in paths.checkpoints.glob("step-[0-9][0-9][0-9][0-9][0-9][0-9]")
        if path.is_dir() and not path.is_symlink()
    }
    document = build_replay_v2(
        run_id=str(paths.run_id),
        revision_id=str(manifest.document["revision_id"]),
        definition_hash=str(manifest.document["definition_hash"]),
        definition=manifest.definition,
        source_step=len(results),
        partial=len(results) < manifest.definition.simulation.max_steps,
        results=results,
        checkpoint_steps=checkpoint_steps,
    )
    content = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    paths.ensure()
    target = paths.artifacts / logical_name
    _atomic_write(target, content)
    return BuiltArtifact(
        path=target,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        source_step=len(frames),
    )


def build_report(
    paths: RunPaths,
    manifest: VerifiedRunManifest,
    *,
    logical_name: str = "simulation.md",
) -> BuiltArtifact:
    """构建`report`。

    参数:
        paths: 传入当前算法的`paths`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`RunPaths`。
        manifest: 已经构建或验证的不可变运行清单。 类型：`VerifiedRunManifest`。
        logical_name: 产物或资源在业务层使用的稳定逻辑名称。 类型：`str`。 默认值：`'simulation.md'`。

    返回:
        返回 `BuiltArtifact` 类型的处理结果。

    异常:
        ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
    """
    if not _SAFE_ARTIFACT.fullmatch(logical_name):
        raise ValueError("unsafe artifact logical_name")
    frames = _read_frames(paths)
    conversation_count = sum(len(frame.get("conversations", [])) for frame in frames)
    memory_count = sum(len(frame.get("memory_deltas", [])) for frame in frames)
    lines = [
        f"# {manifest.definition.experiment.name}",
        "",
        f"- Run: `{paths.run_id}`",
        f"- Revision: `{manifest.document['revision_id']}`",
        f"- Definition hash: `{manifest.document['definition_hash']}`",
        f"- Committed steps: {len(frames)}",
        f"- Conversations: {conversation_count}",
        f"- Memory events: {memory_count}",
        "",
        "> 本报告只统计已提交 frame 中的结构化事实。",
        "",
    ]
    content = "\n".join(lines).encode("utf-8")
    paths.ensure()
    target = paths.artifacts / logical_name
    _atomic_write(target, content)
    return BuiltArtifact(
        path=target,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        source_step=len(frames),
    )


def build_parser() -> argparse.ArgumentParser:
    """构建`parser`。

    返回:
        返回 `argparse.ArgumentParser` 类型的处理结果。
    """
    parser = argparse.ArgumentParser(
        description="build an artifact for one isolated Run"
    )
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--artifact", choices=("replay", "report"), required=True)
    return parser


def main(argv=None) -> int:
    """解析启动参数并执行当前模块的主流程。

    参数:
        argv: 命令行参数序列；为 `None` 时读取当前进程的命令行。 默认值：`None`。

    返回:
        返回计算得到的整数值或版本号。
    """
    args = build_parser().parse_args(argv)
    paths = RunPaths.under(args.data_root, args.run_id)
    manifest = RunManifestStore(paths).load_verified()
    artifact = (
        build_replay(paths, manifest)
        if args.artifact == "replay"
        else build_report(paths, manifest)
    )
    print(artifact.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
