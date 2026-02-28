"""
Unit tests for new UX improvements:
  - ToolError / isError propagation
  - k8s_get handler
  - k8s_get_configmap_data handler
  - Rollout status/history with resource_type parameter
  - Dry-run for patch/delete
  - Summary headers in _simple_list
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from mcp.types import TextContent

from k8s_mcp.formatters import ToolError, _err
from k8s_mcp.kubectl import KubectlError
from k8s_mcp.tools.awareness import (
    handle_get,
    handle_get_configmap_data,
    handle_list_statefulsets,
)
from k8s_mcp.tools.diagnostics import (
    handle_rollout_history,
    handle_rollout_status,
)
from k8s_mcp.tools.remediation import (
    handle_delete_resource,
    handle_patch_resource,
)


# ---------------------------------------------------------------------------
# ToolError / isError propagation
# ---------------------------------------------------------------------------


class TestToolError:
    def test_err_returns_tool_error_instance(self):
        result = _err("something broke")
        assert isinstance(result, ToolError)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].text == "Error: something broke"

    def test_tool_error_is_list_subclass(self):
        result = _err("test")
        assert isinstance(result, list)
        assert isinstance(result, ToolError)

    def test_normal_list_is_not_tool_error(self):
        result = [TextContent(type="text", text="ok")]
        assert not isinstance(result, ToolError)

    async def test_handler_error_is_tool_error(self):
        with patch("k8s_mcp.tools.awareness.kubectl", side_effect=KubectlError("fail")):
            result = await handle_list_statefulsets({})
        assert isinstance(result, ToolError)

    async def test_handler_success_is_not_tool_error(self):
        with patch("k8s_mcp.tools.awareness.kubectl", return_value="NAME   READY\nfoo    1/1"):
            result = await handle_list_statefulsets({})
        assert not isinstance(result, ToolError)


# ---------------------------------------------------------------------------
# k8s_get handler
# ---------------------------------------------------------------------------


class TestHandleGet:
    async def test_get_list_wide(self):
        output = "NAME      READY   STATUS\nfoo-rs    3/3     Running"
        with patch("k8s_mcp.tools.awareness.kubectl", return_value=output) as mock:
            result = await handle_get({"resource_type": "replicasets"})
        cmd = mock.call_args[0][0]
        assert cmd[:2] == ["get", "replicasets"]
        assert "-o" in cmd
        assert "wide" in cmd
        assert "1 replicasets" in result[0].text
        assert "foo-rs" in result[0].text

    async def test_get_single_resource_yaml(self):
        with patch("k8s_mcp.tools.awareness.kubectl", return_value="apiVersion: v1\nkind: Service"):
            result = await handle_get({
                "resource_type": "service",
                "name": "my-svc",
                "output": "yaml",
            })
        assert "apiVersion" in result[0].text

    async def test_get_with_label_selector(self):
        with patch("k8s_mcp.tools.awareness.kubectl", return_value="NAME\nfoo") as mock:
            await handle_get({
                "resource_type": "pods",
                "label_selector": "app=web",
            })
        cmd = mock.call_args[0][0]
        assert "-l" in cmd
        assert "app=web" in cmd

    async def test_get_with_field_selector(self):
        with patch("k8s_mcp.tools.awareness.kubectl", return_value="NAME\nfoo") as mock:
            await handle_get({
                "resource_type": "pods",
                "field_selector": "status.phase=Running",
            })
        cmd = mock.call_args[0][0]
        assert "--field-selector" in cmd
        assert "status.phase=Running" in cmd

    async def test_get_passes_namespace(self):
        with patch("k8s_mcp.tools.awareness.kubectl", return_value="NAME\nfoo") as mock:
            await handle_get({
                "resource_type": "endpoints",
                "namespace": "prod",
                "all_namespaces": False,
            })
        assert mock.call_args.kwargs["namespace"] == "prod"
        assert mock.call_args.kwargs["all_namespaces"] is False

    async def test_get_error(self):
        with patch("k8s_mcp.tools.awareness.kubectl", side_effect=KubectlError("not found")):
            result = await handle_get({"resource_type": "foobar"})
        assert isinstance(result, ToolError)
        assert "Error" in result[0].text

    async def test_get_json_output(self):
        with patch("k8s_mcp.tools.awareness.kubectl", return_value='{"items": []}') as mock:
            result = await handle_get({
                "resource_type": "pods",
                "output": "json",
            })
        cmd = mock.call_args[0][0]
        assert "-o" in cmd
        assert "json" in cmd

    async def test_get_name_output(self):
        with patch("k8s_mcp.tools.awareness.kubectl", return_value="pod/foo\npod/bar") as mock:
            result = await handle_get({
                "resource_type": "pods",
                "output": "name",
            })
        cmd = mock.call_args[0][0]
        assert "name" in cmd


# ---------------------------------------------------------------------------
# k8s_get_configmap_data handler
# ---------------------------------------------------------------------------


class TestHandleGetConfigmapData:
    async def test_get_all_keys(self):
        cm_json = {
            "data": {"app.conf": "key=value\nfoo=bar", "settings.yaml": "debug: true"},
            "binaryData": {},
        }
        with patch("k8s_mcp.tools.awareness.kubectl_json", return_value=cm_json):
            result = await handle_get_configmap_data({"configmap_name": "my-config"})
        text = result[0].text
        assert "ConfigMap: my-config" in text
        assert "Keys: 2" in text
        assert "app.conf" in text
        assert "key=value" in text
        assert "settings.yaml" in text

    async def test_get_single_key(self):
        cm_json = {
            "data": {"db_host": "postgres.svc", "db_port": "5432"},
        }
        with patch("k8s_mcp.tools.awareness.kubectl_json", return_value=cm_json):
            result = await handle_get_configmap_data({
                "configmap_name": "db-config",
                "key": "db_host",
            })
        assert "postgres.svc" in result[0].text

    async def test_get_missing_key(self):
        cm_json = {"data": {"existing_key": "val"}}
        with patch("k8s_mcp.tools.awareness.kubectl_json", return_value=cm_json):
            result = await handle_get_configmap_data({
                "configmap_name": "cfg",
                "key": "nonexistent",
            })
        assert isinstance(result, ToolError)
        assert "nonexistent" in result[0].text
        assert "existing_key" in result[0].text

    async def test_get_error(self):
        with patch("k8s_mcp.tools.awareness.kubectl_json", side_effect=KubectlError("not found")):
            result = await handle_get_configmap_data({"configmap_name": "gone"})
        assert isinstance(result, ToolError)

    async def test_get_empty_configmap(self):
        cm_json = {"data": None, "binaryData": None}
        with patch("k8s_mcp.tools.awareness.kubectl_json", return_value=cm_json):
            result = await handle_get_configmap_data({"configmap_name": "empty"})
        assert "Keys: 0" in result[0].text

    async def test_truncates_long_values(self):
        cm_json = {"data": {"big": "x" * 5000}}
        with patch("k8s_mcp.tools.awareness.kubectl_json", return_value=cm_json):
            result = await handle_get_configmap_data({"configmap_name": "big-config"})
        assert "truncated" in result[0].text


# ---------------------------------------------------------------------------
# Rollout status/history with resource_type
# ---------------------------------------------------------------------------


class TestRolloutResourceType:
    async def test_rollout_status_deployment_default(self):
        with patch("k8s_mcp.tools.diagnostics.kubectl", return_value="rolled out") as mock:
            result = await handle_rollout_status({"name": "my-app"})
        cmd = mock.call_args[0][0]
        assert "deployment/my-app" in cmd

    async def test_rollout_status_statefulset(self):
        with patch("k8s_mcp.tools.diagnostics.kubectl", return_value="rolled out") as mock:
            result = await handle_rollout_status({
                "name": "my-sts",
                "resource_type": "statefulset",
            })
        cmd = mock.call_args[0][0]
        assert "statefulset/my-sts" in cmd

    async def test_rollout_status_daemonset(self):
        with patch("k8s_mcp.tools.diagnostics.kubectl", return_value="rolled out") as mock:
            result = await handle_rollout_status({
                "name": "my-ds",
                "resource_type": "daemonset",
            })
        cmd = mock.call_args[0][0]
        assert "daemonset/my-ds" in cmd

    async def test_rollout_status_backward_compat(self):
        """Old-style deployment_name param still works."""
        with patch("k8s_mcp.tools.diagnostics.kubectl", return_value="ok") as mock:
            result = await handle_rollout_status({"deployment_name": "legacy-app"})
        cmd = mock.call_args[0][0]
        assert "deployment/legacy-app" in cmd

    async def test_rollout_status_error_suggests_rollback(self):
        with patch("k8s_mcp.tools.diagnostics.kubectl", side_effect=KubectlError("timed out")):
            result = await handle_rollout_status({
                "name": "stuck-app",
                "namespace": "prod",
            })
        assert isinstance(result, ToolError)
        assert "k8s_rollout_history" in result[0].text
        assert "k8s_rollback_deployment" in result[0].text

    async def test_rollout_history_statefulset(self):
        with patch("k8s_mcp.tools.diagnostics.kubectl", return_value="revision 1\nrevision 2") as mock:
            result = await handle_rollout_history({
                "name": "my-sts",
                "resource_type": "statefulset",
            })
        cmd = mock.call_args[0][0]
        assert "statefulset/my-sts" in cmd
        assert "revision 1" in result[0].text

    async def test_rollout_history_backward_compat(self):
        with patch("k8s_mcp.tools.diagnostics.kubectl", return_value="history") as mock:
            result = await handle_rollout_history({"deployment_name": "old-app"})
        cmd = mock.call_args[0][0]
        assert "deployment/old-app" in cmd


# ---------------------------------------------------------------------------
# Dry-run for patch and delete
# ---------------------------------------------------------------------------


class TestDryRun:
    async def test_patch_dry_run(self):
        with patch("k8s_mcp.tools.remediation.kubectl", return_value="patched (dry run)") as mock:
            result = await handle_patch_resource({
                "resource_type": "deployment",
                "resource_name": "my-app",
                "patch": '{"spec": {"replicas": 5}}',
                "dry_run": True,
            })
        cmd = mock.call_args[0][0]
        assert "--dry-run=server" in cmd
        assert "patched" in result[0].text

    async def test_patch_no_dry_run_default(self):
        with patch("k8s_mcp.tools.remediation.kubectl", return_value="patched") as mock:
            await handle_patch_resource({
                "resource_type": "deployment",
                "resource_name": "my-app",
                "patch": '{"spec": {"replicas": 5}}',
            })
        cmd = mock.call_args[0][0]
        assert "--dry-run=server" not in cmd

    async def test_delete_dry_run(self):
        with patch("k8s_mcp.tools.remediation.kubectl", return_value='pod "foo" deleted (dry run)') as mock:
            result = await handle_delete_resource({
                "resource_type": "pod",
                "resource_name": "foo",
                "dry_run": True,
            })
        cmd = mock.call_args[0][0]
        assert "--dry-run=server" in cmd

    async def test_delete_no_dry_run_default(self):
        with patch("k8s_mcp.tools.remediation.kubectl", return_value='pod "foo" deleted') as mock:
            await handle_delete_resource({
                "resource_type": "pod",
                "resource_name": "foo",
            })
        cmd = mock.call_args[0][0]
        assert "--dry-run=server" not in cmd


# ---------------------------------------------------------------------------
# Summary headers in _simple_list
# ---------------------------------------------------------------------------


class TestSimpleListSummary:
    async def test_summary_prepended_with_count(self):
        output = "NAME     READY   STATUS\nfoo-0    1/1     Running\nfoo-1    1/1     Running"
        with patch("k8s_mcp.tools.awareness.kubectl", return_value=output):
            result = await handle_list_statefulsets({})
        text = result[0].text
        # Should start with summary
        assert text.startswith("2 statefulsets")
        # Should still contain the original output
        assert "foo-0" in text
        assert "foo-1" in text

    async def test_summary_includes_namespace_scope(self):
        output = "NAME     READY\nbar-0    1/1"
        with patch("k8s_mcp.tools.awareness.kubectl", return_value=output):
            result = await handle_list_statefulsets({"namespace": "production"})
        assert "production" in result[0].text

    async def test_summary_all_namespaces_scope(self):
        output = "NAME     READY\nbaz-0    1/1"
        with patch("k8s_mcp.tools.awareness.kubectl", return_value=output):
            result = await handle_list_statefulsets({"all_namespaces": True})
        assert "all namespaces" in result[0].text

    async def test_no_summary_on_empty_output(self):
        """When kubectl returns no rows, don't prepend a summary."""
        output = "No resources found in default namespace."
        with patch("k8s_mcp.tools.awareness.kubectl", return_value=output):
            result = await handle_list_statefulsets({})
        # Should return the raw message without modification
        assert result[0].text == output
