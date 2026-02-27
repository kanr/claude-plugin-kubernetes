---
name: k8s-health
description: Run a full Kubernetes cluster health scan
---

Run a full Kubernetes cluster health scan:

1. Use k8s_find_issues to detect any problems across all namespaces
2. Use k8s_list_pods with all_namespaces=true to get a full pod overview
3. Summarize findings: highlight any non-Running pods, high-restart containers, or warning events. If everything looks healthy, say so clearly.
