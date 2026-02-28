# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup & Running

```bash
# Create venv and install in editable mode
python3 -m venv .venv
.venv/bin/pip install -e .

# Run the MCP server directly (for debugging)
.venv/bin/python -m k8s_mcp.server
```

The server is registered via `.mcp.json` in the project root and is picked up automatically by Claude Code. After code changes, restart the MCP server with `/mcp` in Claude Code to reload.

## Architecture

The server exposes 48 `kubectl`-backed tools over MCP stdio transport. Every tool follows the same pattern: a `Tool` definition (name, description, inputSchema) lives alongside its async handler function in the same file.

**Data flow:** `server.py` → dispatches to handler in `tools/` → calls `kubectl()` or `kubectl_json()` in `kubectl.py` → returns `list[TextContent]`

### `k8s_mcp/kubectl.py` — the only place kubectl is invoked

- `kubectl(args, *, context, namespace, all_namespaces)` — runs kubectl, returns stdout string
- `kubectl_json(args, ...)` — appends `-o json` and parses result
- `kubectl_stdin(args, stdin_data, ...)` — pipes data to stdin (used by `apply -f -`)
- `kubectl_diff(stdin_data, *, context, namespace)` — runs `diff -f -`, returns `(returncode, stdout, stderr)`; exit code 0 = no diff, 1 = has diff, >1 = error
- `_build_args()` — assembles the full arg list: `[--context X] [--namespace Y] + args + [--all-namespaces]`
  - **Important:** `--all-namespaces` goes in a *suffix*, not prefix — it must come after the subcommand

### `k8s_mcp/tools/`

Each module exports two dicts consumed by `server.py`:
- `{CATEGORY}_TOOLS: list[Tool]` — MCP tool definitions with inputSchema
- `{CATEGORY}_HANDLERS: dict[str, Callable]` — name → async handler

| Module | Tools | Notes |
|---|---|---|
| `awareness.py` | 28 read-only | `handle_cluster_info` runs 3 kubectl calls in parallel, degrades gracefully per-call; includes RBAC, storage, quota, and PDB listing tools |
| `diagnostics.py` | 11 read-only | `handle_find_issues` runs 8 health checks in parallel; `_check_*` helpers return `list[str]` issue lines; includes exec, log-by-selector, and rollout tools |
| `remediation.py` | 9 write | Risk levels in tool descriptions: LOW / MEDIUM / HIGH |

### `k8s_mcp/formatters.py`

Shared helpers: `severity_icon()`, `node_conditions_summary()`, `kv_table()`, `bullet_list()`. Used primarily in `diagnostics.py`.

## Adding a New Tool

1. Add a `Tool(...)` entry to the relevant `*_TOOLS` list
2. Add an `async def handle_*(args: dict) -> list[TextContent]` handler
3. Register it in the `*_HANDLERS` dict in the same file
4. No changes needed in `server.py` — it merges all three handler dicts automatically

## Known Quirks

- `kubectl version --short` is used in `handle_cluster_info`; `--short` is deprecated in newer kubectl but still works
- `kubectl_stdin` captures stderr on success too (kubectl apply writes resource status to stderr)
- `k8s_list_events` defaults `all_namespaces` to `True` when no explicit namespace is given; scoped when namespace is provided
- `pyproject.toml` uses `build-backend = "setuptools.build_meta"` — the newer `.backends.legacy:build` variant fails on Python 3.11's bundled setuptools

## Coauthoring

add the follow to your commit message to have it attributed to Claude:
`Co-Authored-By: <noreply@anthropic.com>`
