"""Actor description and current activity, independent of Brain implementation."""
from dataclasses import dataclass
from typing import Any

@dataclass
class ActorProfile:
    config: dict[str, Any]
    currently: str = ""
