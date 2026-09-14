"""Identify public simulation MCP references without loading Runtime or Studio."""

import re


# Keep this catalog aligned with SimulationMCPServer.tools (checked by tests).
SIMULATION_MCP_TOOLS = (
    "world-perceive",
    "world-navigate",
    "world-act",
    "memory-stream-search",
    "memory-stream-append",
    "memory-stream-supersede",
    "memory-stream-invalidate",
)


def referenced_mcp_tools(markdown: str) -> list[str]:
    """Return unique supported tool names explicitly referenced by the Skill."""
    return [
        name for name in SIMULATION_MCP_TOOLS
        if re.search(r"(?<![A-Za-z0-9_-])" + re.escape(name) + r"(?![A-Za-z0-9_-])", markdown)
    ]
