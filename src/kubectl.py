"""
Async kubectl wrapper.

Uses asyncio.create_subprocess_exec — no shell involved, immune to injection.
All callers must pass resource names/values as explicit list elements, never
interpolated into a shell string.
"""

from __future__ import annotations

import asyncio
import json
from typing import Sequence


KUBECTL_TIMEOUT = 60  # seconds
MAX_OUTPUT_BYTES = 10 * 1024 * 1024  # 10 MB


class KubectlError(Exception):
    """Raised when kubectl exits with a non-zero status."""


def _build_args(
    args: Sequence[str],
    context: str | None = None,
    namespace: str | None = None,
    all_namespaces: bool = False,
) -> list[str]:
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
) -> str:
    """Run kubectl and return stdout as a string."""
    full_args = _build_args(args, context=context, namespace=namespace, all_namespaces=all_namespaces)

    proc = await asyncio.create_subprocess_exec(
        "kubectl",
        *full_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=KUBECTL_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        raise KubectlError(f"kubectl timed out after {KUBECTL_TIMEOUT}s: kubectl {' '.join(full_args)}")

    if len(stdout) > MAX_OUTPUT_BYTES:
        stdout = stdout[:MAX_OUTPUT_BYTES] + b"\n[... output truncated at 10 MB ...]"

    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip()
        raise KubectlError(err or f"kubectl exited with code {proc.returncode}")

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
    return json.loads(output)


async def kubectl_stdin(
    args: Sequence[str],
    stdin_data: str,
    *,
    context: str | None = None,
    namespace: str | None = None,
) -> str:
    """Run kubectl with data piped to stdin (e.g. apply -f -)."""
    full_args = _build_args(args, context=context, namespace=namespace)

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
        raise KubectlError(err or f"kubectl exited with code {proc.returncode}")

    out = stdout.decode(errors="replace").strip()
    err_out = stderr.decode(errors="replace").strip()
    # kubectl apply prints useful info to stderr on success too
    if err_out:
        return f"{out}\n{err_out}".strip()
    return out
