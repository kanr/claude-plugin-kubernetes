"""
Unit tests for src/tools/diagnostics.py handlers and _check_* helpers.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from mcp.types import TextContent

from k8s_mcp.kubectl import KubectlError
from k8s_mcp.tools.diagnostics import (
    _check_deployments,
    _check_events,
    _check_nodes,
    _check_pods,
    handle_describe,
    handle_find_issues,
    handle_get_yaml,
    handle_logs,
)
from tests.conftest import DEPLOYMENTS_JSON, EVENTS_TEXT, NODES_JSON, PODS_JSON


# ---------------------------------------------------------------------------
# _check_pods
# ---------------------------------------------------------------------------

async def test_check_pods_all_healthy():
    healthy = {
        "items": [
            {
                "metadata": {"name": "app", "namespace": "default"},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [{"name": "app", "restartCount": 0, "state": {}}],
                },
            }
        ]
    }
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value=healthy):
        issues = await _check_pods(None, None, False, 5)
    assert issues == []


async def test_check_pods_pending_phase():
    data = {
        "items": [
            {
                "metadata": {"name": "pending-pod", "namespace": "default"},
                "status": {"phase": "Pending", "reason": "Unschedulable", "containerStatuses": []},
            }
        ]
    }
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value=data):
        issues = await _check_pods(None, None, False, 5)
    assert len(issues) == 1
    assert "pending-pod" in issues[0]
    assert "Pending" in issues[0]


async def test_check_pods_high_restarts():
    data = {
        "items": [
            {
                "metadata": {"name": "crasher", "namespace": "kube-system"},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [
                        {"name": "crasher", "restartCount": 10, "state": {}}
                    ],
                },
            }
        ]
    }
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value=data):
        issues = await _check_pods(None, None, False, 5)
    assert len(issues) == 1
    assert "restarts=10" in issues[0]


async def test_check_pods_waiting_reason_appended():
    data = {
        "items": [
            {
                "metadata": {"name": "crasher", "namespace": "default"},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [
                        {
                            "name": "app",
                            "restartCount": 8,
                            "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                        }
                    ],
                },
            }
        ]
    }
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value=data):
        issues = await _check_pods(None, None, False, 5)
    assert "CrashLoopBackOff" in issues[0]


async def test_check_pods_below_threshold_not_flagged():
    data = {
        "items": [
            {
                "metadata": {"name": "app", "namespace": "default"},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [{"name": "app", "restartCount": 4, "state": {}}],
                },
            }
        ]
    }
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value=data):
        issues = await _check_pods(None, None, False, 5)
    assert issues == []


# ---------------------------------------------------------------------------
# _check_nodes
# ---------------------------------------------------------------------------

async def test_check_nodes_all_ready():
    data = {
        "items": [
            {
                "metadata": {"name": "node1"},
                "status": {
                    "conditions": [
                        {"type": "Ready", "status": "True", "reason": "KubeletReady", "message": ""}
                    ]
                },
            }
        ]
    }
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value=data):
        issues = await _check_nodes(None)
    assert issues == []


async def test_check_nodes_not_ready():
    data = {
        "items": [
            {
                "metadata": {"name": "bad-node"},
                "status": {
                    "conditions": [
                        {
                            "type": "Ready",
                            "status": "False",
                            "reason": "KubeletNotReady",
                            "message": "PLEG unhealthy",
                        }
                    ]
                },
            }
        ]
    }
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value=data):
        issues = await _check_nodes(None)
    assert len(issues) == 1
    assert "bad-node" in issues[0]
    assert "ISSUES" in issues[0]


# ---------------------------------------------------------------------------
# _check_deployments
# ---------------------------------------------------------------------------

async def test_check_deployments_healthy():
    data = {
        "items": [
            {
                "metadata": {"name": "app", "namespace": "default"},
                "spec": {"replicas": 3},
                "status": {"readyReplicas": 3, "availableReplicas": 3},
            }
        ]
    }
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value=data):
        issues = await _check_deployments(None, None, False)
    assert issues == []


async def test_check_deployments_unavailable():
    data = {
        "items": [
            {
                "metadata": {"name": "broken", "namespace": "default"},
                "spec": {"replicas": 2},
                "status": {"readyReplicas": 1, "unavailableReplicas": 1},
            }
        ]
    }
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value=data):
        issues = await _check_deployments(None, None, False)
    assert len(issues) == 1
    assert "broken" in issues[0]
    assert "unavailable=1" in issues[0]


async def test_check_deployments_all_down():
    data = {
        "items": [
            {
                "metadata": {"name": "down-app", "namespace": "default"},
                "spec": {"replicas": 3},
                "status": {"readyReplicas": 0},
            }
        ]
    }
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value=data):
        issues = await _check_deployments(None, None, False)
    assert len(issues) == 1
    assert "ready=0" in issues[0]


# ---------------------------------------------------------------------------
# _check_events
# ---------------------------------------------------------------------------

async def test_check_events_returns_last_20():
    with patch("k8s_mcp.tools.diagnostics.kubectl", return_value=EVENTS_TEXT):
        issues = await _check_events(None, None, False)
    assert len(issues) == 20


async def test_check_events_kubectl_error_returns_empty():
    with patch("k8s_mcp.tools.diagnostics.kubectl", side_effect=KubectlError("forbidden")):
        issues = await _check_events(None, None, False)
    assert issues == []


# ---------------------------------------------------------------------------
# handle_find_issues
# ---------------------------------------------------------------------------

async def test_find_issues_no_problems():
    with patch("k8s_mcp.tools.diagnostics._check_pods", return_value=[]), \
         patch("k8s_mcp.tools.diagnostics._check_nodes", return_value=[]), \
         patch("k8s_mcp.tools.diagnostics._check_deployments", return_value=[]), \
         patch("k8s_mcp.tools.diagnostics._check_events", return_value=[]):
        result = await handle_find_issues({})

    assert "No issues" in result[0].text


async def test_find_issues_reports_pod_section():
    with patch("k8s_mcp.tools.diagnostics._check_pods", return_value=["[default] crasher restarts=10"]), \
         patch("k8s_mcp.tools.diagnostics._check_nodes", return_value=[]), \
         patch("k8s_mcp.tools.diagnostics._check_deployments", return_value=[]), \
         patch("k8s_mcp.tools.diagnostics._check_events", return_value=[]):
        result = await handle_find_issues({})

    assert "Pod Issues" in result[0].text
    assert "crasher" in result[0].text


async def test_find_issues_check_exception_reported():
    with patch("k8s_mcp.tools.diagnostics._check_pods", side_effect=Exception("timeout")), \
         patch("k8s_mcp.tools.diagnostics._check_nodes", return_value=[]), \
         patch("k8s_mcp.tools.diagnostics._check_deployments", return_value=[]), \
         patch("k8s_mcp.tools.diagnostics._check_events", return_value=[]):
        result = await handle_find_issues({})

    assert "scan failed" in result[0].text
    assert "timeout" in result[0].text


async def test_find_issues_counts_categories_correctly():
    with patch("k8s_mcp.tools.diagnostics._check_pods", return_value=["issue1"]), \
         patch("k8s_mcp.tools.diagnostics._check_nodes", return_value=["issue2"]), \
         patch("k8s_mcp.tools.diagnostics._check_deployments", return_value=[]), \
         patch("k8s_mcp.tools.diagnostics._check_events", return_value=[]):
        result = await handle_find_issues({})

    assert "2 categories" in result[0].text


# ---------------------------------------------------------------------------
# handle_describe
# ---------------------------------------------------------------------------

async def test_handle_describe_success():
    with patch("k8s_mcp.tools.diagnostics.kubectl", return_value="Name: my-pod\nNamespace: default"):
        result = await handle_describe({"resource_type": "pod", "resource_name": "my-pod"})
    assert "my-pod" in result[0].text


async def test_handle_describe_error():
    with patch("k8s_mcp.tools.diagnostics.kubectl", side_effect=KubectlError("not found")):
        result = await handle_describe({"resource_type": "pod", "resource_name": "ghost"})
    assert "Error" in result[0].text


# ---------------------------------------------------------------------------
# handle_logs
# ---------------------------------------------------------------------------

async def test_handle_logs_default_tail():
    with patch("k8s_mcp.tools.diagnostics.kubectl", return_value="log line") as mock_kctl:
        await handle_logs({"pod_name": "my-pod"})
    cmd = mock_kctl.call_args[0][0]
    assert "--tail=100" in cmd


async def test_handle_logs_with_options():
    with patch("k8s_mcp.tools.diagnostics.kubectl", return_value="log") as mock_kctl:
        await handle_logs({
            "pod_name": "my-pod",
            "namespace": "default",
            "container": "sidecar",
            "tail": 50,
            "previous": True,
            "since": "5m",
        })
    cmd = mock_kctl.call_args[0][0]
    assert "--tail=50" in cmd
    assert "-c" in cmd
    assert "sidecar" in cmd
    assert "--previous" in cmd
    assert "--since=5m" in cmd


async def test_handle_logs_empty_output():
    with patch("k8s_mcp.tools.diagnostics.kubectl", return_value=""):
        result = await handle_logs({"pod_name": "my-pod"})
    assert "no log output" in result[0].text


# ---------------------------------------------------------------------------
# handle_get_yaml
# ---------------------------------------------------------------------------

async def test_handle_get_yaml_success():
    with patch("k8s_mcp.tools.diagnostics.kubectl", return_value="apiVersion: v1\nkind: Pod"):
        result = await handle_get_yaml({"resource_type": "pod", "resource_name": "my-pod"})
    assert "apiVersion" in result[0].text


async def test_handle_get_yaml_error():
    with patch("k8s_mcp.tools.diagnostics.kubectl", side_effect=KubectlError("not found")):
        result = await handle_get_yaml({"resource_type": "pod", "resource_name": "ghost"})
    assert "Error" in result[0].text
