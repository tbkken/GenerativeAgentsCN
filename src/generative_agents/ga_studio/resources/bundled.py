"""Explicit Studio-owned bundled author inputs."""
from pathlib import Path
from generative_agents.ga_protocol.skills.documents import SkillRegistry

BUNDLED_ROOT = Path(__file__).resolve().parents[1] / 'bundled'

def bundled_skills():
    return SkillRegistry(BUNDLED_ROOT / 'skills')
