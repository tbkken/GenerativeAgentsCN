"""Private Skill inputs for parser and executor regressions."""

from pathlib import Path

from generative_agents.ga_protocol.skills.documents import SkillRegistry


def fixture_skills() -> SkillRegistry:
    return SkillRegistry(Path(__file__).parent / "skills")
