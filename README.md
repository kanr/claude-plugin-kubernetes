# Claude Plugin — Kubernetes

An MCP server that gives Claude deep Kubernetes cluster awareness, diagnostics, and remediation capabilities via `kubectl`.

## Tools (21)

### Awareness
| Tool | Description |
|---|---|
| `k8s_cluster_info` | Current context, server version, API endpoint |
| `k8s_get_contexts` | List all kubeconfig contexts |
| `k8s_list_namespaces` | List namespaces with status |
| `k8s_list_nodes` | Nodes with roles, status, OS, IP |
| `k8s_list_pods` | Pods with status, restarts, node (filter by ns/label) |
| `k8s_list_deployments` | Deployments with replica counts |
| `k8s_list_services` | Services with type, IPs, ports |
| `k8s_list_events` | Cluster events (filterable by Warning type) |

### Diagnostics
| Tool | Description |
|---|---|
| `k8s_describe` | `kubectl describe` any resource |
| `k8s_logs` | Pod logs (tail, container, previous container) |
| `k8s_top_pods` | Pod CPU/memory (requires metrics-server) |
| `k8s_top_nodes` | Node CPU/memory (requires metrics-server) |
| `k8s_find_issues` | Full cluster health scan — failing pods, node pressure, bad deployments, warning events |
| `k8s_get_yaml` | Export any resource as YAML |

### Remediation
| Tool | Risk | Description |
|---|---|---|
| `k8s_restart_deployment` | Low | Rolling restart a deployment |
| `k8s_scale` | Medium | Scale deployment/statefulset replicas |
| `k8s_delete_pod` | Low-Med | Delete pod (triggers controller recreation) |
| `k8s_rollback_deployment` | Medium | Rollback to previous or specific revision |
| `k8s_apply_manifest` | Medium | Apply YAML/JSON manifest via stdin |
| `k8s_patch_resource` | Medium | JSON merge patch any resource |
| `k8s_node_operation` | High | Cordon / uncordon / drain a node |

## Setup

### Claude Desktop

Add the following to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "kubernetes": {
      "command": "uvx",
      "args": ["--from", "claude-plugin-kubernetes", "k8s-mcp"]
    }
  }
}
```

Requires [`uv`](https://docs.astral.sh/uv/getting-started/installation/) to be installed.

### Claude Code (CLI)

The `.mcp.json` in this repo is already configured. Clone the repo and open it in Claude Code — the server registers automatically.

To verify, run `/mcp` inside Claude Code. You should see `kubernetes` listed with 21 tools.

### Local development

```bash
git clone https://github.com/kanr/claude-plugin-kubernetes
cd claude-plugin-kubernetes
python3 -m venv .venv && .venv/bin/pip install -e .
```

## Requirements

- `kubectl` installed and on `$PATH`
- A valid `~/.kube/config` (or `$KUBECONFIG` set)
- Python 3.11+
- `metrics-server` installed in the cluster for `k8s_top_pods` / `k8s_top_nodes`

## Security Notes

- The kubectl wrapper uses `asyncio.create_subprocess_exec` — no shell, no injection risk.
- All resource names are passed as discrete arguments, never interpolated into shell strings.
- Remediation tools carry risk labels. Claude will warn you before applying destructive changes.
