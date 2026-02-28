"""Shared output formatting helpers."""

from __future__ import annotations

from typing import Any

from mcp.types import TextContent


# ---------------------------------------------------------------------------
# Shared error helper
# ---------------------------------------------------------------------------

class ToolError(list):
    """Sentinel list subclass returned by tool handlers to indicate an error.

    Wraps a ``list[TextContent]`` so existing handler return-type contracts
    are preserved while ``server.py`` can detect errors via ``isinstance()``.
    """


def _err(msg: str) -> list[TextContent]:
    """Return an error response that ``server.py`` will mark with ``isError=True``."""
    result = ToolError([TextContent(type="text", text=f"Error: {msg}")])
    return result


def section(title: str, body: str) -> str:
    """Format a titled section."""
    bar = "─" * len(title)
    return f"{title}\n{bar}\n{body}"


def bullet_list(items: list[str]) -> str:
    return "\n".join(f"  • {item}" for item in items)


def kv_table(pairs: list[tuple[str, Any]], indent: int = 0) -> str:
    if not pairs:
        return ""
    max_key = max(len(str(k)) for k, _ in pairs)
    pad = " " * indent
    lines = [f"{pad}{str(k).ljust(max_key)}  {v}" for k, v in pairs]
    return "\n".join(lines)


def severity_icon(level: str) -> str:
    return {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(level, "⚪")


def node_conditions_summary(conditions: list[dict]) -> str:
    """Summarise node conditions from the JSON .status.conditions list."""
    issues = []
    healthy = []
    for c in conditions:
        ctype = c.get("type", "")
        status = c.get("status", "")
        reason = c.get("reason", "")
        msg = c.get("message", "")

        # Ready=True is good; all others False/Unknown is good
        if ctype == "Ready":
            if status != "True":
                issues.append(f"NotReady ({reason}): {msg}")
            else:
                healthy.append("Ready")
        else:
            if status == "True":
                issues.append(f"{ctype} ({reason}): {msg}")

    result_parts = []
    if healthy:
        result_parts.append(", ".join(healthy))
    if issues:
        result_parts.append("ISSUES: " + " | ".join(issues))
    return " | ".join(result_parts) if result_parts else "Unknown"
