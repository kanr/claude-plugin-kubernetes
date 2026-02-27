"""
Unit tests for src/formatters.py — all pure functions, no mocking needed.
"""

from k8s_mcp.formatters import (
    bullet_list,
    kv_table,
    node_conditions_summary,
    section,
    severity_icon,
)


# ---------------------------------------------------------------------------
# section()
# ---------------------------------------------------------------------------

def test_section_format():
    result = section("Title", "body text")
    lines = result.splitlines()
    assert lines[0] == "Title"
    assert lines[1] == "─" * len("Title")
    assert lines[2] == "body text"


def test_section_bar_matches_title_length():
    title = "Short"
    result = section(title, "x")
    bar = result.splitlines()[1]
    assert len(bar) == len(title)


# ---------------------------------------------------------------------------
# bullet_list()
# ---------------------------------------------------------------------------

def test_bullet_list_prefix():
    result = bullet_list(["alpha", "beta"])
    assert result == "  • alpha\n  • beta"


def test_bullet_list_empty():
    assert bullet_list([]) == ""


# ---------------------------------------------------------------------------
# kv_table()
# ---------------------------------------------------------------------------

def test_kv_table_alignment():
    pairs = [("short", "v1"), ("a-longer-key", "v2")]
    result = kv_table(pairs)
    lines = result.splitlines()
    # Both value columns should start at the same column
    col0 = lines[0].index("v1")
    col1 = lines[1].index("v2")
    assert col0 == col1


def test_kv_table_empty():
    assert kv_table([]) == ""


def test_kv_table_indent():
    result = kv_table([("k", "v")], indent=4)
    assert result.startswith("    ")


# ---------------------------------------------------------------------------
# severity_icon()
# ---------------------------------------------------------------------------

def test_severity_icon_critical():
    assert severity_icon("critical") == "🔴"


def test_severity_icon_warning():
    assert severity_icon("warning") == "🟡"


def test_severity_icon_info():
    assert severity_icon("info") == "🔵"


def test_severity_icon_unknown():
    assert severity_icon("something-else") == "⚪"


# ---------------------------------------------------------------------------
# node_conditions_summary()
# ---------------------------------------------------------------------------

def test_node_conditions_ready():
    conditions = [{"type": "Ready", "status": "True", "reason": "KubeletReady", "message": ""}]
    result = node_conditions_summary(conditions)
    assert "Ready" in result
    assert "ISSUES" not in result


def test_node_conditions_not_ready():
    conditions = [
        {"type": "Ready", "status": "False", "reason": "KubeletNotReady", "message": "PLEG unhealthy"}
    ]
    result = node_conditions_summary(conditions)
    assert "ISSUES" in result
    assert "NotReady" in result
    assert "PLEG unhealthy" in result


def test_node_conditions_memory_pressure():
    conditions = [
        {"type": "Ready", "status": "True", "reason": "KubeletReady", "message": ""},
        {"type": "MemoryPressure", "status": "True", "reason": "KubeletHasInsufficientMemory", "message": "low mem"},
    ]
    result = node_conditions_summary(conditions)
    assert "ISSUES" in result
    assert "MemoryPressure" in result


def test_node_conditions_pressure_false_is_healthy():
    conditions = [
        {"type": "Ready", "status": "True", "reason": "KubeletReady", "message": ""},
        {"type": "MemoryPressure", "status": "False", "reason": "KubeletHasSufficientMemory", "message": ""},
    ]
    result = node_conditions_summary(conditions)
    assert "ISSUES" not in result


def test_node_conditions_empty():
    assert node_conditions_summary([]) == "Unknown"
