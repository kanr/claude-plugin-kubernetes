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

### 1. Install dependencies

```bash
cd /Users/admin/Development/claude-plugin-kubernetes
python3 -m venv .venv
.venv/bin/pip install -e .
```

### 2. Register with Claude Code

The `.mcp.json` file in this directory is automatically picked up by Claude Code when you open this project. Alternatively, copy its contents into `~/.claude/mcp.json`.

### 3. Verify connection

Inside Claude Code, run:
```
/mcp
```
You should see `kubernetes` listed as a connected server with 21 tools.

## Requirements

- `kubectl` installed and on `$PATH`
- A valid `~/.kube/config` (or `$KUBECONFIG` set)
- Python 3.11+
- `metrics-server` installed in the cluster for `k8s_top_pods` / `k8s_top_nodes`

## Security Notes

- The kubectl wrapper uses `asyncio.create_subprocess_exec` — no shell, no injection risk.
- All resource names are passed as discrete arguments, never interpolated into shell strings.
- Remediation tools carry risk labels. Claude will warn you before applying destructive changes.
