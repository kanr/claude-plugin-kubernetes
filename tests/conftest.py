"""
Shared fixtures for the test suite.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Subprocess mock factory
# ---------------------------------------------------------------------------

def make_proc(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
    """Mimics the object returned by asyncio.create_subprocess_exec."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


@pytest.fixture
def mock_run(monkeypatch):
    """
    Patches asyncio.create_subprocess_exec with a fake that pops responses
    from a queue.

    Usage:
        mock_run((b"output", b"", 0))
        mock_run((b"out1", b"", 0), (b"out2", b"", 0))  # multiple calls
    """
    responses: list[tuple[bytes, bytes, int]] = []

    async def fake_exec(*args, **kwargs):
        assert responses, f"Unexpected kubectl call: {args}"
        stdout, stderr, rc = responses.pop(0)
        return make_proc(stdout, stderr, rc)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    def queue(*items: tuple[bytes, bytes, int]):
        responses.extend(items)

    return queue


# ---------------------------------------------------------------------------
# Sample kubectl JSON responses
# ---------------------------------------------------------------------------

PODS_JSON = {
    "apiVersion": "v1",
    "kind": "List",
    "items": [
        {
            "metadata": {"name": "app-abc", "namespace": "default"},
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {"name": "app", "restartCount": 0, "state": {}},
                ],
            },
        },
        {
            "metadata": {"name": "app-pending", "namespace": "default"},
            "status": {
                "phase": "Pending",
                "reason": "Unschedulable",
                "containerStatuses": [],
            },
        },
        {
            "metadata": {"name": "crasher", "namespace": "kube-system"},
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {
                        "name": "crasher",
                        "restartCount": 10,
                        "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                    }
                ],
            },
        },
    ],
}

NODES_JSON = {
    "apiVersion": "v1",
    "kind": "List",
    "items": [
        {
            "metadata": {"name": "node-ready"},
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "True", "reason": "KubeletReady", "message": ""},
                ]
            },
        },
        {
            "metadata": {"name": "node-notready"},
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "False", "reason": "KubeletNotReady", "message": "PLEG is not healthy"},
                    {"type": "MemoryPressure", "status": "False", "reason": "KubeletHasSufficientMemory", "message": ""},
                ]
            },
        },
    ],
}

DEPLOYMENTS_JSON = {
    "apiVersion": "v1",
    "kind": "List",
    "items": [
        {
            "metadata": {"name": "healthy-app", "namespace": "default"},
            "spec": {"replicas": 3},
            "status": {"readyReplicas": 3, "availableReplicas": 3},
        },
        {
            "metadata": {"name": "broken-app", "namespace": "default"},
            "spec": {"replicas": 2},
            "status": {"readyReplicas": 1, "availableReplicas": 1, "unavailableReplicas": 1},
        },
    ],
}

EVENTS_TEXT = "\n".join(
    [f"default   {i}m   Warning   BackOff   pod/foo   Back-off restarting" for i in range(25)]
)
