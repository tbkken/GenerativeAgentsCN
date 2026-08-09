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

_FRAME_NAME = re.compile(r"^step-([0-9]{6})\.json\.gz$")
_SAFE_ARTIFACT = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")


@dataclass(frozen=True, slots=True)
class BuiltArtifact:
    path: Path
    sha256: str
    size_bytes: int
    source_step: int
    source_kind: str = "DERIVED"


def _atomic_write(path: Path, content: bytes) -> None:
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
    frames: list[tuple[int, dict]] = []
    for frame_path in paths.frames.iterdir():
        match = _FRAME_NAME.fullmatch(frame_path.name)
        if not match or not frame_path.is_file():
            continue
        step_no = int(match.group(1))
        with gzip.open(frame_path, "rt", encoding="utf-8") as file_handle:
            document = json.load(file_handle)
        result = document.get("result")
        if not isinstance(result, dict):
            raise ValueError(f"frame has no result: {frame_path.name}")
        if result.get("run_id") != str(paths.run_id) or result.get("step_no") != step_no:
            raise ValueError(f"frame ownership mismatch: {frame_path.name}")
        frames.append((step_no, result))
    frames.sort(key=lambda item: item[0])
    for expected, (actual, _result) in enumerate(frames, 1):
        if actual != expected:
            raise ValueError(f"canonical frames are not contiguous at step {expected}")
    return [result for _step, result in frames]


def build_replay(
    paths: RunPaths,
    manifest: VerifiedRunManifest,
    *,
    logical_name: str = "replay-v2.json",
) -> BuiltArtifact:
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
    parser = argparse.ArgumentParser(description="build an artifact for one isolated Run")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--artifact", choices=("replay", "report"), required=True)
    return parser


def main(argv=None) -> int:
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
