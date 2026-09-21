"""发现、校验、编辑并快照以 ``SKILL.md`` 为事实来源的 Agent Skill。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from generative_agents.ga_protocol.skills.dependencies import referenced_mcp_tools


SkillKind = Literal["atomic", "pack", "brain"]
_KINDS: tuple[SkillKind, ...] = ("atomic", "pack", "brain")
_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FRONTMATTER = re.compile(
    r"\A---\s*\r?\n(?P<header>.*?)\r?\n---\s*(?:\r?\n|\Z)", re.DOTALL
)
_PROMPT = re.compile(
    r"<!--\s*PROMPT:START\s*-->\s*(?P<prompt>.*?)\s*<!--\s*PROMPT:END\s*-->",
    re.DOTALL,
)
_SKILL_REFERENCE = re.compile(r"(?<![\w-])\$([a-z0-9]+(?:-[a-z0-9]+)*)")


class SkillRegistryError(ValueError):
    """A skill document is missing, unsafe, or invalid."""


@dataclass(frozen=True, slots=True)
class SkillDocument:
    """一个已解析 Skill 的元数据、正文、依赖和磁盘来源。"""

    name: str
    description: str
    kind: SkillKind
    path: Path
    markdown: str
    body: str
    prompt_template: str
    children: tuple[str, ...]
    scripts: tuple[str, ...]
    content_hash: str
    updated_at: str
    example_input: str = ""

    def summary(self) -> dict[str, object]:
        """执行 `SkillDocument` 的摘要操作。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        return {
            "name": self.name,
            "description": self.description,
            "example_input": self.example_input,
            "kind": self.kind,
            "path": self.path.as_posix(),
            "children": list(self.children),
            "scripts": list(self.scripts),
            "content_hash": self.content_hash,
            "updated_at": self.updated_at,
        }

    def detail(self) -> dict[str, object]:
        """执行 `SkillDocument` 的`detail`操作。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        return {**self.summary(), "markdown": self.markdown}


class SkillRegistry:
    """Read and validate standard Skill directories."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    @staticmethod
    def normalize_name(name: str) -> str:
        """规范化`name`。

        参数:
            name: 目标对象的人类可读名称。 类型：`str`。

        返回:
            返回处理后的文本或稳定标识。

        异常:
            SkillRegistryError: 当底层操作报告该异常条件时抛出。
        """
        normalized = str(name).strip().casefold().replace("_", "-")
        if not _NAME.fullmatch(normalized) or len(normalized) > 64:
            raise SkillRegistryError(
                "Skill name must be 1-64 lowercase letters, numbers, or hyphens"
            )
        return normalized

    def list(
        self, *, kind: SkillKind | None = None, query: str = ""
    ) -> list[SkillDocument]:
        """执行 `SkillRegistry` 的`list`操作。

        参数:
            kind: 用于选择解析、校验或执行分支的稳定类型判别值。 类型：`SkillKind | None`。 默认值：`None`。
            query: 用于名称、正文或标识模糊匹配的搜索文本。 类型：`str`。 默认值：`''`。

        返回:
            返回按接口约定组织的结果集合。
        """
        kinds = (kind,) if kind else _KINDS
        needle = query.strip().casefold()
        normalized_needle = re.sub(r"[\s_-]+", "-", needle).strip("-")
        documents: list[SkillDocument] = []
        for current_kind in kinds:
            kind_root = (
                self.root / f"{current_kind}s"
                if current_kind != "atomic"
                else self.root / "atomic"
            )
            if not kind_root.exists():
                continue
            for skill_file in sorted(kind_root.glob("*/SKILL.md")):
                document = self._read(skill_file, current_kind)
                haystack = (
                    f"{document.name}\n{document.name.replace('-', ' ')}\n"
                    f"{document.description}\n{document.body}"
                ).casefold()
                if not needle or needle in haystack or normalized_needle in document.name:
                    documents.append(document)
        return sorted(documents, key=lambda item: (item.kind, item.name))

    def get(self, name: str) -> SkillDocument:
        """执行 `SkillRegistry` 的`get`操作。

        参数:
            name: 目标对象的人类可读名称。 类型：`str`。

        返回:
            返回 `SkillDocument` 类型的处理结果。

        异常:
            SkillRegistryError: 当底层操作报告该异常条件时抛出。
        """
        normalized = self.normalize_name(name)
        for kind in _KINDS:
            path = self._skill_path(normalized, kind) / "SKILL.md"
            if path.is_file():
                return self._read(path, kind)
        raise SkillRegistryError(f"Skill does not exist: {normalized}")




    def dependencies(self, name: str) -> dict[str, object]:
        """执行 `SkillRegistry` 的`dependencies`操作。

        参数:
            name: 目标对象的人类可读名称。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        document = self.get(name)
        children = []
        for child_name in document.children:
            try:
                children.append(self.get(child_name).summary())
            except SkillRegistryError:
                children.append({"name": child_name, "missing": True})
        return {
            "skill": document.name,
            "scripts": list(document.scripts),
            "skills": children,
            "mcp": referenced_mcp_tools(document.body),
        }

    def snapshot(
        self,
        roots: Iterable[str] | None = None,
    ) -> dict[str, dict[str, object]]:
        """执行 `SkillRegistry` 的快照操作。

        参数:
            roots: 传入当前算法的`roots`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`Iterable[str] | None`。 默认值：`None`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """

        if roots is None:
            documents = self.list()
        else:
            pending = [self.normalize_name(name) for name in roots]
            selected: dict[str, SkillDocument] = {}
            while pending:
                name = pending.pop()
                if name in selected:
                    continue
                document = self.get(name)
                selected[name] = document
                pending.extend(document.children)
            documents = sorted(
                selected.values(), key=lambda item: (item.kind, item.name)
            )
        return {
            document.name: {
                "kind": document.kind,
                "description": document.description,
                "markdown": document.markdown,
                "content_hash": document.content_hash,
                "scripts": {
                    relative_path: (document.path.parent / relative_path).read_text(
                        encoding="utf-8-sig"
                    )
                    for relative_path in document.scripts
                },
            }
            for document in documents
        }

    def prompt(self, key: str) -> str:
        """执行 `SkillRegistry` 的提示词操作。

        参数:
            key: 用于定位目标记录、配置项或技能的稳定键。 类型：`str`。

        返回:
            返回处理后的文本或稳定标识。
        """
        document = self.get(key)
        return document.prompt_template or document.body

    def _skill_path(self, name: str, kind: SkillKind) -> Path:
        """执行技能路径的内部处理，供当前模块或类复用。

        参数:
            name: 目标对象的人类可读名称。 类型：`str`。
            kind: 用于选择解析、校验或执行分支的稳定类型判别值。 类型：`SkillKind`。

        返回:
            返回目标文件或目录路径。

        异常:
            SkillRegistryError: 当底层操作报告该异常条件时抛出。
        """
        folder = "atomic" if kind == "atomic" else f"{kind}s"
        path = (self.root / folder / name).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise SkillRegistryError("Skill path escaped the configured root") from exc
        return path

    def _read(self, path: Path, kind: SkillKind) -> SkillDocument:
        """执行`read`的内部处理，供当前模块或类复用。

        参数:
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。
            kind: 用于选择解析、校验或执行分支的稳定类型判别值。 类型：`SkillKind`。

        返回:
            返回 `SkillDocument` 类型的处理结果。
        """
        return self._parse(path.read_text(encoding="utf-8-sig"), path, kind)

    def _parse(self, markdown: str, path: Path, kind: SkillKind) -> SkillDocument:
        """执行`parse`的内部处理，供当前模块或类复用。

        参数:
            markdown: 待校验、转换或输出的 Markdown 文本。 类型：`str`。
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。
            kind: 用于选择解析、校验或执行分支的稳定类型判别值。 类型：`SkillKind`。

        返回:
            返回 `SkillDocument` 类型的处理结果。

        异常:
            SkillRegistryError: 当底层操作报告该异常条件时抛出。
        """
        match = _FRONTMATTER.match(markdown)
        if not match:
            raise SkillRegistryError(f"SKILL.md has no valid YAML frontmatter: {path}")
        fields: dict[str, str] = {}
        for raw_line in match.group("header").splitlines():
            if not raw_line.strip():
                continue
            key, separator, raw_value = raw_line.partition(":")
            if not separator:
                raise SkillRegistryError(
                    f"Invalid frontmatter line in {path}: {raw_line}"
                )
            key = key.strip()
            if key not in {"name", "description", "example_input"}:
                raise SkillRegistryError(
                    f"Unsupported frontmatter field in {path}: {key}"
                )
            value = raw_value.strip()
            if value.startswith('"') and value.endswith('"'):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError as exc:
                    raise SkillRegistryError(
                        f"Invalid quoted frontmatter value in {path}"
                    ) from exc
            fields[key] = value
        if not {"name", "description"} <= set(fields):
            raise SkillRegistryError(f"SKILL.md requires name and description: {path}")
        name = self.normalize_name(fields["name"])
        if path.parent.name != name:
            raise SkillRegistryError(
                f"Skill folder and frontmatter name differ: {path}"
            )
        description = fields["description"].strip()
        if not description:
            raise SkillRegistryError(f"Skill description is empty: {path}")
        example_input = fields.get("example_input", "").strip()
        body = markdown[match.end() :].strip()
        prompt_match = _PROMPT.search(body)
        prompt_template = prompt_match.group("prompt").strip() if prompt_match else ""
        children = tuple(
            dict.fromkeys(
                child for child in _SKILL_REFERENCE.findall(body) if child != name
            )
        )
        scripts_root = path.parent / "scripts"
        scripts = (
            tuple(
                item.relative_to(path.parent).as_posix()
                for item in sorted(scripts_root.rglob("*"))
                if item.is_file()
                and "__pycache__" not in item.parts
                and item.suffix.casefold() != ".pyc"
            )
            if scripts_root.exists()
            else ()
        )
        digest_builder = hashlib.sha256(markdown.encode("utf-8"))
        for relative_path in scripts:
            digest_builder.update(b"\x00")
            digest_builder.update(relative_path.encode("utf-8"))
            digest_builder.update(b"\x00")
            digest_builder.update((path.parent / relative_path).read_bytes())
        digest = digest_builder.hexdigest()
        updated = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
        return SkillDocument(
            name=name,
            description=description,
            kind=kind,
            path=path,
            markdown=markdown,
            body=body,
            prompt_template=prompt_template,
            children=children,
            scripts=scripts,
            content_hash=digest,
            updated_at=updated,
            example_input=example_input,
        )



class SnapshotSkillRegistry:
    """Expose an immutable Run manifest Skill bundle through ``SkillRegistry``.

    The manifest remains the source of truth. Files are materialized only inside
    the Run-owned runtime directory so deterministic scripts execute the exact
    frozen source instead of the mutable workspace Skill catalog.
    """

    def __init__(
        self,
        skills: Mapping[str, Mapping[str, Any]],
        *,
        root: str | Path,
    ) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._content_hashes = {
            self.normalize_name(name): str(snapshot.get("content_hash") or "")
            for name, snapshot in skills.items()
        }
        self._registry = SkillRegistry(
            self.root,
        )
        self._materialize(skills)

    @staticmethod
    def normalize_name(name: str) -> str:
        return SkillRegistry.normalize_name(name)

    def get(self, name: str) -> SkillDocument:
        document = self._registry.get(name)
        content_hash = self._content_hashes.get(document.name)
        return replace(document, content_hash=content_hash) if content_hash else document

    def list(
        self, *, kind: SkillKind | None = None, query: str = ""
    ) -> list[SkillDocument]:
        return [
            replace(document, content_hash=self._content_hashes[document.name])
            if self._content_hashes.get(document.name)
            else document
            for document in self._registry.list(kind=kind, query=query)
        ]

    def prompt(self, key: str) -> str:
        return self._registry.prompt(key)

    def _materialize(self, skills: Mapping[str, Mapping[str, Any]]) -> None:
        if not skills:
            raise SkillRegistryError("run Skill bundle is empty")
        for raw_name, raw_snapshot in sorted(skills.items()):
            name = self.normalize_name(raw_name)
            snapshot = dict(raw_snapshot)
            kind = str(snapshot.get("kind") or "")
            if kind not in _KINDS:
                raise SkillRegistryError(
                    f"Unsupported Skill kind in run snapshot: {kind!r}"
                )
            folder = "atomic" if kind == "atomic" else f"{kind}s"
            skill_root = (self.root / folder / name).resolve()
            try:
                skill_root.relative_to(self.root)
            except ValueError as exc:
                raise SkillRegistryError("Skill snapshot path escaped Run root") from exc
            skill_root.mkdir(parents=True, exist_ok=True)
            markdown = str(snapshot.get("markdown") or "")
            if not markdown.strip():
                raise SkillRegistryError(f"Skill snapshot has no markdown: {name}")
            self._write_exact(skill_root / "SKILL.md", markdown)
            scripts = snapshot.get("scripts") or {}
            if not isinstance(scripts, Mapping):
                raise SkillRegistryError(f"Skill snapshot scripts are invalid: {name}")
            for raw_relative, raw_source in sorted(scripts.items()):
                relative = Path(str(raw_relative).replace("\\", "/"))
                if relative.is_absolute() or ".." in relative.parts:
                    raise SkillRegistryError(
                        f"Unsafe Skill snapshot script path: {raw_relative}"
                    )
                target = (skill_root / relative).resolve()
                try:
                    target.relative_to(skill_root)
                except ValueError as exc:
                    raise SkillRegistryError(
                        f"Skill snapshot script escaped Skill root: {raw_relative}"
                    ) from exc
                target.parent.mkdir(parents=True, exist_ok=True)
                self._write_exact(target, str(raw_source))

    @staticmethod
    def _write_exact(path: Path, content: str) -> None:
        if path.exists():
            if path.read_text(encoding="utf-8-sig") != content:
                raise SkillRegistryError(
                    "Run Skill snapshot file already exists with different content: "
                    f"{path}"
                )
            return
        path.write_bytes(content.encode("utf-8"))
