"""
Unit tests for src/tools/remediation.py handlers.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from mcp.types import TextContent

from k8s_mcp.kubectl import KubectlError
from k8s_mcp.tools.remediation import (
    handle_apply_manifest,
    handle_delete_pod,
    handle_node_operation,
    handle_patch_resource,
    handle_restart_deployment,
    handle_rollback_deployment,
    handle_scale,
)


# ---------------------------------------------------------------------------
# handle_restart_deployment
# ---------------------------------------------------------------------------

async def test_restart_deployment_chains_two_calls():
    with patch("k8s_mcp.tools.remediation.kubectl", new_callable=AsyncMock) as mock_kctl:
        mock_kctl.side_effect = ["restarted", "successfully rolled out"]
        result = await handle_restart_deployment({"deployment_name": "my-app"})

    assert mock_kctl.call_count == 2
    restart_cmd = mock_kctl.call_args_list[0][0][0]
    status_cmd = mock_kctl.call_args_list[1][0][0]
    assert "restart" in restart_cmd
    assert "deployment/my-app" in restart_cmd
    assert "status" in status_cmd
    assert "restarted" in result[0].text
    assert "rolled out" in result[0].text


async def test_restart_deployment_error():
    with patch("k8s_mcp.tools.remediation.kubectl", side_effect=KubectlError("not found")):
        result = await handle_restart_deployment({"deployment_name": "ghost"})
    assert "Error" in result[0].text


# ---------------------------------------------------------------------------
# handle_scale
# ---------------------------------------------------------------------------

async def test_scale_deployment():
    with patch("k8s_mcp.tools.remediation.kubectl", return_value="scaled") as mock_kctl:
        result = await handle_scale({
            "resource_type": "deployment",
            "resource_name": "my-app",
            "replicas": 3,
        })
    cmd = mock_kctl.call_args[0][0]
    assert "scale" in cmd
    assert "deployment/my-app" in cmd
    assert "--replicas=3" in cmd
    assert "scaled" in result[0].text


async def test_scale_statefulset():
    with patch("k8s_mcp.tools.remediation.kubectl", return_value="scaled") as mock_kctl:
        await handle_scale({
            "resource_type": "statefulset",
            "resource_name": "db",
            "replicas": 1,
        })
    cmd = mock_kctl.call_args[0][0]
    assert "statefulset/db" in cmd


async def test_scale_to_zero():
    with patch("k8s_mcp.tools.remediation.kubectl", return_value="scaled") as mock_kctl:
        await handle_scale({"resource_type": "deployment", "resource_name": "app", "replicas": 0})
    cmd = mock_kctl.call_args[0][0]
    assert "--replicas=0" in cmd


# ---------------------------------------------------------------------------
# handle_delete_pod
# ---------------------------------------------------------------------------

async def test_delete_pod_normal():
    with patch("k8s_mcp.tools.remediation.kubectl", return_value="pod deleted") as mock_kctl:
        result = await handle_delete_pod({"pod_name": "my-pod"})
    cmd = mock_kctl.call_args[0][0]
    assert "delete" in cmd
    assert "my-pod" in cmd
    assert "--force" not in cmd
    assert "--grace-period=0" not in cmd


async def test_delete_pod_force():
    with patch("k8s_mcp.tools.remediation.kubectl", return_value="pod deleted") as mock_kctl:
        await handle_delete_pod({"pod_name": "stuck-pod", "force": True})
    cmd = mock_kctl.call_args[0][0]
    assert "--grace-period=0" in cmd
    assert "--force" in cmd


async def test_delete_pod_error():
    with patch("k8s_mcp.tools.remediation.kubectl", side_effect=KubectlError("not found")):
        result = await handle_delete_pod({"pod_name": "ghost"})
    assert "Error" in result[0].text


# ---------------------------------------------------------------------------
# handle_rollback_deployment
# ---------------------------------------------------------------------------

async def test_rollback_no_revision():
    with patch("k8s_mcp.tools.remediation.kubectl", return_value="rolled back") as mock_kctl:
        await handle_rollback_deployment({"deployment_name": "my-app"})
    cmd = mock_kctl.call_args[0][0]
    assert "undo" in cmd
    assert "deployment/my-app" in cmd
    assert not any("to-revision" in arg for arg in cmd)


async def test_rollback_with_revision():
    with patch("k8s_mcp.tools.remediation.kubectl", return_value="rolled back") as mock_kctl:
        await handle_rollback_deployment({"deployment_name": "my-app", "revision": 3})
    cmd = mock_kctl.call_args[0][0]
    assert "--to-revision=3" in cmd


# ---------------------------------------------------------------------------
# handle_apply_manifest
# ---------------------------------------------------------------------------

async def test_apply_manifest_uses_stdin():
    manifest = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: test"
    with patch("k8s_mcp.tools.remediation.kubectl_stdin", return_value="configured") as mock_stdin:
        result = await handle_apply_manifest({"manifest": manifest})

    assert mock_stdin.call_args.kwargs["stdin_data"] == manifest
    cmd = mock_stdin.call_args[0][0]
    assert "apply" in cmd
    assert "-f" in cmd
    assert "-" in cmd
    assert "configured" in result[0].text


async def test_apply_manifest_dry_run():
    with patch("k8s_mcp.tools.remediation.kubectl_stdin", return_value="dry run ok") as mock_stdin:
        await handle_apply_manifest({"manifest": "---", "dry_run": True})
    cmd = mock_stdin.call_args[0][0]
    assert "--dry-run=server" in cmd


async def test_apply_manifest_no_dry_run_by_default():
    with patch("k8s_mcp.tools.remediation.kubectl_stdin", return_value="ok") as mock_stdin:
        await handle_apply_manifest({"manifest": "---"})
    cmd = mock_stdin.call_args[0][0]
    assert "--dry-run=server" not in cmd


# ---------------------------------------------------------------------------
# handle_patch_resource
# ---------------------------------------------------------------------------

async def test_patch_resource_default_type():
    with patch("k8s_mcp.tools.remediation.kubectl", return_value="patched") as mock_kctl:
        await handle_patch_resource({
            "resource_type": "deployment",
            "resource_name": "my-app",
            "patch": '{"spec":{"replicas":2}}',
        })
    cmd = mock_kctl.call_args[0][0]
    assert "--type=merge" in cmd


async def test_patch_resource_strategic_type():
    with patch("k8s_mcp.tools.remediation.kubectl", return_value="patched") as mock_kctl:
        await handle_patch_resource({
            "resource_type": "deployment",
            "resource_name": "my-app",
            "patch": '{"spec":{}}',
            "patch_type": "strategic",
        })
    cmd = mock_kctl.call_args[0][0]
    assert "--type=strategic" in cmd


async def test_patch_resource_patch_value_in_cmd():
    patch_str = '{"metadata":{"labels":{"env":"prod"}}}'
    with patch("k8s_mcp.tools.remediation.kubectl", return_value="patched") as mock_kctl:
        await handle_patch_resource({
            "resource_type": "configmap",
            "resource_name": "my-cm",
            "patch": patch_str,
        })
    cmd = mock_kctl.call_args[0][0]
    assert patch_str in cmd


# ---------------------------------------------------------------------------
# handle_node_operation
# ---------------------------------------------------------------------------

async def test_node_cordon():
    with patch("k8s_mcp.tools.remediation.kubectl", return_value="cordoned") as mock_kctl:
        result = await handle_node_operation({"operation": "cordon", "node_name": "node1"})
    cmd = mock_kctl.call_args[0][0]
    assert cmd == ["cordon", "node1"]
    assert "cordoned" in result[0].text


async def test_node_uncordon():
    with patch("k8s_mcp.tools.remediation.kubectl", return_value="uncordoned") as mock_kctl:
        await handle_node_operation({"operation": "uncordon", "node_name": "node1"})
    cmd = mock_kctl.call_args[0][0]
    assert cmd == ["uncordon", "node1"]


async def test_node_drain_with_flags():
    with patch("k8s_mcp.tools.remediation.kubectl", return_value="drained") as mock_kctl:
        await handle_node_operation({
            "operation": "drain",
            "node_name": "node1",
            "ignore_daemonsets": True,
            "delete_emptydir_data": True,
        })
    cmd = mock_kctl.call_args[0][0]
    assert "drain" in cmd
    assert "--ignore-daemonsets" in cmd
    assert "--delete-emptydir-data" in cmd


async def test_node_drain_without_flags():
    with patch("k8s_mcp.tools.remediation.kubectl", return_value="drained") as mock_kctl:
        await handle_node_operation({"operation": "drain", "node_name": "node1"})
    cmd = mock_kctl.call_args[0][0]
    assert "--ignore-daemonsets" not in cmd
    assert "--delete-emptydir-data" not in cmd


async def test_node_operation_invalid():
    with patch("k8s_mcp.tools.remediation.kubectl") as mock_kctl:
        result = await handle_node_operation({"operation": "nuke", "node_name": "node1"})
    mock_kctl.assert_not_called()
    assert "Error" in result[0].text
    assert "Unknown operation" in result[0].text
