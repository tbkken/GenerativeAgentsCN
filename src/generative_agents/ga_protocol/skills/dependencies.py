"""Resolve natural-language Skill references against the shared interface catalog."""

from collections.abc import Callable, Mapping
import re

from generative_agents.ga_protocol.skills.interfaces import authoring_interface_catalog, simulation_interface_catalog
from generative_agents.ga_protocol.skills.metadata import FRONTMATTER


SIMULATION_INTERFACES = tuple(item["name"] for item in simulation_interface_catalog())
_INTERFACE_REFERENCE = re.compile(r"(?<![A-Za-z0-9_@/\-])@([A-Za-z0-9][A-Za-z0-9_-]*)(?![A-Za-z0-9_-])")
_SKILL_REFERENCE = re.compile(r"(?<![A-Za-z0-9_$@/\-])\$([a-z0-9]+(?:-[a-z0-9]+)*)(?![A-Za-z0-9_-])")


def _body(markdown: str) -> str:
    return FRONTMATTER.sub("", markdown, count=1)


def referenced_interfaces(markdown: str) -> list[str]:
    """Return explicit ``@name`` references in body order, including unknown names."""
    return list(dict.fromkeys(_INTERFACE_REFERENCE.findall(_body(markdown))))


def referenced_skills(markdown: str) -> list[str]:
    """Return exact ``$skill-key`` references, retaining self-references for validation."""
    return list(dict.fromkeys(_SKILL_REFERENCE.findall(_body(markdown))))


def validate_interface_references(markdown: str) -> None:
    unknown = [name for name in referenced_interfaces(markdown) if name not in SIMULATION_INTERFACES]
    if unknown:
        raise ValueError("Unknown simulation interface reference: " + ", ".join(f"@{name}" for name in unknown))


def skill_dependencies(name: str, load: Callable[[str], Mapping | None]) -> dict[str, object]:
    """Build the same recursive document graph for author, package and runtime views.

    ``load`` reads one local document or returns ``None`` for a missing dependency.
    Scripts come from the document's real files; relationships come from its body.
    Missing references and cycles stay visible as leaves rather than aborting editing.
    """
    diagnostics = []
    cache = {}

    def visit(key: str, ancestors: tuple[str, ...]) -> dict:
        path = (*ancestors, key)
        if key not in cache:
            cache[key] = load(key)
        document = cache[key]
        node = {"name": key, "display_name": key, "scripts": [], "interfaces": [], "skills": []}
        if document is None:
            node["missing"] = True
            diagnostics.append({"code": "SKILL_DEPENDENCY_MISSING", "message": f"Skill ${key} 不存在或已归档", "path": list(path)})
            return node
        node.update({field: document[field] for field in ("display_name", "kind", "description", "content_hash") if field in document})
        node["scripts"] = sorted(document.get("scripts") or [])
        markdown = str(document.get("markdown") or "")
        node["interfaces"] = referenced_interfaces(markdown)
        if key in ancestors:
            node["cycle"] = True
            diagnostics.append({"code": "SKILL_DEPENDENCY_CYCLE", "message": "Skill 依赖形成循环：" + " → ".join(path), "path": list(path)})
            return node
        for interface in node["interfaces"]:
            if interface not in SIMULATION_INTERFACES:
                diagnostics.append({"code": "SKILL_INTERFACE_UNKNOWN", "message": f"未知接口 @{interface}", "path": list(path)})
        node["skills"] = [visit(child, path) for child in referenced_skills(markdown)]
        return node

    root = visit(name, ())
    return {
        "skill": name,
        "display_name": root["display_name"],
        "description": root.get("description", ""),
        "scripts": root["scripts"],
        "skills": root["skills"],
        "interfaces": root["interfaces"],
        "interface_catalog": authoring_interface_catalog(),
        "diagnostics": diagnostics,
    }
