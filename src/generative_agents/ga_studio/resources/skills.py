"""Studio database catalog for directly editable public Skill resources."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from generative_agents.ga_studio.resources.skill_document import AuthorSkillDocument
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import object_session
from sqlalchemy.exc import IntegrityError

from generative_agents.ga_studio.storage.database import Database
from generative_agents.ga_studio.storage.models import StudioSkill

from generative_agents.ga_protocol.skills.documents import SkillDocument
from generative_agents.ga_protocol.skills.documents import SkillKind
from generative_agents.ga_protocol.skills.documents import SkillRegistry
from generative_agents.ga_protocol.skills.documents import SkillRegistryError
from generative_agents.ga_protocol.skills.dependencies import skill_dependencies
from generative_agents.ga_protocol.skills.metadata import skill_display_name, skill_frontmatter
from generative_agents.ga_protocol.skills.decisions import validate_compiled_decision


def _utc_now() -> datetime:
    return datetime.now(UTC)


def skill_header_column():
    """Project only the frontmatter, including its closing delimiter, in SQLite."""
    return func.substr(StudioSkill.markdown, 1, func.instr(StudioSkill.markdown, "\n---") + 3)


def display_name_from_markdown(markdown: str, key: str) -> str:
    return skill_display_name(skill_frontmatter(markdown)[0], key)


class DatabaseSkillRegistry:
    """Store one mutable row per public Skill; experiments receive physical copies.

    ``content_hash`` detects behavior changes and participates in experiment
    package integrity. It is not a selectable revision, publication identity,
    upgrade target, or Runtime database reference.
    """

    def __init__(self, database: Database, *, cache_root: str | Path) -> None:
        self.database = database
        self.cache_root = Path(cache_root).resolve()
        self.cache_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def normalize_name(name: str) -> str:
        return SkillRegistry.normalize_name(name)

    def list(
        self,
        *,
        kind: SkillKind | None = None,
        query: str = "",
        include_archived: bool = False,
    ) -> list[SkillDocument]:
        if kind is not None and kind not in {"skill", "brain"}:
            raise SkillRegistryError(f"Unsupported Skill kind: {kind}")
        with self.database.session_factory() as session:
            statement = select(StudioSkill)
            if kind is not None:
                statement = statement.where(StudioSkill.kind == kind)
            if not include_archived:
                statement = statement.where(StudioSkill.archived_at.is_(None))
            rows = list(
                session.scalars(statement.order_by(StudioSkill.kind, StudioSkill.skill_key))
            )
            needle = query.strip().casefold()
            if needle:
                normalized = re.sub(r"[\s_-]+", "-", needle).strip("-")
                rows = [
                    row
                    for row in rows
                    if needle in row.description.casefold()
                    or needle in display_name_from_markdown(row.markdown, row.skill_key).casefold()
                    or needle in row.skill_key.replace("-", " ").casefold()
                    or normalized in row.skill_key.casefold()
                ]
            return [self._document(row) for row in rows]

    def list_summaries(self, *, kind=None, query="", include_archived=False, page=1, page_size=20):
        """The author list is a SQL projection, never an executable Skill materialization."""
        if kind is not None and kind not in {"skill", "brain"}:
            raise SkillRegistryError(f"Unsupported Skill kind: {kind}")
        from .listing import dates, page_result, query_page, search_predicate
        model = StudioSkill
        base = [] if include_archived else [model.archived_at.is_(None)]
        predicates = [*base]
        if kind:
            predicates.append(model.kind == kind)
        if query.strip():
            normalized = re.sub(r"[\s_-]+", "-", query.strip().casefold()).strip("-")
            predicates.append(search_predicate(normalized, model.skill_key)
                              | search_predicate(query, model.description, skill_header_column()))
        columns = [model.id.label("resource_id"), model.skill_key.label("name"), model.kind,
                   model.description, model.children_json.label("children"), model.content_hash,
                   model.is_builtin, model.archived_at, model.updated_at, model.row_version,
                   skill_header_column().label("frontmatter")]
        with self.database.session_factory() as session:
            rows, total = query_page(session, model, columns, predicates=predicates,
                                     order=(model.kind, model.skill_key), page=page, page_size=page_size)
            counts = dict(session.execute(select(model.kind, func.count()).where(*base).group_by(model.kind)).all())
        for row in rows:
            row["display_name"] = display_name_from_markdown(row.pop("frontmatter"), row["name"])
            dates(row)
            row.update(storage="database", storage_ref=f"database://skills/{row['resource_id']}")
            row["path"] = row["storage_ref"]
        return page_result(rows, total, page, page_size,
                           counts={name: counts.get(name, 0) for name in ("skill", "brain")})

    def get(self, name: str, *, include_archived: bool = False) -> SkillDocument:
        normalized = self.normalize_name(name)
        with self.database.session_factory() as session:
            return self._document(
                self._row(session, normalized, include_archived=include_archived)
            )

    def get_by_id(self, resource_id: str) -> SkillDocument:
        """Resolve a mutable Studio Skill by its stable resource ID."""
        with self.database.session_factory() as session:
            row = session.get(StudioSkill, resource_id)
            if row is None or row.archived_at is not None:
                raise SkillRegistryError(f"Skill does not exist: {resource_id}")
            return self._document(row)

    # Temporary adapter for the old console module.  The package-first Studio
    # API uses ``get_by_id`` and exposes no revision vocabulary.
    get_revision = get_by_id

    def create(
        self,
        *,
        name: str,
        description: str,
        display_name: str | None = None,
        kind: SkillKind = "skill",
    ) -> SkillDocument:
        normalized = self.normalize_name(name)
        description = description.strip()
        if not description:
            raise SkillRegistryError("Skill description is empty")
        if kind not in {"skill", "brain"}:
            raise SkillRegistryError(f"Unsupported Skill kind: {kind}")
        title = skill_display_name({"display_name": display_name} if display_name is not None else {}, normalized)
        markdown = (
            f"---\nname: {normalized}\n"
            f"display_name: {json.dumps(title, ensure_ascii=False)}\n"
            f"description: {json.dumps(description, ensure_ascii=False)}\n"
            'example_input: "填写一条贴近真实运行时的示例输入（可含 \\n 换行）"\n'
            "---\n\n"
            f"# {title}\n\n## 使用时机\n\n{description}\n\n"
            "## 说明\n\n说明如何完成任务，并直接返回有用的结果。\n"
        )
        parsed = self._parse_candidate(normalized, kind, markdown, {})
        try:
            with self.database.session_factory.begin() as session:
                if session.scalar(select(StudioSkill.id).where(StudioSkill.skill_key == normalized)):
                    raise SkillRegistryError(f"Skill already exists: {normalized}")
                session.add(
                    StudioSkill(
                        skill_key=normalized,
                        description=parsed.description,
                        kind=kind,
                        markdown=markdown,
                        content_hash=self._content_hash(markdown, {}),
                        children_json=list(parsed.children),
                        scripts_json={},
                    )
                )
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
            row = self._row(session, normalized)
            normalized_scripts = (
                dict(row.scripts_json or {})
                if scripts is None
                else self._normalize_script_sources(scripts)
            )
            from generative_agents.ga_studio.resources.exchange import resource_extra_files, bind_saved_resource_dependencies
            parsed = self._parse_candidate(normalized, row.kind, markdown, normalized_scripts,
                                           extra_files=resource_extra_files(session, "skill", row.id))
            validate_compiled_decision(parsed)
            bind_saved_resource_dependencies(session, "skill", row.id, [
                {"kind": "skill", "key": key} for key in parsed.children
            ])
            content_hash = self._content_hash(markdown, normalized_scripts,
                                               extra_files=resource_extra_files(session, "skill", row.id))
            if content_hash != row.content_hash:
                row.markdown = markdown
                row.content_hash = content_hash
                row.children_json = list(parsed.children)
                row.scripts_json = normalized_scripts
                row.description = parsed.description
                row.row_version += 1
                row.updated_at = _utc_now()
        return self.get(normalized)

    def script_sources(self, name: str) -> dict[str, str]:
        normalized = self.normalize_name(name)
        with self.database.session_factory() as session:
            row = self._row(session, normalized)
            return dict(sorted((row.scripts_json or {}).items()))

    def author_detail(self, name):
        """Parse one author document in memory, without executing/materializing its closure."""
        with self.database.session_factory() as session:
            row = self._row(session, self.normalize_name(name))
            scripts = row.scripts_json or {}
            document = SkillRegistry(self.cache_root)._parse(row.markdown,
                self.cache_root / row.skill_key / "SKILL.md", row.kind,
                file_contents={path: text.encode("utf-8") for path, text in scripts.items()})
            return {**document.detail(), "content_hash": row.content_hash,
                    "updated_at": row.updated_at.isoformat(), "row_version": row.row_version,
                    "path": f"database://skills/{row.id}", "storage": "database",
                    "storage_ref": f"database://skills/{row.id}", "resource_id": row.id,
                    "is_builtin": row.is_builtin, "archived_at": row.archived_at.isoformat() if row.archived_at else None,
                    "script_sources": dict(sorted(scripts.items()))}

    def history(self, name: str) -> list[dict[str, str | int]]:
        """Return current content metadata; public Skills have no version history."""

        normalized = self.normalize_name(name)
        with self.database.session_factory() as session:
            row = session.execute(select(StudioSkill.content_hash, StudioSkill.row_version, StudioSkill.updated_at)
                                  .where(StudioSkill.skill_key == normalized)).one_or_none()
            if row is None:
                raise SkillRegistryError(f"Skill does not exist: {normalized}")
            return [
                {
                    "content_hash": row.content_hash,
                    "row_version": row.row_version,
                    "updated_at": row.updated_at.isoformat(),
                    "source": "CURRENT_RESOURCE",
                }
            ]

    def dependencies(self, name: str) -> dict[str, object]:
        normalized = self.normalize_name(name)
        with self.database.session_factory() as session:
            self._row(session, normalized)
            def load(key):
                row = session.scalar(select(StudioSkill).where(StudioSkill.skill_key == key,
                                                               StudioSkill.archived_at.is_(None)))
                if row is None:
                    return None
                return {"kind": row.kind, "display_name": display_name_from_markdown(row.markdown, row.skill_key),
                        "description": row.description, "markdown": row.markdown,
                        "content_hash": row.content_hash, "scripts": list(row.scripts_json or {})}
            return skill_dependencies(normalized, load)

    def snapshot(
        self,
        roots: Iterable[str] | None = None,
        *,
        root_revisions: Mapping[str, str] | None = None,
    ) -> dict[str, dict[str, object]]:
        """Copy the current recursive closure for immediate package materialization."""

        with self.database.session_factory() as session:
            selected: dict[str, StudioSkill] = {}
            if roots is None:
                rows = list(
                    session.scalars(
                        select(StudioSkill)
                        .where(StudioSkill.archived_at.is_(None))
                        .order_by(StudioSkill.kind, StudioSkill.skill_key)
                    )
                )
                selected = {row.skill_key: row for row in rows}
            else:
                expected_ids = {
                    self.normalize_name(name): resource_id
                    for name, resource_id in (root_revisions or {}).items()
                }
                pending = [self.normalize_name(name) for name in roots]
                while pending:
                    name = pending.pop()
                    if name in selected:
                        continue
                    row = self._row(session, name, include_archived=True)
                    if name in expected_ids and expected_ids[name] != row.id:
                        raise SkillRegistryError(
                            f"Skill resource ID does not belong to {name}: {expected_ids[name]}"
                        )
                    selected[name] = row
                    pending.extend(row.children_json or ())
            return {
                name: {
                    "kind": row.kind,
                    "display_name": display_name_from_markdown(row.markdown, name),
                    "description": row.description,
                    "markdown": row.markdown,
                    "content_hash": row.content_hash,
                    "scripts": dict(row.scripts_json or {}),
                    "dependencies": list(row.children_json or []),
                }
                for name, row in sorted(selected.items())
            }

    def prompt(self, key: str) -> str:
        document = self.get(key)
        return document.prompt_template or document.body

    def archive(self, name: str) -> SkillDocument:
        normalized = self.normalize_name(name)
        with self.database.session_factory.begin() as session:
            row = self._row(session, normalized, include_archived=True)
            row.archived_at = row.archived_at or _utc_now()
            row.updated_at = _utc_now()
            row.row_version += 1
        return self.get(normalized, include_archived=True)

    def restore(self, name: str) -> SkillDocument:
        normalized = self.normalize_name(name)
        with self.database.session_factory.begin() as session:
            row = self._row(session, normalized, include_archived=True)
            row.archived_at = None
            row.updated_at = _utc_now()
            row.row_version += 1
        return self.get(normalized)

    def delete(self, name: str) -> None:
        """Delete freely; already-created experiments contain physical copies."""

        normalized = self.normalize_name(name)
        with self.database.session_factory.begin() as session:
            row = self._row(session, normalized, include_archived=True)
            session.delete(row)
            from generative_agents.ga_studio.resources.exchange import delete_resource_exchange_state
            delete_resource_exchange_state(session, "skill", row.id)

    @staticmethod
    def _row(session, name: str, *, include_archived: bool = False) -> StudioSkill:
        row = session.scalar(select(StudioSkill).where(StudioSkill.skill_key == name))
        if row is None or (row.archived_at is not None and not include_archived):
            raise SkillRegistryError(f"Skill does not exist: {name}")
        return row

    def _document(self, row: StudioSkill) -> SkillDocument:
        root = self.cache_root / row.content_hash
        folder = f"{row.kind}s"
        skill_root = root / folder / row.skill_key
        self._write_exact(skill_root / "SKILL.md", row.markdown)
        for relative, source in sorted((row.scripts_json or {}).items()):
            self._write_exact(self._safe_script_path(skill_root, relative), source)
        from generative_agents.ga_studio.resources.exchange import resource_extra_files
        for relative, content in resource_extra_files(object_session(row), "skill", row.id).items():
            target = self._safe_script_path(skill_root, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        document = SkillRegistry(
            root=root,
        ).get(row.skill_key)
        return AuthorSkillDocument(
            **{**asdict(document), "content_hash": row.content_hash, "updated_at": row.updated_at.isoformat()},
            storage="database",
            storage_ref=f"database://skills/{row.id}",
            resource_id=row.id,
            is_builtin=bool(row.is_builtin),
            archived_at=row.archived_at.isoformat() if row.archived_at else None,
        )

    def _parse_candidate(self, name, kind, markdown, scripts, *, extra_files=None) -> SkillDocument:
        if kind not in {"skill", "brain"}:
            raise SkillRegistryError(f"Unsupported Skill kind: {kind}")
        content_hash = self._content_hash(markdown, scripts, extra_files=extra_files)
        root = self.cache_root / "validation" / content_hash
        folder = f"{kind}s"
        skill_root = root / folder / name
        self._write_exact(skill_root / "SKILL.md", markdown)
        for relative, source in sorted(scripts.items()):
            self._write_exact(self._safe_script_path(skill_root, relative), source)
        for relative, content in sorted((extra_files or {}).items()):
            target = self._safe_script_path(skill_root, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return SkillRegistry(root=root).get(name)

    @staticmethod
    def _content_hash(markdown: str, scripts: Mapping[str, str], *, extra_files=None) -> str:
        payload = {"markdown": markdown, "scripts": dict(sorted(scripts.items()))}
        if extra_files:
            payload["extra_files"] = {path: hashlib.sha256(content).hexdigest()
                                      for path, content in sorted(extra_files.items())}
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _normalize_script_sources(cls, scripts: Mapping[str, str]) -> dict[str, str]:
        if len(scripts) > 50:
            raise SkillRegistryError("Skill cannot contain more than 50 private Scripts")
        normalized: dict[str, str] = {}
        for raw_relative, raw_source in scripts.items():
            relative = str(raw_relative).replace("\\", "/").strip()
            if not relative.startswith("scripts/") or relative.endswith("/"):
                raise SkillRegistryError(f"Skill private files must live under scripts/: {raw_relative}")
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
            if path.read_bytes() != content.encode("utf-8"):
                raise SkillRegistryError(f"Skill cache conflicts with content hash: {path}")
            return
        path.write_bytes(content.encode("utf-8"))


__all__ = ["DatabaseSkillRegistry"]
