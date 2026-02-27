"""
Unit tests for src/kubectl.py
"""

from __future__ import annotations

import asyncio
import json

import pytest

from k8s_mcp.kubectl import KubectlError, _build_args, kubectl, kubectl_json, kubectl_stdin


# ---------------------------------------------------------------------------
# _build_args — pure function
# ---------------------------------------------------------------------------

def test_build_args_empty():
    assert _build_args(["get", "pods"]) == ["get", "pods"]


def test_build_args_context():
    result = _build_args(["get", "pods"], context="minikube")
    assert result == ["--context", "minikube", "get", "pods"]


def test_build_args_namespace():
    result = _build_args(["get", "pods"], namespace="default")
    assert result == ["--namespace", "default", "get", "pods"]


def test_build_args_all_namespaces_is_suffix():
    result = _build_args(["get", "pods"], all_namespaces=True)
    # --all-namespaces must come AFTER the subcommand
    assert result == ["get", "pods", "--all-namespaces"]
    assert result[0] != "--all-namespaces"


def test_build_args_context_and_all_namespaces():
    result = _build_args(["get", "pods"], context="minikube", all_namespaces=True)
    assert result == ["--context", "minikube", "get", "pods", "--all-namespaces"]


def test_build_args_all_namespaces_wins_over_namespace():
    result = _build_args(["get", "pods"], namespace="default", all_namespaces=True)
    assert "--namespace" not in result
    assert "--all-namespaces" in result


# ---------------------------------------------------------------------------
# kubectl()
# ---------------------------------------------------------------------------

async def test_kubectl_success(mock_run):
    mock_run((b"  hello world  \n", b"", 0))
    out = await kubectl(["get", "pods"])
    assert out == "hello world"


async def test_kubectl_nonzero_exit_uses_stderr(mock_run):
    mock_run((b"", b"Error from server: not found", 1))
    with pytest.raises(KubectlError, match="Error from server"):
        await kubectl(["get", "pods"])


async def test_kubectl_nonzero_exit_no_stderr(mock_run):
    mock_run((b"", b"", 2))
    with pytest.raises(KubectlError, match="exited with code 2"):
        await kubectl(["get", "pods"])


async def test_kubectl_timeout(mock_run, monkeypatch):
    async def slow_communicate():
        raise asyncio.TimeoutError

    import asyncio as _asyncio
    orig = _asyncio.wait_for

    async def fake_wait_for(coro, timeout):
        raise asyncio.TimeoutError

    monkeypatch.setattr("asyncio.wait_for", fake_wait_for)

    # Need a real proc object for kill()
    from tests.conftest import make_proc
    proc = make_proc()
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        lambda *a, **kw: _async_return(proc),
    )

    with pytest.raises(KubectlError, match="timed out"):
        await kubectl(["get", "pods"])

    proc.kill.assert_called_once()


async def _async_return(val):
    return val


async def test_kubectl_output_truncated(mock_run):
    big = b"x" * (10 * 1024 * 1024 + 1)
    mock_run((big, b"", 0))
    out = await kubectl(["get", "pods"])
    assert "truncated" in out


async def test_kubectl_utf8_replacement(mock_run):
    mock_run((b"valid \xff invalid", b"", 0))
    out = await kubectl(["get", "pods"])
    assert "\xff" not in out
    assert "valid" in out


# ---------------------------------------------------------------------------
# kubectl_json()
# ---------------------------------------------------------------------------

async def test_kubectl_json_parses_dict(mock_run):
    payload = {"items": [{"name": "foo"}]}
    mock_run((json.dumps(payload).encode(), b"", 0))
    result = await kubectl_json(["get", "pods"])
    assert result == payload


async def test_kubectl_json_appends_flag(mock_run):
    captured = []
    orig_exec = asyncio.create_subprocess_exec

    async def capture_exec(*args, **kwargs):
        captured.extend(args)
        from tests.conftest import make_proc
        return make_proc(b"{}", b"", 0)

    import asyncio as _asyncio
    _asyncio.create_subprocess_exec = capture_exec
    try:
        await kubectl_json(["get", "pods"])
    finally:
        _asyncio.create_subprocess_exec = orig_exec

    assert "-o" in captured
    assert "json" in captured


# ---------------------------------------------------------------------------
# kubectl_stdin()
# ---------------------------------------------------------------------------

async def test_kubectl_stdin_pipes_data(monkeypatch):
    received_input = []

    from tests.conftest import make_proc
    proc = make_proc(b"applied", b"", 0)

    # Override communicate to capture what was sent
    async def fake_communicate(input=None):
        received_input.append(input)
        return b"applied", b""

    proc.communicate = fake_communicate

    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        lambda *a, **kw: _async_return(proc),
    )

    out = await kubectl_stdin(["apply", "-f", "-"], stdin_data="apiVersion: v1\n")
    assert received_input[0] == b"apiVersion: v1\n"
    assert out == "applied"


async def test_kubectl_stdin_includes_stderr_on_success(monkeypatch):
    from tests.conftest import make_proc
    proc = make_proc(b"deployment.apps/foo configured", b"Warning: resource already exists", 0)

    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        lambda *a, **kw: _async_return(proc),
    )

    out = await kubectl_stdin(["apply", "-f", "-"], stdin_data="---")
    assert "configured" in out
    assert "Warning" in out
