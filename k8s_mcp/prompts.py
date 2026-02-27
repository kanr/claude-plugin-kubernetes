"""
MCP prompt templates for Kubernetes troubleshooting workflows.

Each prompt provides a structured set of instructions that guide Claude through
a specific troubleshooting or operational workflow, referencing the exact tool
names available in this MCP server.
"""

from __future__ import annotations

from mcp.types import (
    GetPromptResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    TextContent,
)

# ---------------------------------------------------------------------------
# Prompt definitions
# ---------------------------------------------------------------------------

ALL_PROMPTS: list[Prompt] = [
    Prompt(
        name="diagnose-pod",
        description="Step-by-step diagnosis of a specific pod — describes it, checks events, pulls logs, inspects node conditions and resource limits.",
        arguments=[
            PromptArgument(name="pod_name", description="Name of the pod to diagnose", required=True),
            PromptArgument(name="namespace", description="Namespace the pod is in", required=True),
        ],
    ),
    Prompt(
        name="cluster-health-report",
        description="Comprehensive cluster health report — runs a health scan, drills into critical issues, and produces a summary.",
        arguments=[
            PromptArgument(name="namespace", description="Optional namespace to scope the report to", required=False),
        ],
    ),
    Prompt(
        name="incident-response",
        description="Incident triage workflow — scans for issues in a namespace, checks warning events, pulls logs from failing pods, and recommends remediation.",
        arguments=[
            PromptArgument(name="namespace", description="Namespace to triage", required=True),
        ],
    ),
    Prompt(
        name="debug-crashloop",
        description="Targeted CrashLoopBackOff debugging — inspects pod state, pulls previous container logs, checks for OOM kills, and reviews resource limits and probes.",
        arguments=[
            PromptArgument(name="pod_name", description="Name of the crashing pod", required=True),
            PromptArgument(name="namespace", description="Namespace the pod is in", required=True),
        ],
    ),
    Prompt(
        name="pre-deploy-checklist",
        description="Pre-deployment verification — reviews current deployments, pod health, recent events, and resource quotas in a namespace.",
        arguments=[
            PromptArgument(name="namespace", description="Namespace to verify before deploying", required=True),
        ],
    ),
]

# ---------------------------------------------------------------------------
# Prompt message builders
# ---------------------------------------------------------------------------

_PROMPT_BUILDERS: dict[str, callable] = {}


def _builder(name: str):
    """Decorator to register a prompt message builder."""
    def decorator(func):
        _PROMPT_BUILDERS[name] = func
        return func
    return decorator


@_builder("diagnose-pod")
def _diagnose_pod(args: dict[str, str]) -> GetPromptResult:
    pod_name = args["pod_name"]
    namespace = args["namespace"]
    return GetPromptResult(
        description=f"Diagnose pod {pod_name} in namespace {namespace}",
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=f"""\
Diagnose the pod "{pod_name}" in namespace "{namespace}" by following these steps in order:

1. **Describe the pod** — Use k8s_describe with resource_type="pod", resource_name="{pod_name}", namespace="{namespace}". Note the pod status, conditions, container states, and any restart counts.

2. **Check events** — Use k8s_list_events with namespace="{namespace}" and look for events related to this pod (scheduling failures, image pull errors, liveness/readiness probe failures, OOM kills).

3. **Pull current container logs** — Use k8s_logs with pod_name="{pod_name}", namespace="{namespace}", tail=200. Look for application errors, stack traces, or connection failures.

4. **Pull previous container logs if restarting** — If the pod has restarts or is in CrashLoopBackOff, use k8s_logs with pod_name="{pod_name}", namespace="{namespace}", previous=true, tail=200 to get logs from the last terminated container.

5. **Check the node** — From the describe output, identify which node the pod is running on. Use k8s_describe with resource_type="node" and that node name. Check for memory pressure, disk pressure, PID pressure, or NotReady conditions.

6. **Review resource limits** — From the pod describe output, check the resource requests and limits for each container. Flag if no limits are set (risk of OOM) or if requests are very close to limits (risk of throttling).

Produce a diagnosis summary with:
- Root cause (or most likely cause)
- Evidence supporting the diagnosis
- Recommended fix""",
                ),
            ),
        ],
    )


@_builder("cluster-health-report")
def _cluster_health_report(args: dict[str, str]) -> GetPromptResult:
    namespace = args.get("namespace")
    scope = f'namespace "{namespace}"' if namespace else "the entire cluster"
    ns_arg = f', namespace="{namespace}"' if namespace else ""
    return GetPromptResult(
        description=f"Health report for {scope}",
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=f"""\
Generate a comprehensive health report for {scope} by following these steps:

1. **Run the health scan** — Use k8s_find_issues{ns_arg} to get an overview of all detected problems (non-running pods, high-restart pods, unhealthy nodes, unavailable deployments, warning events).

2. **Drill into critical issues** — For each critical issue found:
   - For failing pods: use k8s_describe (resource_type="pod") and k8s_logs to understand the failure.
   - For unhealthy nodes: use k8s_describe (resource_type="node") to check conditions and allocatable resources.
   - For unavailable deployments: use k8s_describe (resource_type="deployment") to check rollout status.

3. **Check resource consumption** — Use k8s_top_pods{ns_arg} and k8s_top_nodes to identify any resource pressure.

4. **Review recent warning events** — Use k8s_list_events with warnings_only=true{ns_arg} to catch issues not surfaced by the health scan.

Produce a structured report with these sections:
- **Overall Status**: HEALTHY / DEGRADED / CRITICAL
- **Summary**: One-paragraph overview
- **Issues Found**: Table of issues with severity, affected resource, and description
- **Resource Utilization**: CPU and memory highlights
- **Recommendations**: Prioritized list of actions to take""",
                ),
            ),
        ],
    )


@_builder("incident-response")
def _incident_response(args: dict[str, str]) -> GetPromptResult:
    namespace = args["namespace"]
    return GetPromptResult(
        description=f"Incident response triage for namespace {namespace}",
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=f"""\
Perform an incident response triage for namespace "{namespace}" by following this workflow:

1. **Initial scan** — Use k8s_find_issues with namespace="{namespace}" to get a rapid overview of all problems. Note the count and severity of issues.

2. **Check warning events** — Use k8s_list_events with namespace="{namespace}", warnings_only=true. Look for recurring patterns: image pull failures, scheduling problems, probe failures, OOM kills, volume mount errors.

3. **Identify failing pods** — Use k8s_list_pods with namespace="{namespace}". For each pod that is not Running/Succeeded:
   - Use k8s_describe with resource_type="pod" and the pod name to understand the failure state.
   - Use k8s_logs with the pod name, namespace="{namespace}", tail=150 to capture recent logs.
   - If the pod is in CrashLoopBackOff, also use k8s_logs with previous=true to get the last crash output.

4. **Check node health** — Use k8s_list_nodes to check for nodes with pressure conditions or NotReady status. For any unhealthy node, use k8s_describe with resource_type="node" to get details.

5. **Check resource pressure** — Use k8s_top_pods with namespace="{namespace}" to check for pods near their resource limits. Use k8s_top_nodes to check cluster-level resource pressure.

6. **Assess blast radius** — Use k8s_list_deployments with namespace="{namespace}" to check how many deployments are affected and whether any have zero available replicas.

Produce an incident report with:
- **Severity**: P1 (service down) / P2 (degraded) / P3 (warning, no user impact)
- **Affected Services**: List of impacted deployments and pods
- **Timeline**: Sequence of events based on event timestamps
- **Root Cause Analysis**: Most likely cause with supporting evidence
- **Recommended Remediation Steps**: Ordered list of actions, referencing specific tools:
  - k8s_restart_deployment for rolling restarts
  - k8s_rollback_deployment to revert bad releases
  - k8s_scale to adjust replica counts
  - k8s_delete_pod to force-restart stuck pods
  - k8s_apply_manifest or k8s_patch_resource for configuration fixes""",
                ),
            ),
        ],
    )


@_builder("debug-crashloop")
def _debug_crashloop(args: dict[str, str]) -> GetPromptResult:
    pod_name = args["pod_name"]
    namespace = args["namespace"]
    return GetPromptResult(
        description=f"Debug CrashLoopBackOff for pod {pod_name} in namespace {namespace}",
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=f"""\
Debug the CrashLoopBackOff for pod "{pod_name}" in namespace "{namespace}" by following these steps:

1. **Get pod details** — Use k8s_describe with resource_type="pod", resource_name="{pod_name}", namespace="{namespace}". From the output, note:
   - Container state and last termination reason (OOMKilled, Error, etc.)
   - Exit code (137 = OOM/SIGKILL, 1 = application error, 143 = SIGTERM)
   - Restart count and time between restarts (exponential backoff pattern)
   - Container image and tag (verify correct image)

2. **Pull previous container logs** — Use k8s_logs with pod_name="{pod_name}", namespace="{namespace}", previous=true, tail=300. This captures output from the last crashed instance. Look for:
   - Stack traces or panic messages
   - "connection refused" or DNS resolution failures
   - Missing environment variables or config files
   - Permission denied errors

3. **Pull current container logs** — Use k8s_logs with pod_name="{pod_name}", namespace="{namespace}", tail=100. The container may produce output before crashing again.

4. **Check for OOM events** — Use k8s_list_events with namespace="{namespace}". Look for events with reason "OOMKilling" or "OOMKilled" targeting this pod. If OOM is confirmed:
   - Note the current memory limit from the describe output
   - Check k8s_top_pods with namespace="{namespace}" to see current memory usage of similar pods

5. **Inspect resource limits and probes** — From the describe output, check:
   - **Memory limits**: If the container is OOMKilled, the memory limit is too low or there is a memory leak
   - **CPU limits**: Very low CPU limits can cause extreme throttling, making liveness probes fail
   - **Liveness probe**: Check the probe configuration (path, port, initialDelaySeconds, periodSeconds, failureThreshold). A probe that is too aggressive or checks the wrong endpoint can kill healthy containers
   - **Readiness probe**: A failing readiness probe does not cause CrashLoopBackOff but may indicate the same underlying issue
   - **Startup probe**: If missing, the liveness probe may fire before the app is ready (common with slow-starting JVM apps)

6. **Get the full YAML** — Use k8s_get_yaml with resource_type="pod", resource_name="{pod_name}", namespace="{namespace}" to inspect the complete pod spec for any misconfigurations (wrong command, missing volume mounts, incorrect environment variables).

Produce a diagnosis with:
- **Crash Reason**: OOMKilled / Application Error / Probe Failure / Configuration Error
- **Evidence**: Specific log lines, events, or configuration values
- **Fix**: Concrete steps to resolve the issue (e.g., increase memory limit to X, fix liveness probe initialDelaySeconds, correct the image tag)""",
                ),
            ),
        ],
    )


@_builder("pre-deploy-checklist")
def _pre_deploy_checklist(args: dict[str, str]) -> GetPromptResult:
    namespace = args["namespace"]
    return GetPromptResult(
        description=f"Pre-deployment checklist for namespace {namespace}",
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=f"""\
Run a pre-deployment checklist for namespace "{namespace}" to verify the environment is healthy before deploying:

1. **Current deployment status** — Use k8s_list_deployments with namespace="{namespace}". Verify that all deployments have their desired replica count available. Flag any deployment where ready < desired.

2. **Pod health check** — Use k8s_list_pods with namespace="{namespace}". Check that:
   - All pods are in Running or Succeeded state
   - No pods have high restart counts (> 3)
   - No pods are stuck in Pending, ImagePullBackOff, or CrashLoopBackOff

3. **Recent warning events** — Use k8s_list_events with namespace="{namespace}", warnings_only=true. Check for:
   - Any events in the last 15 minutes that indicate instability
   - Recurring patterns that suggest an ongoing issue
   - Resource quota or limit range violations

4. **Resource utilization** — Use k8s_top_pods with namespace="{namespace}" to check current resource usage. Flag any pod using > 80% of its memory limit (risk of OOM during deployment surge). Use k8s_top_nodes to verify nodes have headroom for additional pods during rolling updates.

5. **Service health** — Use k8s_list_services with namespace="{namespace}" to verify services exist and have the expected configuration.

6. **Node readiness** — Use k8s_list_nodes to confirm all nodes are Ready and not cordoned. A deployment during node pressure may fail scheduling.

Produce a checklist report:
- **Status**: READY TO DEPLOY / CAUTION / DO NOT DEPLOY
- **Pre-existing Issues**: Any problems found that should be resolved first
- **Resource Headroom**: Available capacity for rolling update (nodes, CPU, memory)
- **Risks**: Potential issues that could affect the deployment
- **Recommendations**: Any actions to take before or during deployment""",
                ),
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_prompt(name: str, args: dict[str, str] | None) -> GetPromptResult:
    """Look up a prompt by name and return the rendered GetPromptResult."""
    builder = _PROMPT_BUILDERS.get(name)
    if builder is None:
        raise ValueError(f"Unknown prompt: {name}")
    return builder(args or {})
