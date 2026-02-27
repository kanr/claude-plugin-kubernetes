"""
Async kubectl wrapper.

Uses asyncio.create_subprocess_exec — no shell involved, immune to injection.
All callers must pass resource names/values as explicit list elements, never
interpolated into a shell string.

Safety features:
  - Context allowlist via K8S_MCP_ALLOWED_CONTEXTS env var
  - Namespace blocklist via K8S_MCP_NAMESPACE_BLOCKLIST env var (write ops)
  - Concurrency semaphore to limit parallel subprocess count
  - Enriched error messages for common failure modes
  - Timeout override support for long-running operations (drain)
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Sequence


KUBECTL_TIMEOUT = 60  # seconds
MAX_OUTPUT_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_CONCURRENT_KUBECTL = 10

# ---------------------------------------------------------------------------
# Safety: context allowlist & namespace blocklist
# ---------------------------------------------------------------------------

_ALLOWED_CONTEXTS: list[str] = [
    c.strip()
    for c in os.environ.get("K8S_MCP_ALLOWED_CONTEXTS", "").split(",")
    if c.strip()
]

_DEFAULT_NS_BLOCKLIST = "kube-system,kube-public,kube-node-lease"
_NAMESPACE_BLOCKLIST: set[str] = {
    ns.strip()
    for ns in os.environ.get("K8S_MCP_NAMESPACE_BLOCKLIST", _DEFAULT_NS_BLOCKLIST).split(",")
    if ns.strip()
}

_NAMESPACE_ALLOWLIST: list[str] = [
    ns.strip()
    for ns in os.environ.get("K8S_MCP_NAMESPACE_ALLOWLIST", "").split(",")
    if ns.strip()
]


def check_context_allowed(context: str | None) -> None:
    """Raise if context is not in the allowlist (when configured)."""
    if _ALLOWED_CONTEXTS and context and context not in _ALLOWED_CONTEXTS:
        raise KubectlError(
            f"Context '{context}' is not in the allowed list: {_ALLOWED_CONTEXTS}. "
            f"Set K8S_MCP_ALLOWED_CONTEXTS to adjust."
        )


def check_namespace_writable(namespace: str | None) -> None:
    """Raise if namespace is blocked for write operations."""
    if not namespace:
        return
    if _NAMESPACE_ALLOWLIST and namespace not in _NAMESPACE_ALLOWLIST:
        raise KubectlError(
            f"Namespace '{namespace}' is not in the write allowlist: {_NAMESPACE_ALLOWLIST}. "
            f"Set K8S_MCP_NAMESPACE_ALLOWLIST to adjust."
        )
    if namespace in _NAMESPACE_BLOCKLIST:
        raise KubectlError(
            f"Namespace '{namespace}' is protected from write operations. "
            f"Set K8S_MCP_NAMESPACE_BLOCKLIST to adjust (current: {_NAMESPACE_BLOCKLIST})."
        )


# ---------------------------------------------------------------------------
# Error enrichment
# ---------------------------------------------------------------------------

_ERROR_HINTS = {
    "No such file or directory": (
        "kubectl binary not found. Ensure kubectl is installed and on your PATH."
    ),
    "Unable to connect to the server": (
        "Cannot reach the Kubernetes API server. Check that your cluster is running "
        "and kubeconfig is correct."
    ),
    "error: You must be logged in": (
        "Authentication failed. Your kubeconfig credentials may have expired."
    ),
    "the server has asked for the client to provide credentials": (
        "Cluster rejected credentials. Token may be expired."
    ),
    "exec plugin: invalid apiVersion": (
        "Exec-based auth plugin version mismatch. Check your kubeconfig's exec provider."
    ),
    "was refused": (
        "Connection refused by the API server. The cluster may be down or the endpoint is wrong."
    ),
}


def _enrich_error(raw_stderr: str) -> str:
    """Prepend an actionable hint to common kubectl errors."""
    for pattern, hint in _ERROR_HINTS.items():
        if pattern in raw_stderr:
            return f"{hint}\n\nkubectl stderr: {raw_stderr}"
    return raw_stderr


# ---------------------------------------------------------------------------
# Concurrency control
# ---------------------------------------------------------------------------

_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(MAX_CONCURRENT_KUBECTL)
    return _semaphore


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class KubectlError(Exception):
    """Raised when kubectl exits with a non-zero status."""


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def _build_args(
    args: Sequence[str],
    context: str | None = None,
    namespace: str | None = None,
    all_namespaces: bool = False,
) -> list[str]:
    check_context_allowed(context)
    prefix: list[str] = []
    suffix: list[str] = []
    if context:
        prefix += ["--context", context]
    if all_namespaces:
        suffix += ["--all-namespaces"]
    elif namespace:
        prefix += ["--namespace", namespace]
    return prefix + list(args) + suffix


async def kubectl(
    args: Sequence[str],
    *,
    context: str | None = None,
    namespace: str | None = None,
    all_namespaces: bool = False,
    timeout_override: int | None = None,
) -> str:
    """Run kubectl and return stdout as a string."""
    full_args = _build_args(args, context=context, namespace=namespace, all_namespaces=all_namespaces)
    timeout = timeout_override or KUBECTL_TIMEOUT

    async with _get_semaphore():
        proc = await asyncio.create_subprocess_exec(
            "kubectl",
            *full_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise KubectlError(f"kubectl timed out after {timeout}s: kubectl {' '.join(full_args)}")

    if len(stdout) > MAX_OUTPUT_BYTES:
        stdout = stdout[:MAX_OUTPUT_BYTES] + b"\n[... output truncated at 10 MB ...]"

    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip()
        raise KubectlError(_enrich_error(err) if err else f"kubectl exited with code {proc.returncode}")

    return stdout.decode(errors="replace").strip()


async def kubectl_json(
    args: Sequence[str],
    *,
    context: str | None = None,
    namespace: str | None = None,
    all_namespaces: bool = False,
) -> dict | list:
    """Run kubectl with -o json and parse the result."""
    output = await kubectl(
        list(args) + ["-o", "json"],
        context=context,
        namespace=namespace,
        all_namespaces=all_namespaces,
    )
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        raise KubectlError(
            "Response too large to parse as JSON (likely truncated at 10 MB). "
            "Try narrowing your query with a namespace or label selector."
        )


async def kubectl_stdin(
    args: Sequence[str],
    stdin_data: str,
    *,
    context: str | None = None,
    namespace: str | None = None,
) -> str:
    """Run kubectl with data piped to stdin (e.g. apply -f -)."""
    full_args = _build_args(args, context=context, namespace=namespace)

    async with _get_semaphore():
        proc = await asyncio.create_subprocess_exec(
            "kubectl",
            *full_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_data.encode()),
                timeout=KUBECTL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise KubectlError(f"kubectl timed out after {KUBECTL_TIMEOUT}s")

    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip()
        raise KubectlError(_enrich_error(err) if err else f"kubectl exited with code {proc.returncode}")

    out = stdout.decode(errors="replace").strip()
    err_out = stderr.decode(errors="replace").strip()
    # kubectl apply prints useful info to stderr on success too
    if err_out:
        return f"{out}\n{err_out}".strip()
    return out
