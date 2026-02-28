"""
Unit tests for k8s_mcp/tools/diagnostics.py handlers and _check_* helpers.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from mcp.types import TextContent

from k8s_mcp.kubectl import KubectlError
from k8s_mcp.tools.diagnostics import (
    _check_daemonsets,
    _check_deployments,
    _check_events,
    _check_jobs,
    _check_nodes,
    _check_pods,
    _check_pvcs,
    _check_statefulsets,
    handle_describe,
    handle_exec,
    handle_find_issues,
    handle_get_yaml,
    handle_logs,
    handle_logs_selector,
    handle_rollout_history,
    handle_rollout_status,
    handle_self_test,
)


# ---------------------------------------------------------------------------
# _check_pods — now returns (critical, warning) tuple
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
        critical, warning = await _check_pods(None, None, False, 5)
    assert critical == []
    assert warning == []


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
        critical, warning = await _check_pods(None, None, False, 5)
    assert len(critical) == 0
    assert len(warning) == 1
    assert "pending-pod" in warning[0]
    assert "Pending" in warning[0]


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
        critical, warning = await _check_pods(None, None, False, 5)
    assert len(critical) == 1
    assert "restarted 10 times" in critical[0]


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
        critical, warning = await _check_pods(None, None, False, 5)
    assert len(critical) == 1
    assert "CrashLoopBackOff" in critical[0]


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
        critical, warning = await _check_pods(None, None, False, 5)
    assert critical == []
    assert warning == []


async def test_check_pods_crashloop_suggests_logs():
    """Critical pod issues should include next-step suggestions."""
    data = {
        "items": [
            {
                "metadata": {"name": "crash-pod", "namespace": "prod"},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [
                        {
                            "name": "app",
                            "restartCount": 15,
                            "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                        }
                    ],
                },
            }
        ]
    }
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value=data):
        critical, _ = await _check_pods(None, None, False, 5)
    assert len(critical) >= 1
    combined = "\n".join(critical)
    assert "k8s_logs" in combined


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
    assert "unavailable" in issues[0].lower()


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
    assert "unavailable" in issues[0].lower() or "down" in issues[0].lower()


# ---------------------------------------------------------------------------
# _check_statefulsets
# ---------------------------------------------------------------------------

async def test_check_statefulsets_healthy():
    data = {"items": [
        {"metadata": {"name": "db", "namespace": "default"}, "spec": {"replicas": 3}, "status": {"readyReplicas": 3}}
    ]}
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value=data):
        issues = await _check_statefulsets(None, None, False)
    assert issues == []


async def test_check_statefulsets_unavailable():
    data = {"items": [
        {"metadata": {"name": "db", "namespace": "default"}, "spec": {"replicas": 3}, "status": {"readyReplicas": 1}}
    ]}
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value=data):
        issues = await _check_statefulsets(None, None, False)
    assert len(issues) == 1
    assert "db" in issues[0]


# ---------------------------------------------------------------------------
# _check_jobs
# ---------------------------------------------------------------------------

async def test_check_jobs_healthy():
    data = {"items": [
        {"metadata": {"name": "migrate", "namespace": "default"}, "status": {"succeeded": 1, "conditions": []}}
    ]}
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value=data):
        issues = await _check_jobs(None, None, False)
    assert issues == []


async def test_check_jobs_failed():
    data = {"items": [
        {
            "metadata": {"name": "bad-job", "namespace": "default"},
            "status": {
                "failed": 3, "succeeded": 0,
                "conditions": [{"type": "Failed", "status": "True", "reason": "BackoffLimitExceeded"}],
            },
        }
    ]}
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value=data):
        issues = await _check_jobs(None, None, False)
    assert len(issues) == 1
    assert "bad-job" in issues[0]
    assert "failed" in issues[0].lower()


# ---------------------------------------------------------------------------
# _check_pvcs
# ---------------------------------------------------------------------------

async def test_check_pvcs_all_bound():
    data = {"items": [
        {"metadata": {"name": "data", "namespace": "default"}, "status": {"phase": "Bound"}, "spec": {}}
    ]}
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value=data):
        issues = await _check_pvcs(None, None, False)
    assert issues == []


async def test_check_pvcs_pending():
    data = {"items": [
        {"metadata": {"name": "stuck-pvc", "namespace": "default"}, "status": {"phase": "Pending"}, "spec": {"storageClassName": "gp2"}}
    ]}
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value=data):
        issues = await _check_pvcs(None, None, False)
    assert len(issues) == 1
    assert "stuck-pvc" in issues[0]
    assert "Pending" in issues[0]


# ---------------------------------------------------------------------------
# _check_events — now uses kubectl_json and returns (lines, event_map) tuple
# ---------------------------------------------------------------------------

async def test_check_events_returns_last_20():
    events_data = {
        "items": [
            {
                "involvedObject": {"name": f"pod-{i}", "kind": "Pod"},
                "metadata": {"namespace": "default", "creationTimestamp": "2026-02-27T10:00:00Z"},
                "reason": "BackOff",
                "message": "Back-off restarting",
                "lastTimestamp": "2026-02-27T10:00:00Z",
                "count": 1,
            }
            for i in range(25)
        ]
    }
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value=events_data):
        lines, event_map = await _check_events(None, None, False)
    assert len(lines) == 20


async def test_check_events_kubectl_error_returns_empty():
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", side_effect=KubectlError("forbidden")):
        lines, event_map = await _check_events(None, None, False)
    assert lines == []
    assert event_map == {}


# ---------------------------------------------------------------------------
# handle_find_issues — now needs all 8 check mocks
# ---------------------------------------------------------------------------

def _patch_all_checks(**overrides):
    """Helper to create patch context managers for all 8 check functions."""
    defaults = {
        "_check_pods": ([], []),
        "_check_nodes": [],
        "_check_deployments": [],
        "_check_statefulsets": [],
        "_check_daemonsets": [],
        "_check_jobs": [],
        "_check_pvcs": [],
        "_check_events": ([], {}),
    }
    defaults.update(overrides)
    return {
        name: patch(f"k8s_mcp.tools.diagnostics.{name}", return_value=value)
        for name, value in defaults.items()
    }


async def test_find_issues_no_problems():
    p = _patch_all_checks()
    with p["_check_pods"], p["_check_nodes"], p["_check_deployments"], \
         p["_check_statefulsets"], p["_check_daemonsets"], p["_check_jobs"], \
         p["_check_pvcs"], p["_check_events"]:
        result = await handle_find_issues({})
    assert "No issues" in result[0].text


async def test_find_issues_reports_critical_pod():
    p = _patch_all_checks(_check_pods=(["[default/crasher] CrashLoopBackOff"], []))
    with p["_check_pods"], p["_check_nodes"], p["_check_deployments"], \
         p["_check_statefulsets"], p["_check_daemonsets"], p["_check_jobs"], \
         p["_check_pvcs"], p["_check_events"]:
        result = await handle_find_issues({})
    assert "CRITICAL" in result[0].text
    assert "crasher" in result[0].text


async def test_find_issues_reports_warning_pods():
    p = _patch_all_checks(_check_pods=([], ["[default/pending-pod] phase=Pending"]))
    with p["_check_pods"], p["_check_nodes"], p["_check_deployments"], \
         p["_check_statefulsets"], p["_check_daemonsets"], p["_check_jobs"], \
         p["_check_pvcs"], p["_check_events"]:
        result = await handle_find_issues({})
    assert "WARNING" in result[0].text
    assert "pending-pod" in result[0].text


async def test_find_issues_check_exception_reported():
    p = _patch_all_checks()
    p["_check_pods"] = patch("k8s_mcp.tools.diagnostics._check_pods", side_effect=Exception("timeout"))
    with p["_check_pods"], p["_check_nodes"], p["_check_deployments"], \
         p["_check_statefulsets"], p["_check_daemonsets"], p["_check_jobs"], \
         p["_check_pvcs"], p["_check_events"]:
        result = await handle_find_issues({})
    assert "scan failed" in result[0].text or "timeout" in result[0].text


async def test_find_issues_severity_counts():
    p = _patch_all_checks(
        _check_pods=(["critical-issue"], []),
        _check_nodes=["node-issue"],
    )
    with p["_check_pods"], p["_check_nodes"], p["_check_deployments"], \
         p["_check_statefulsets"], p["_check_daemonsets"], p["_check_jobs"], \
         p["_check_pvcs"], p["_check_events"]:
        result = await handle_find_issues({})
    text = result[0].text
    assert "2 critical" in text
    assert "0 warning" in text


async def test_find_issues_includes_statefulset_job_pvc():
    """New check categories should appear in output."""
    p = _patch_all_checks(
        _check_statefulsets=["[default/db] statefulset unavailable"],
        _check_jobs=["[default/migrate] Job failed"],
        _check_pvcs=["[default/data-pvc] PVC phase=Pending"],
    )
    with p["_check_pods"], p["_check_nodes"], p["_check_deployments"], \
         p["_check_statefulsets"], p["_check_daemonsets"], p["_check_jobs"], \
         p["_check_pvcs"], p["_check_events"]:
        result = await handle_find_issues({})
    text = result[0].text
    assert "db" in text
    assert "migrate" in text
    assert "data-pvc" in text


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


async def test_handle_logs_error_filter():
    raw_logs = "\n".join([
        "INFO: Starting app",
        "INFO: Ready",
        "ERROR: Connection refused to db:5432",
        "INFO: Retrying",
        "INFO: Processing",
        "FATAL: Shutting down",
        "INFO: Cleanup",
    ])
    with patch("k8s_mcp.tools.diagnostics.kubectl", return_value=raw_logs):
        result = await handle_logs({"pod_name": "my-pod", "filter": "errors"})
    text = result[0].text
    assert "match error patterns" in text
    assert "ERROR" in text
    assert "FATAL" in text


# ---------------------------------------------------------------------------
# handle_get_yaml — now uses kubectl_json by default (strips managed fields)
# ---------------------------------------------------------------------------

async def test_handle_get_yaml_success():
    mock_data = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "my-pod",
            "managedFields": [{"manager": "kubectl"}],
            "annotations": {
                "kubectl.kubernetes.io/last-applied-configuration": "{}",
            },
        },
    }
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value=mock_data):
        result = await handle_get_yaml({"resource_type": "pod", "resource_name": "my-pod"})
    text = result[0].text
    assert "apiVersion" in text
    assert "managedFields" not in text
    assert "last-applied-configuration" not in text


async def test_handle_get_yaml_raw_mode():
    with patch("k8s_mcp.tools.diagnostics.kubectl", return_value="apiVersion: v1\nkind: Pod"):
        result = await handle_get_yaml({"resource_type": "pod", "resource_name": "my-pod", "raw": True})
    assert "apiVersion" in result[0].text


async def test_handle_get_yaml_error():
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", side_effect=KubectlError("not found")):
        result = await handle_get_yaml({"resource_type": "pod", "resource_name": "ghost"})
    assert "Error" in result[0].text


# ---------------------------------------------------------------------------
# _check_daemonsets
# ---------------------------------------------------------------------------


async def test_check_daemonsets_no_issues():
    data = {
        "items": [
            {
                "metadata": {"namespace": "kube-system", "name": "fluentd"},
                "status": {"desiredNumberScheduled": 3, "numberReady": 3},
            }
        ]
    }
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value=data):
        issues = await _check_daemonsets(None, None, True)
    assert issues == []


async def test_check_daemonsets_not_ready():
    data = {
        "items": [
            {
                "metadata": {"namespace": "kube-system", "name": "fluentd"},
                "status": {"desiredNumberScheduled": 3, "numberReady": 1},
            }
        ]
    }
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value=data):
        issues = await _check_daemonsets(None, None, True)
    assert len(issues) == 1
    assert "fluentd" in issues[0]
    assert "2/3" in issues[0]


async def test_check_daemonsets_none_ready():
    data = {
        "items": [
            {
                "metadata": {"namespace": "default", "name": "node-exporter"},
                "status": {"desiredNumberScheduled": 2, "numberReady": None},
            }
        ]
    }
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value=data):
        issues = await _check_daemonsets(None, None, True)
    assert len(issues) == 1
    assert "2/2" in issues[0]


async def test_check_daemonsets_empty():
    with patch("k8s_mcp.tools.diagnostics.kubectl_json", return_value={"items": []}):
        issues = await _check_daemonsets(None, None, True)
    assert issues == []


# ---------------------------------------------------------------------------
# handle_exec
# ---------------------------------------------------------------------------


async def test_handle_exec_success():
    with patch("k8s_mcp.tools.diagnostics.kubectl", return_value="uid=0(root)") as m:
        result = await handle_exec({"pod_name": "my-pod", "command": "id"})
    text = result[0].text
    assert "uid=0" in text
    called_args = m.call_args
    cmd = called_args[0][0]
    assert cmd[0] == "exec"
    assert "my-pod" in cmd
    assert "--" in cmd
    assert "id" in cmd


async def test_handle_exec_with_container():
    with patch("k8s_mcp.tools.diagnostics.kubectl", return_value="ok") as m:
        await handle_exec({"pod_name": "my-pod", "command": "ls", "container": "sidecar"})
    cmd = m.call_args[0][0]
    assert "-c" in cmd
    assert "sidecar" in cmd


async def test_handle_exec_no_output():
    with patch("k8s_mcp.tools.diagnostics.kubectl", return_value=""):
        result = await handle_exec({"pod_name": "my-pod", "command": "true"})
    assert "no output" in result[0].text


async def test_handle_exec_error():
    with patch("k8s_mcp.tools.diagnostics.kubectl", side_effect=KubectlError("container not found")):
        result = await handle_exec({"pod_name": "my-pod", "command": "id"})
    assert "Error" in result[0].text
    assert "container not found" in result[0].text


async def test_handle_exec_passes_context_and_namespace():
    with patch("k8s_mcp.tools.diagnostics.kubectl", return_value="ok") as m:
        await handle_exec({"pod_name": "p", "command": "ls", "context": "prod", "namespace": "app"})
    kwargs = m.call_args[1]
    assert kwargs["context"] == "prod"
    assert kwargs["namespace"] == "app"


# ---------------------------------------------------------------------------
# handle_logs_selector
# ---------------------------------------------------------------------------


async def test_handle_logs_selector_success():
    with patch("k8s_mcp.tools.diagnostics.kubectl", return_value="log line 1\nlog line 2") as m:
        result = await handle_logs_selector({"label_selector": "app=web"})
    text = result[0].text
    assert "log line" in text
    cmd = m.call_args[0][0]
    assert "logs" in cmd
    assert "-l" in cmd
    assert "app=web" in cmd
    assert "--prefix=true" in cmd


async def test_handle_logs_selector_default_tail():
    with patch("k8s_mcp.tools.diagnostics.kubectl", return_value="x") as m:
        await handle_logs_selector({"label_selector": "app=api"})
    cmd = m.call_args[0][0]
    assert "--tail=50" in cmd


async def test_handle_logs_selector_custom_tail():
    with patch("k8s_mcp.tools.diagnostics.kubectl", return_value="x") as m:
        await handle_logs_selector({"label_selector": "app=api", "tail": 100})
    cmd = m.call_args[0][0]
    assert "--tail=100" in cmd


async def test_handle_logs_selector_with_since():
    with patch("k8s_mcp.tools.diagnostics.kubectl", return_value="x") as m:
        await handle_logs_selector({"label_selector": "app=api", "since": "1h"})
    cmd = m.call_args[0][0]
    assert "--since=1h" in cmd


async def test_handle_logs_selector_with_container():
    with patch("k8s_mcp.tools.diagnostics.kubectl", return_value="x") as m:
        await handle_logs_selector({"label_selector": "app=api", "container": "proxy"})
    cmd = m.call_args[0][0]
    assert "-c" in cmd
    assert "proxy" in cmd


async def test_handle_logs_selector_no_output():
    with patch("k8s_mcp.tools.diagnostics.kubectl", return_value=""):
        result = await handle_logs_selector({"label_selector": "app=idle"})
    assert "no log output" in result[0].text


async def test_handle_logs_selector_error():
    with patch("k8s_mcp.tools.diagnostics.kubectl", side_effect=KubectlError("selector invalid")):
        result = await handle_logs_selector({"label_selector": "bad=selector"})
    assert "Error" in result[0].text


# ---------------------------------------------------------------------------
# handle_rollout_status
# ---------------------------------------------------------------------------


async def test_handle_rollout_status_success():
    with patch("k8s_mcp.tools.diagnostics.kubectl", return_value="deployment successfully rolled out") as m:
        result = await handle_rollout_status({"deployment_name": "my-app"})
    assert "successfully rolled out" in result[0].text
    cmd = m.call_args[0][0]
    assert cmd == ["rollout", "status", "deployment/my-app", "--timeout=30s"]


async def test_handle_rollout_status_passes_context_namespace():
    with patch("k8s_mcp.tools.diagnostics.kubectl", return_value="ok") as m:
        await handle_rollout_status({"deployment_name": "api", "context": "staging", "namespace": "backend"})
    kwargs = m.call_args[1]
    assert kwargs["context"] == "staging"
    assert kwargs["namespace"] == "backend"


async def test_handle_rollout_status_error():
    with patch("k8s_mcp.tools.diagnostics.kubectl", side_effect=KubectlError("timed out")):
        result = await handle_rollout_status({"deployment_name": "broken"})
    assert "Error" in result[0].text
    assert "timed out" in result[0].text


# ---------------------------------------------------------------------------
# handle_rollout_history
# ---------------------------------------------------------------------------


async def test_handle_rollout_history_success():
    history = "REVISION  CHANGE-CAUSE\n1         <none>\n2         bump image"
    with patch("k8s_mcp.tools.diagnostics.kubectl", return_value=history) as m:
        result = await handle_rollout_history({"deployment_name": "my-app"})
    assert "REVISION" in result[0].text
    cmd = m.call_args[0][0]
    assert cmd == ["rollout", "history", "deployment/my-app"]


async def test_handle_rollout_history_passes_context_namespace():
    with patch("k8s_mcp.tools.diagnostics.kubectl", return_value="x") as m:
        await handle_rollout_history({"deployment_name": "svc", "context": "prod", "namespace": "ns"})
    kwargs = m.call_args[1]
    assert kwargs["context"] == "prod"
    assert kwargs["namespace"] == "ns"


async def test_handle_rollout_history_error():
    with patch("k8s_mcp.tools.diagnostics.kubectl", side_effect=KubectlError("not found")):
        result = await handle_rollout_history({"deployment_name": "ghost"})
    assert "Error" in result[0].text

