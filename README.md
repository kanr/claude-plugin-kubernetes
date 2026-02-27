# Claude Plugin — Kubernetes

An MCP server that gives Claude deep Kubernetes cluster awareness, diagnostics, and remediation capabilities via `kubectl`.

## Tools (38)

### Awareness (20)

| Tool | Description |
|---|---|
| `k8s_cluster_info` | Current context, server version, API endpoint |
| `k8s_get_contexts` | List all kubeconfig contexts, indicate active one |
| `k8s_list_namespaces` | List namespaces with status and age |
| `k8s_list_nodes` | Nodes with roles, status, version, OS, IP, age |
| `k8s_list_pods` | Pods with status, restarts, node (filter by ns/label) |
| `k8s_list_deployments` | Deployments with replica counts and age |
| `k8s_list_services` | Services with type, cluster IP, external IP, ports |
| `k8s_list_images` | Container images running across pods |
| `k8s_list_events` | Cluster events sorted by time (filterable by Warning type) |
| `k8s_list_statefulsets` | StatefulSets with desired/ready counts and age |
| `k8s_list_ingresses` | Ingress resources with hosts, paths, backends |
| `k8s_list_jobs` | Jobs with completions, duration, age |
| `k8s_list_cronjobs` | CronJobs with schedule, last schedule time, active count |
| `k8s_list_configmaps` | ConfigMaps (metadata only) |
| `k8s_list_secrets` | Secrets (metadata only — never exposes data) |
| `k8s_list_pvcs` | PVCs with status, volume, capacity, access modes |
| `k8s_list_daemonsets` | DaemonSets with desired/current/ready counts |
| `k8s_list_hpa` | HPA with target, min/max replicas, metrics |
| `k8s_list_networkpolicies` | NetworkPolicies with pod selector and age |
| `k8s_api_resources` | Available API resource types including CRDs |

### Diagnostics (7)

| Tool | Description |
|---|---|
| `k8s_describe` | `kubectl describe` any resource |
| `k8s_logs` | Pod logs with tail, container, previous, since, and error filtering |
| `k8s_top_pods` | Pod CPU/memory usage (requires metrics-server) |
| `k8s_top_nodes` | Node CPU/memory usage (requires metrics-server) |
| `k8s_find_issues` | Comprehensive health scan — pods, nodes, deployments, statefulsets, jobs, PVCs, events |
| `k8s_get_yaml` | Resource as YAML (managed fields stripped by default; `raw=true` for full output) |
| `k8s_self_test` | Plugin health check — kubectl binary, cluster connectivity, auth, metrics-server |

### Remediation (11)

| Tool | Risk | Description |
|---|---|---|
| `k8s_restart_deployment` | Low | Rolling restart a deployment |
| `k8s_scale` | Medium | Scale deployment/statefulset (scale-to-zero requires explicit confirmation) |
| `k8s_delete_pod` | Low-Med | Delete pod for controller recreation; `force=true` for stuck pods |
| `k8s_rollback_deployment` | Medium | Roll back to previous or specific revision |
| `k8s_apply_manifest` | Medium | Apply YAML/JSON manifest; dry-run supported; cluster-scoped resources blocked by default |
| `k8s_patch_resource` | Medium | Strategic merge, JSON merge, or JSON patch any resource |
| `k8s_node_operation` | High | Cordon / uncordon / drain a node |
| `k8s_rollout_status` | Read-only | Check rollout progress (30s timeout) |
| `k8s_rollout_history` | Read-only | View all recorded revisions for a deployment |
| `k8s_delete_resource` | Medium | Delete any resource by type and name |
| `k8s_diff` | Read-only | Diff a manifest against live cluster state |

## MCP Resources

Static resources and namespace-scoped templates for direct data access:

| URI | Description |
|---|---|
| `k8s://contexts` | List kubeconfig contexts |
| `k8s://cluster-info` | Current context, server version, API endpoint |
| `k8s://namespaces` | All namespaces with status and age |
| `k8s://namespaces/{namespace}/pods` | Pods in a namespace |
| `k8s://namespaces/{namespace}/deployments` | Deployments in a namespace |
| `k8s://namespaces/{namespace}/services` | Services in a namespace |
| `k8s://namespaces/{namespace}/events` | Events in a namespace |

## MCP Prompts

Structured troubleshooting workflows:

| Prompt | Description |
|---|---|
| `diagnose-pod` | Step-by-step pod diagnosis (describe, events, logs, resources) |
| `cluster-health-report` | Full health scan with drill-down into issues |
| `incident-response` | Incident triage — scan, events, failing pods, blast radius |
| `debug-crashloop` | CrashLoopBackOff debugging — previous logs, OOM check, probes |
| `pre-deploy-checklist` | Pre-deployment verification — deployments, pods, resources, nodes |

## Setup

### Claude Code (CLI)

Add to your project's `.mcp.json`:

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

To verify, run `/mcp` inside Claude Code. You should see `kubernetes` listed with 38 tools.

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

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

Requires [`uv`](https://docs.astral.sh/uv/getting-started/installation/).

### Local development

```bash
git clone https://github.com/kanr/claude-plugin-kubernetes
cd claude-plugin-kubernetes
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

## Safety & Guardrails

The plugin ships with multiple layers of protection for production use:

| Feature | Details |
|---|---|
| **Read-only mode** | `K8S_MCP_READ_ONLY=true` — only awareness and diagnostic tools are registered |
| **Context allowlist** | `K8S_MCP_ALLOWED_CONTEXTS=ctx1,ctx2` — restrict which kubeconfig contexts can be used |
| **Namespace blocklist** | `K8S_MCP_NAMESPACE_BLOCKLIST` — defaults to `kube-system,kube-public,kube-node-lease` |
| **Namespace allowlist** | `K8S_MCP_NAMESPACE_ALLOWLIST` — if set, only these namespaces are writable |
| **Cluster-scoped blocklist** | `k8s_apply_manifest` blocks ClusterRoles, webhooks, CRDs, PVs by default; override with `K8S_MCP_ALLOW_CLUSTER_RESOURCES=true` |
| **Scale-to-zero gate** | `k8s_scale` requires `confirm_scale_to_zero=true` to scale to 0 replicas |
| **YAML pre-validation** | Manifests are validated with `yaml.safe_load_all()` before any kubectl call |
| **Audit logging** | All write operations logged to stderr with timestamp, tool name, and args |
| **No shell invocation** | Uses `asyncio.create_subprocess_exec` — no shell injection risk |
| **Concurrency limit** | Max 10 parallel kubectl subprocesses |
| **Output truncation** | Responses capped at 10 MB |
| **Timeouts** | 60s default; 300s for drain; 30s for rollout status; 5s for connectivity checks |
| **Preflight check** | On startup: verifies kubectl is on PATH, checks version, tests cluster connectivity |

## Requirements

- `kubectl` installed and on `$PATH`
- A valid `~/.kube/config` (or `$KUBECONFIG` set)
- Python 3.11+
- `metrics-server` installed in the cluster for `k8s_top_pods` / `k8s_top_nodes`
