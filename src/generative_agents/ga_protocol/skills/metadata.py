"""Parse Skill author metadata without inspecting scripts or executable content."""
from __future__ import annotations

import json
import re


FRONTMATTER = re.compile(r"\A\ufeff?---\s*\r?\n(?P<header>.*?)\r?\n---\s*(?:\r?\n|\Z)", re.DOTALL)


class SkillRegistryError(ValueError):
    """A Skill document is missing, unsafe, or invalid."""


def skill_frontmatter(markdown: str, *, location: object = "SKILL.md") -> tuple[dict[str, str], int]:
    """Read the supported, single-line frontmatter fields and body offset."""
    match = FRONTMATTER.match(markdown)
    if not match:
        raise SkillRegistryError(f"SKILL.md has no valid YAML frontmatter: {location}")
    fields = {}
    for raw_line in match.group("header").splitlines():
        if not raw_line.strip():
            continue
        key, separator, raw_value = raw_line.partition(":")
        if not separator:
            raise SkillRegistryError(f"Invalid frontmatter line in {location}: {raw_line}")
        key = key.strip()
        if key not in {"name", "display_name", "description", "example_input", "llm_type", "decision_questions", "decision_prompt_sha256"}:
            raise SkillRegistryError(f"Unsupported frontmatter field in {location}: {key}")
        if key in fields:
            raise SkillRegistryError(f"Duplicate frontmatter field in {location}: {key}")
        value = raw_value.strip()
        if value.startswith('"') and value.endswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise SkillRegistryError(f"Invalid quoted frontmatter value in {location}") from exc
        fields[key] = value
    if not {"name", "description"} <= set(fields):
        raise SkillRegistryError(f"SKILL.md requires name and description: {location}")
    return fields, match.end()


def skill_display_name(fields: dict[str, str], key: str) -> str:
    """The optional human label never changes the stable Skill key."""
    raw = fields.get("display_name", key)
    if not isinstance(raw, str):
        raise SkillRegistryError("Skill display_name must be text")
    value = raw.strip()
    if not value or len(value) > 200 or "\n" in value or "\r" in value:
        raise SkillRegistryError("Skill display_name must contain 1-200 characters on one line")
    return value
