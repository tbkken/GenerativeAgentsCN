"""Private helper used by the perception-and-memory Skill pack."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime
from typing import Any, Mapping

from generative_agents.skills.mcp import MemoryStream


def append_memory(input_text: str, context: Mapping[str, Any]) -> str:
    """Persist one natural-language memory and return a handoff sentence."""

    store = _store(context)
    item = store.append(
        agent_key=str(context["agent_key"]),
        content=input_text,
        kind=str(context.get("kind") or "event"),
        poignancy=int(context.get("poignancy") or 1),
    )
    return f"Memory {item['id']} stored for {item['agent_key']}: {item['content']}"


def recall_memories(input_text: str, context: Mapping[str, Any]) -> str:
    """Return matching memories as readable text for the next Skill."""

    items = _store(context).search(
        agent_key=str(context["agent_key"]),
        query=input_text,
        limit=int(context.get("limit") or 8),
    )
    if not items:
        return "No relevant memories were found."
    return "\n".join(
        f"- [{item['created_at']}] ({item['kind']}, importance {item['poignancy']}) "
        f"{item['content']}"
        for item in items
    )


def _store(context: Mapping[str, Any]) -> MemoryStream:
    path = Path(str(context.get("memory_database") or "var/skill-memory.db"))
    store = MemoryStream(
        path,
        run_id=str(context.get("run_id") or "skill-workspace"),
        attempt_id=str(context.get("attempt_id") or "skill-workspace"),
    )
    if context.get("step_no") and context.get("virtual_time"):
        store.begin_step(
            int(context["step_no"]),
            datetime.fromisoformat(str(context["virtual_time"])),
        )
    return store
