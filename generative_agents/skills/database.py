"""Database-backed product Skill catalog with immutable revisions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.exc import IntegrityError

from generative_agents.persistence.database import Database
from generative_agents.persistence.models import (
    ExperimentRevision,
    SeedResourceTombstone,
    SkillDefinition,
    SkillRevision,
    WorldMapRevision,
)

from .registry import SkillDocument, SkillKind, SkillRegistry, SkillRegistryError


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DatabaseSkillRegistry:
    """Use SQL as Skill truth and files only as a disposable execution cache."""

    def __init__(self, database: Database, *, cache_root: str | Path) -> None:
        self.database = database
        self.cache_root = Path(cache_root).resolve()
        self.cache_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def normalize_name(name: str) -> str:
        return SkillRegistry.normalize_name(name)

    def ensure_builtin_skills(self, source: SkillRegistry | None = None) -> int:
        """Seed missing bundled Skills once; later user revisions stay in SQL."""

        source = source or SkillRegistry()
        inserted = 0
        with self.database.session_factory.begin() as session:
            existing = set(session.scalars(select(SkillDefinition.skill_key)))
            deleted_seeds = set(
                session.scalars(
                    select(SeedResourceTombstone.resource_key).where(
                        SeedResourceTombstone.resource_type == "skill"
                    )
                )
            )
            for document in source.list():
                if document.name in existing or document.name in deleted_seeds:
                    continue
                scripts = {
                    relative: (document.path.parent / relative).read_text(
                        encoding="utf-8-sig"
                    )
                    for relative in document.scripts
                }
                content_hash = self._content_hash(document.markdown, scripts)
                definition = SkillDefinition(
                    skill_key=document.name,
                    description=document.description,
                    kind=document.kind,
                    is_builtin=True,
                    row_version=1,
                )
                session.add(definition)
                session.flush()
                revision = SkillRevision(
                    skill_id=definition.id,
                    revision_no=1,
                    markdown=document.markdown,
                    content_hash=content_hash,
                    children_json=list(document.children),
                    scripts_json=scripts,
                    source="SYSTEM_SEED",
                )
                session.add(revision)
                session.flush()
                definition.current_revision_id = revision.id
                existing.add(document.name)
                inserted += 1
        return inserted

    def list(
        self,
        *,
        kind: SkillKind | None = None,
        query: str = "",
        include_archived: bool = False,
    ) -> list[SkillDocument]:
        with self.database.session_factory() as session:
            statement = select(SkillDefinition)
            if kind is not None:
                statement = statement.where(SkillDefinition.kind == kind)
            if not include_archived:
                statement = statement.where(SkillDefinition.archived_at.is_(None))
            rows = list(
                session.scalars(
                    statement.order_by(
                        SkillDefinition.kind, SkillDefinition.skill_key
                    )
                )
            )
            needle = query.strip().casefold()
            if needle:
                normalized_needle = re.sub(r"[\s_-]+", "-", needle).strip("-")
                rows = [
                    row
                    for row in rows
                    if needle in row.description.casefold()
                    or needle in row.skill_key.replace("-", " ").casefold()
                    or normalized_needle in row.skill_key.casefold()
                ]
            return [self._document(session, row) for row in rows]

    def get(self, name: str, *, include_archived: bool = False) -> SkillDocument:
        normalized = self.normalize_name(name)
        with self.database.session_factory() as session:
            definition = self._definition(
                session, normalized, include_archived=include_archived
            )
            return self._document(session, definition)

    def get_revision(self, revision_id: str) -> SkillDocument:
        """Load one immutable Skill Revision by identity, independent of latest state."""
        with self.database.session_factory() as session:
            revision = session.get(SkillRevision, revision_id)
            if revision is None:
                raise SkillRegistryError(
                    f"Skill revision does not exist: {revision_id}"
                )
            definition = session.get(SkillDefinition, revision.skill_id)
            if definition is None:
                raise SkillRegistryError(
                    f"Skill definition does not exist for revision: {revision_id}"
                )
            return self._document(session, definition, revision)

    def create(
        self,
        *,
        name: str,
        description: str,
        kind: SkillKind = "atomic",
    ) -> SkillDocument:
        normalized = self.normalize_name(name)
        description = description.strip()
        if not description:
            raise SkillRegistryError("Skill description is empty")
        if kind not in {"atomic", "pack", "brain"}:
            raise SkillRegistryError(f"Unsupported Skill kind: {kind}")
        title = " ".join(part.capitalize() for part in normalized.split("-"))
        markdown = (
            f"---\nname: {normalized}\n"
            f"description: {json.dumps(description, ensure_ascii=False)}\n"
            'example_input: "填写一条贴近真实运行时的示例输入（可含 \\n 换行）"\n'
            "---\n\n"
            f"# {title}\n\n## 使用时机\n\n{description}\n\n"
            "## 说明\n\n说明如何完成任务，并直接返回有用的结果。\n"
        )
        parsed = self._parse_candidate(normalized, kind, markdown, {})
        content_hash = self._content_hash(markdown, {})
        try:
            with self.database.session_factory.begin() as session:
                if session.scalar(
                    select(SkillDefinition.id).where(
                        SkillDefinition.skill_key == normalized
                    )
                ):
                    raise SkillRegistryError(f"Skill already exists: {normalized}")
                definition = SkillDefinition(
                    skill_key=normalized,
                    description=parsed.description,
                    kind=kind,
                    is_builtin=False,
                )
                session.add(definition)
                session.flush()
                revision = SkillRevision(
                    skill_id=definition.id,
                    revision_no=1,
                    markdown=markdown,
                    content_hash=content_hash,
                    children_json=list(parsed.children),
                    scripts_json={},
                    source="USER",
                )
                session.add(revision)
                session.flush()
                definition.current_revision_id = revision.id
        except IntegrityError as exc:
            raise SkillRegistryError(f"Skill already exists: {normalized}") from exc
        return self.get(normalized)

    def save(
        self,
        name: str,
        markdown: str,
        *,
        scripts: Mapping[str, str] | None = None,
    ) -> SkillDocument:
        normalized = self.normalize_name(name)
        with self.database.session_factory.begin() as session:
            definition = self._definition(session, normalized)
            current = self._revision(session, definition)
            scripts = (
                dict(current.scripts_json or {})
                if scripts is None
                else self._normalize_script_sources(scripts)
            )
            parsed = self._parse_candidate(
                normalized, definition.kind, markdown, scripts
            )
            if parsed.name != normalized:
                raise SkillRegistryError(
                    "The frontmatter name cannot be changed in place"
                )
            content_hash = self._content_hash(markdown, scripts)
            if content_hash == current.content_hash:
                return self._document(session, definition, current)
            revision_no = int(
                session.scalar(
                    select(func.max(SkillRevision.revision_no)).where(
                        SkillRevision.skill_id == definition.id
                    )
                )
                or 0
            ) + 1
            revision = SkillRevision(
                skill_id=definition.id,
                revision_no=revision_no,
                markdown=markdown,
                content_hash=content_hash,
                children_json=list(parsed.children),
                scripts_json=scripts,
                source="USER",
            )
            session.add(revision)
            session.flush()
            definition.current_revision_id = revision.id
            definition.description = parsed.description
            definition.updated_at = _utc_now()
            definition.row_version += 1
        return self.get(normalized)

    def script_sources(self, name: str) -> dict[str, str]:
        """Return current private Script sources from the database Revision."""

        normalized = self.normalize_name(name)
        with self.database.session_factory() as session:
            definition = self._definition(session, normalized)
            revision = self._revision(session, definition)
            return dict(sorted((revision.scripts_json or {}).items()))

    def history(self, name: str) -> list[dict[str, str | int]]:
        normalized = self.normalize_name(name)
        with self.database.session_factory() as session:
            definition = self._definition(
                session, normalized, include_archived=True
            )
            revisions = list(
                session.scalars(
                    select(SkillRevision)
                    .where(SkillRevision.skill_id == definition.id)
                    .order_by(SkillRevision.revision_no.desc())
                )
            )
            return [
                {
                    "revision": item.content_hash,
                    "revision_no": item.revision_no,
                    "created_at": item.created_at.isoformat(),
                    "source": item.source,
                }
                for item in revisions
            ]

    def dependencies(self, name: str) -> dict[str, object]:
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
            "mcp": ["memory-stream"]
            if "memory-stream" in document.markdown
            else [],
        }

    def snapshot(
        self,
        roots: Iterable[str] | None = None,
        *,
        root_revisions: Mapping[str, str] | None = None,
    ) -> dict[str, dict[str, object]]:
        with self.database.session_factory() as session:
            selected: dict[str, tuple[SkillDefinition, SkillRevision]] = {}
            if roots is None:
                definitions = list(
                    session.scalars(
                        select(SkillDefinition)
                        .where(SkillDefinition.archived_at.is_(None))
                        .order_by(SkillDefinition.kind, SkillDefinition.skill_key)
                    )
                )
                for definition in definitions:
                    selected[definition.skill_key] = (
                        definition,
                        self._revision(session, definition),
                    )
            else:
                pinned = {
                    self.normalize_name(name): revision_id
                    for name, revision_id in (root_revisions or {}).items()
                }
                pending = [self.normalize_name(name) for name in roots]
                while pending:
                    name = pending.pop()
                    if name in selected:
                        continue
                    definition = self._definition(
                        session, name, include_archived=True
                    )
                    revision_id = pinned.get(name)
                    revision = (
                        session.get(SkillRevision, revision_id)
                        if revision_id is not None
                        else self._revision(session, definition)
                    )
                    if revision is None or revision.skill_id != definition.id:
                        raise SkillRegistryError(
                            f"Skill revision does not belong to {name}: {revision_id}"
                        )
                    selected[name] = (definition, revision)
                    pending.extend(revision.children_json or ())
            return {
                name: {
                    "kind": definition.kind,
                    "description": definition.description,
                    "markdown": revision.markdown,
                    "revision": revision.content_hash,
                    "revision_no": revision.revision_no,
                    "scripts": dict(revision.scripts_json or {}),
                }
                for name, (definition, revision) in sorted(selected.items())
            }

    def prompt(self, key: str) -> str:
        document = self.get(key)
        return document.prompt_template or document.body

    def archive(self, name: str) -> SkillDocument:
        normalized = self.normalize_name(name)
        with self.database.session_factory.begin() as session:
            definition = self._definition(session, normalized, include_archived=True)
            if definition.is_builtin:
                raise SkillRegistryError("Built-in Skills cannot be archived")
            definition.archived_at = definition.archived_at or _utc_now()
            definition.updated_at = _utc_now()
            definition.row_version += 1
        return self.get(normalized, include_archived=True)

    def restore(self, name: str) -> SkillDocument:
        normalized = self.normalize_name(name)
        with self.database.session_factory.begin() as session:
            definition = self._definition(session, normalized, include_archived=True)
            definition.archived_at = None
            definition.updated_at = _utc_now()
            definition.row_version += 1
        return self.get(normalized)

    def delete(self, name: str) -> None:
        normalized = self.normalize_name(name)
        with self.database.session_factory.begin() as session:
            definition = self._definition(session, normalized, include_archived=True)
            revision_ids = list(
                session.scalars(
                    select(SkillRevision.id).where(
                        SkillRevision.skill_id == definition.id
                    )
                )
            )
            references = [
                cast(ExperimentRevision.definition_json, Text).contains(normalized),
            ]
            references.extend(
                cast(ExperimentRevision.definition_json, Text).contains(revision_id)
                for revision_id in revision_ids
            )
            used_by_experiment = session.scalar(
                select(ExperimentRevision.id).where(or_(*references)).limit(1)
            )
            used_by_map = session.scalar(
                select(WorldMapRevision.id)
                .where(cast(WorldMapRevision.world_json, Text).contains(normalized))
                .limit(1)
            )
            used_by_skill = session.scalar(
                select(SkillRevision.id)
                .where(
                    SkillRevision.skill_id != definition.id,
                    cast(SkillRevision.children_json, Text).contains(normalized),
                )
                .limit(1)
            )
            if used_by_experiment or used_by_map or used_by_skill:
                raise SkillRegistryError(
                    "Skill 仍被不可变的实验、地图或其他 Skill Revision 引用"
                )
            if definition.is_builtin:
                session.merge(
                    SeedResourceTombstone(
                        resource_type="skill", resource_key=definition.skill_key
                    )
                )
            session.delete(definition)

    def _definition(self, session, name: str, *, include_archived: bool = False):
        definition = session.scalar(
            select(SkillDefinition).where(SkillDefinition.skill_key == name)
        )
        if definition is None or (
            definition.archived_at is not None and not include_archived
        ):
            raise SkillRegistryError(f"Skill does not exist: {name}")
        return definition

    @staticmethod
    def _revision(session, definition: SkillDefinition) -> SkillRevision:
        revision = (
            session.get(SkillRevision, definition.current_revision_id)
            if definition.current_revision_id
            else None
        )
        if revision is None:
            raise SkillRegistryError(
                f"Skill has no current revision: {definition.skill_key}"
            )
        return revision

    def _document(self, session, definition, revision=None) -> SkillDocument:
        revision = revision or self._revision(session, definition)
        root = self.cache_root / revision.content_hash
        folder = "atomic" if definition.kind == "atomic" else f"{definition.kind}s"
        skill_root = root / folder / definition.skill_key
        skill_file = skill_root / "SKILL.md"
        self._write_exact(skill_file, revision.markdown)
        for relative, source in sorted((revision.scripts_json or {}).items()):
            target = self._safe_script_path(skill_root, relative)
            self._write_exact(target, source)
        parser = SkillRegistry(
            root=root,
            history_root=root / ".history-disabled",
        )
        document = parser.get(definition.skill_key)
        return replace(
            document,
            revision=revision.content_hash,
            updated_at=revision.created_at.isoformat(),
            storage="database",
            storage_ref=(
                f"database://skills/{definition.skill_key}/revisions/"
                f"{revision.revision_no}"
            ),
            revision_id=revision.id,
            revision_no=revision.revision_no,
            is_builtin=bool(definition.is_builtin),
            archived_at=(
                definition.archived_at.isoformat()
                if definition.archived_at is not None
                else None
            ),
        )

    def _parse_candidate(self, name, kind, markdown, scripts) -> SkillDocument:
        content_hash = self._content_hash(markdown, scripts)
        root = self.cache_root / "validation" / content_hash
        folder = "atomic" if kind == "atomic" else f"{kind}s"
        skill_root = root / folder / name
        self._write_exact(skill_root / "SKILL.md", markdown)
        for relative, source in sorted(scripts.items()):
            self._write_exact(self._safe_script_path(skill_root, relative), source)
        return SkillRegistry(
            root=root,
            history_root=root / ".history-disabled",
        ).get(name)

    @staticmethod
    def _content_hash(markdown: str, scripts: Mapping[str, str]) -> str:
        encoded = json.dumps(
            {
                "markdown": markdown,
                "scripts": dict(sorted(scripts.items())),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _normalize_script_sources(
        cls, scripts: Mapping[str, str]
    ) -> dict[str, str]:
        if len(scripts) > 50:
            raise SkillRegistryError("Skill cannot contain more than 50 private Scripts")
        normalized: dict[str, str] = {}
        for raw_relative, raw_source in scripts.items():
            relative = str(raw_relative).replace("\\", "/").strip()
            if not relative.startswith("scripts/") or relative.endswith("/"):
                raise SkillRegistryError(
                    f"Skill private files must live under scripts/: {raw_relative}"
                )
            if not isinstance(raw_source, str):
                raise SkillRegistryError(f"Skill script source must be text: {relative}")
            if len(raw_source) > 500_000:
                raise SkillRegistryError(f"Skill script is too large: {relative}")
            cls._safe_script_path(Path("skill-root").resolve(), relative)
            normalized[relative] = raw_source
        return dict(sorted(normalized.items()))

    @staticmethod
    def _safe_script_path(skill_root: Path, relative: str) -> Path:
        candidate = Path(str(relative).replace("\\", "/"))
        if candidate.is_absolute() or ".." in candidate.parts:
            raise SkillRegistryError(f"Unsafe Skill script path: {relative}")
        target = (skill_root / candidate).resolve()
        try:
            target.relative_to(skill_root.resolve())
        except ValueError as exc:
            raise SkillRegistryError(f"Unsafe Skill script path: {relative}") from exc
        return target

    @staticmethod
    def _write_exact(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_text(encoding="utf-8-sig") != content:
                raise SkillRegistryError(
                    f"Skill runtime cache conflicts with revision content: {path}"
                )
            return
        path.write_bytes(content.encode("utf-8"))


__all__ = ["DatabaseSkillRegistry"]
