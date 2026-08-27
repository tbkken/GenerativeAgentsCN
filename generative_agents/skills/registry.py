"""发现、校验、编辑并快照以 ``SKILL.md`` 为事实来源的 Agent Skill。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Literal


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
    revision: str
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
            "revision": self.revision,
            "updated_at": self.updated_at,
        }

    def detail(self) -> dict[str, object]:
        """执行 `SkillDocument` 的`detail`操作。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        return {**self.summary(), "markdown": self.markdown}


class SkillRegistry:
    """Read and atomically update standard Skill directories."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        history_root: str | Path | None = None,
    ) -> None:
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            root: 受控存储区域的根目录；派生路径不得逃逸该目录。 类型：`str | Path | None`。 默认值：`None`。
            history_root: `history`使用的根目录路径。 类型：`str | Path | None`。 默认值：`None`。

        返回:
            无返回值。
        """
        package_root = Path(__file__).resolve().parents[1]
        self.root = Path(root or package_root / "data" / "skills").resolve()
        self.history_root = Path(
            history_root or package_root.parent / "var" / "skill-history"
        ).resolve()

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
                haystack = f"{document.name}\n{document.description}\n{document.body}".casefold()
                if not needle or needle in haystack:
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

    def create(
        self,
        *,
        name: str,
        description: str,
        kind: SkillKind = "atomic",
    ) -> SkillDocument:
        """执行 `SkillRegistry` 的`create`操作。

        参数:
            name: 目标对象的人类可读名称。 类型：`str`。
            description: 目标对象的人类可读说明；会按业务规则去除无效空白。 类型：`str`。
            kind: 用于选择解析、校验或执行分支的稳定类型判别值。 类型：`SkillKind`。 默认值：`'atomic'`。

        返回:
            返回 `SkillDocument` 类型的处理结果。

        异常:
            SkillRegistryError: 当底层操作报告该异常条件时抛出。
        """
        normalized = self.normalize_name(name)
        if kind not in _KINDS:
            raise SkillRegistryError(f"Unsupported Skill kind: {kind}")
        try:
            self.get(normalized)
        except SkillRegistryError:
            pass
        else:
            raise SkillRegistryError(f"Skill already exists: {normalized}")
        skill_dir = self._skill_path(normalized, kind)
        skill_dir.mkdir(parents=True, exist_ok=False)
        title = " ".join(part.capitalize() for part in normalized.split("-"))
        markdown = (
            f"---\nname: {normalized}\n"
            f"description: {json.dumps(description.strip(), ensure_ascii=False)}\n"
            'example_input: "填写一条贴近真实运行时的示例输入（可含 \\n 换行）"\n---\n\n'
            f"# {title}\n\n## 使用时机\n\n{description.strip()}\n\n"
            "## 说明\n\n说明如何完成任务，并直接返回有用的结果。\n"
        )
        (skill_dir / "SKILL.md").write_text(markdown, encoding="utf-8")
        agents_dir = skill_dir / "agents"
        agents_dir.mkdir()
        display_name = title.replace('"', "")
        (agents_dir / "openai.yaml").write_text(
            "interface:\n"
            f'  display_name: "{display_name}"\n'
            f'  short_description: "运行 {display_name} 智能体技能"\n'
            f'  default_prompt: "使用 ${normalized} 来完成任务。"\n',
            encoding="utf-8",
        )
        return self.get(normalized)

    def save(self, name: str, markdown: str) -> SkillDocument:
        """执行 `SkillRegistry` 的`save`操作。

        参数:
            name: 目标对象的人类可读名称。 类型：`str`。
            markdown: 待校验、转换或输出的 Markdown 文本。 类型：`str`。

        返回:
            返回 `SkillDocument` 类型的处理结果。

        异常:
            SkillRegistryError: 当底层操作报告该异常条件时抛出。
        """
        current = self.get(name)
        parsed = self._parse(markdown, current.path, current.kind)
        if parsed.name != current.name:
            raise SkillRegistryError("The frontmatter name cannot be changed in place")
        self._snapshot(current)
        temporary = current.path.with_name("SKILL.md.tmp")
        temporary.write_text(markdown.rstrip() + "\n", encoding="utf-8")
        temporary.replace(current.path)
        return self.get(name)

    def history(self, name: str) -> list[dict[str, str]]:
        """执行 `SkillRegistry` 的`history`操作。

        参数:
            name: 目标对象的人类可读名称。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        current = self.get(name)
        items = [
            {
                "revision": current.revision,
                "created_at": current.updated_at,
                "source": "current",
            }
        ]
        directory = self.history_root / current.name
        if directory.exists():
            for path in sorted(directory.glob("*.md"), reverse=True):
                timestamp, _, revision = path.stem.partition("-")
                items.append(
                    {
                        "revision": revision,
                        "created_at": timestamp.replace("_", ":"),
                        "source": path.as_posix(),
                    }
                )
        return items

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
            "mcp": ["memory-stream"] if "memory-stream" in document.markdown else [],
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
                "revision": document.revision,
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
        digest = digest_builder.hexdigest()[:12]
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
            revision=digest,
            updated_at=updated,
            example_input=example_input,
        )

    def _snapshot(self, document: SkillDocument) -> None:
        """执行快照的内部处理，供当前模块或类复用。

        参数:
            document: 待校验、转换或持久化的结构化文档。 类型：`SkillDocument`。

        返回:
            无返回值。
        """
        target_dir = self.history_root / document.name
        target_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H_%M_%S.%fZ")
        target = target_dir / f"{timestamp}-{document.revision}.md"
        target.write_text(document.markdown, encoding="utf-8")
