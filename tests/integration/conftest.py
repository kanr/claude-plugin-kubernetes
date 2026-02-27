"""
Integration test fixtures — requires a live minikube cluster.
"""

from __future__ import annotations

import subprocess

import pytest


def _cluster_reachable() -> bool:
    try:
        result = subprocess.run(
            ["kubectl", "cluster-info", "--context=minikube"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


skip_no_cluster = pytest.mark.skipif(
    not _cluster_reachable(),
    reason="minikube cluster not reachable — skipping integration tests",
)
