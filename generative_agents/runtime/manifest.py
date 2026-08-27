"""生成并校验 Worker 消费的不可变运行清单。

清单冻结实验定义、Skill 快照、依赖版本和算法身份。Worker 启动或恢复时必须验证
内容哈希，不能悄悄改读当前草稿或主机上的新版 Skill。
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import UUID, uuid4

from generative_agents.config import (
    ExperimentDefinition,
    canonical_json_bytes,
    definition_hash,
    get_algorithm_profile,
)

from .context import RunPaths


MANIFEST_SCHEMA_VERSION = 2


class ManifestConflictError(RuntimeError):
    """磁盘清单与期望的 Revision、算法或内容哈希不一致。"""

    pass


@dataclass(frozen=True, slots=True)
class VerifiedRunManifest:
    """已通过结构和哈希校验、可安全交给 Worker 的清单视图。"""

    path: Path
    manifest_hash: str
    document: Mapping[str, Any]

    @property
    def definition(self) -> ExperimentDefinition:
        """执行 `VerifiedRunManifest` 的仿真定义操作。

        返回:
            返回 `ExperimentDefinition` 类型的处理结果。
        """
        return ExperimentDefinition.model_validate(self.document["definition"])

    @property
    def skill_bundle(self) -> Mapping[str, Mapping[str, Any]]:
        """执行 `VerifiedRunManifest` 的技能`bundle`操作。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        value = self.document.get("skill_bundle")
        if not isinstance(value, Mapping):
            raise ValueError("run manifest has no Skill bundle")
        return value


def skill_bundle_hash(skills: Mapping[str, Mapping[str, Any]]) -> str:
    """执行 的技能`bundle`哈希值操作。

    参数:
        skills: 当前智能体可调用的技能指令仓库或执行器集合。 类型：`Mapping[str, Mapping[str, Any]]`。

    返回:
        返回处理后的文本或稳定标识。
    """
    normalized = {str(key): dict(value) for key, value in sorted(skills.items())}
    return hashlib.sha256(canonical_json_bytes(normalized)).hexdigest()


def collect_dependency_versions(
    distributions: Iterable[str] = (
        "pydantic",
        "SQLAlchemy",
        "fastapi",
        "llama-index-core",
        "openai",
    ),
) -> dict[str, str | None]:
    """执行 的`collect``dependency``versions`操作。

    参数:
        distributions: 随机选择使用的候选项概率分布。 类型：`Iterable[str]`。

    返回:
        返回以字段名或业务键组织的结构化映射。 没有可用结果时返回 `None`。
    """
    versions: dict[str, str | None] = {}
    for distribution in distributions:
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = None
    return versions


def build_manifest_document(
    *,
    run_id: UUID,
    experiment_id: UUID,
    revision_id: UUID,
    definition: ExperimentDefinition,
    expected_definition_hash: str,
    code_build_id: str,
    assets: Iterable[Mapping[str, Any]],
    materialized_at: datetime,
    dependency_versions: Mapping[str, str | None] | None = None,
    skill_bundle: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """构建运行清单`document`。

    参数:
        run_id: 仿真运行的唯一标识。 类型：`UUID`。
        experiment_id: 实验记录的唯一标识。 类型：`UUID`。
        revision_id: 实验修订版本的唯一标识。 类型：`UUID`。
        definition: 已校验的仿真定义，描述地图、智能体、模型与执行参数。 类型：`ExperimentDefinition`。
        expected_definition_hash: 调用方期望的已发布定义哈希，用于拒绝输入漂移。 类型：`str`。
        code_build_id: `code``build`的唯一标识。 类型：`str`。
        assets: 当前运行清单引用的不可变资源集合。 类型：`Iterable[Mapping[str, Any]]`。
        materialized_at: `materialized`对应的时间点。 类型：`datetime`。
        dependency_versions: 运行依赖名称到冻结版本号的映射。 类型：`Mapping[str, str | None] | None`。 默认值：`None`。
        skill_bundle: 当前运行冻结使用的技能指令与版本信息集合。 类型：`Mapping[str, Mapping[str, Any]]`。

    返回:
        返回以字段名或业务键组织的结构化映射。

    异常:
        ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
    """
    actual_definition_hash = definition_hash(definition)
    if actual_definition_hash != expected_definition_hash:
        raise ValueError(
            "revision definition_hash does not match normalized definition"
        )
    if materialized_at.tzinfo is None:
        raise ValueError("materialized_at must be timezone-aware")
    if not code_build_id.strip():
        raise ValueError("code_build_id must not be empty")
    algorithm_version = definition.engine.algorithm_version
    get_algorithm_profile(algorithm_version)
    asset_list = sorted(
        (dict(asset) for asset in assets),
        key=lambda item: (
            str(item.get("logical_path", "")),
            str(item.get("asset_hash", "")),
        ),
    )
    envelope: dict[str, Any] = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "run_id": str(run_id),
        "experiment_id": str(experiment_id),
        "revision_id": str(revision_id),
        "definition_hash": actual_definition_hash,
        "definition": definition.model_dump(mode="json", exclude_none=False),
        "algorithm_version": algorithm_version,
        "code_build_id": code_build_id.strip(),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "dependency_versions": dict(
            dependency_versions
            if dependency_versions is not None
            else collect_dependency_versions()
        ),
        "assets": asset_list,
        "materialized_at": materialized_at.isoformat(),
        "brain_skill": definition.engine.brain_skill,
    }
    skills = {str(key): dict(value) for key, value in sorted(skill_bundle.items())}
    envelope["skill_bundle"] = skills
    envelope["skill_bundle_hash"] = skill_bundle_hash(skills)
    envelope["manifest_hash"] = hashlib.sha256(
        canonical_json_bytes(envelope)
    ).hexdigest()
    return envelope


class RunManifestStore:
    """在 Run 私有目录中原子写入、复用和验证运行清单。"""

    def __init__(self, paths: RunPaths):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            paths: 传入当前算法的`paths`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`RunPaths`。

        返回:
            无返回值。
        """
        self._paths = paths
        self._paths.ensure()

    def materialize(self, document: Mapping[str, Any]) -> VerifiedRunManifest:
        """执行 `RunManifestStore` 的`materialize`操作。

        参数:
            document: 待校验、转换或持久化的结构化文档。 类型：`Mapping[str, Any]`。

        返回:
            返回 `VerifiedRunManifest` 类型的处理结果。

        异常:
            ManifestConflictError: 当底层操作报告该异常条件时抛出。
        """
        self._verify_document(document)
        content = canonical_json_bytes(document)
        target = self._paths.manifest
        if target.exists():
            existing = target.read_bytes()
            if existing != content:
                raise ManifestConflictError(
                    "run manifest is immutable and already differs"
                )
            return self.load_verified()
        temporary = self._paths.temporary / f"manifest-{uuid4()}.tmp"
        try:
            with temporary.open("xb") as file_handle:
                file_handle.write(content)
                file_handle.flush()
                os.fsync(file_handle.fileno())
            os.replace(temporary, target)
            self._fsync_directory(target.parent)
        finally:
            temporary.unlink(missing_ok=True)
        return self.load_verified()

    def reuse_for_revision(
        self,
        *,
        experiment_id: UUID,
        revision_id: UUID,
        definition: ExperimentDefinition,
        expected_definition_hash: str,
        assets: Iterable[Mapping[str, Any]],
        skill_bundle: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> VerifiedRunManifest:
        """核验现有运行清单是否与已发布修订版本完全一致。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`UUID`。
            revision_id: 实验修订版本的唯一标识。 类型：`UUID`。
            definition: 已校验的仿真定义，描述地图、智能体、模型与执行参数。 类型：`ExperimentDefinition`。
            expected_definition_hash: 调用方期望的已发布定义哈希，用于拒绝输入漂移。 类型：`str`。
            assets: 当前运行清单引用的不可变资源集合。 类型：`Iterable[Mapping[str, Any]]`。
            skill_bundle: 当前运行冻结使用的技能指令与版本信息集合。 类型：`Mapping[str, Mapping[str, Any]] | None`。 默认值：`None`。

        返回:
            返回 `VerifiedRunManifest` 类型的处理结果。

        异常:
            ManifestConflictError: 当底层操作报告该异常条件时抛出。

        说明:
            清单一旦绑定运行便视为不可变；发现修订版本或资源哈希不一致时必须失败，不能就地覆盖。
        """

        verified = self.load_verified()
        actual_definition_hash = definition_hash(definition)
        if actual_definition_hash != expected_definition_hash:
            raise ManifestConflictError(
                "published Revision definition no longer matches its definition hash"
            )
        expected_assets = sorted(
            (dict(asset) for asset in assets),
            key=lambda item: (
                str(item.get("logical_path", "")),
                str(item.get("asset_hash", "")),
            ),
        )
        document = verified.document
        matches = (
            document.get("experiment_id") == str(experiment_id)
            and document.get("revision_id") == str(revision_id)
            and document.get("definition_hash") == actual_definition_hash
            and document.get("definition")
            == definition.model_dump(mode="json", exclude_none=False)
            and document.get("algorithm_version") == definition.engine.algorithm_version
            and document.get("assets") == expected_assets
        )
        if not matches:
            raise ManifestConflictError(
                "run manifest does not match the claimed published Revision"
            )
        return verified

    def exists(self) -> bool:
        """执行 `RunManifestStore` 的`exists`操作。

        返回:
            条件成立时返回 `True`，否则返回 `False`。
        """
        target = self._paths.manifest
        return target.exists() or target.is_symlink()

    def load_verified(self) -> VerifiedRunManifest:
        """加载`verified`。

        返回:
            返回 `VerifiedRunManifest` 类型的处理结果。
        """
        target = self._paths.manifest
        with target.open("r", encoding="utf-8") as file_handle:
            document = json.load(file_handle)
        manifest_hash = self._verify_document(document)
        return VerifiedRunManifest(
            path=target,
            manifest_hash=manifest_hash,
            document=document,
        )

    def _verify_document(self, document: Mapping[str, Any]) -> str:
        """验证`document`。

        参数:
            document: 待校验、转换或持久化的结构化文档。 类型：`Mapping[str, Any]`。

        返回:
            返回处理后的文本或稳定标识。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if document.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION:
            raise ValueError("unsupported manifest schema version")
        if document.get("run_id") != str(self._paths.run_id):
            raise ValueError("manifest run_id does not own this run directory")
        expected_manifest_hash = document.get("manifest_hash")
        if not isinstance(expected_manifest_hash, str):
            raise ValueError("manifest_hash is missing")
        unsigned = dict(document)
        unsigned.pop("manifest_hash", None)
        actual_manifest_hash = hashlib.sha256(
            canonical_json_bytes(unsigned)
        ).hexdigest()
        if actual_manifest_hash != expected_manifest_hash:
            raise ValueError("manifest_hash mismatch")
        definition = ExperimentDefinition.model_validate(document.get("definition"))
        if definition_hash(definition) != document.get("definition_hash"):
            raise ValueError("manifest definition_hash mismatch")
        algorithm_version = document.get("algorithm_version")
        if algorithm_version != definition.engine.algorithm_version:
            raise ValueError("manifest algorithm_version mismatch")
        get_algorithm_profile(algorithm_version)
        skills = document.get("skill_bundle")
        digest = document.get("skill_bundle_hash")
        if not isinstance(skills, Mapping) or not skills:
            raise ValueError("manifest Skill bundle is missing")
        if document.get("brain_skill") not in skills:
            raise ValueError("manifest brain Skill is missing from the Skill bundle")
        if skill_bundle_hash(skills) != digest:
            raise ValueError("manifest skill_bundle_hash mismatch")
        return actual_manifest_hash

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        """执行`fsync``directory`的内部处理，供当前模块或类复用。

        参数:
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。

        返回:
            无返回值。
        """
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
