# RBAC Design — Prometheus Service Discovery

This document explains the Kubernetes RBAC configuration that allows Prometheus
to discover and scrape search-api pods automatically.

---

## The Problem

Prometheus uses Kubernetes service discovery (`kubernetes_sd_configs`) to find
pods to scrape. This requires calling the Kubernetes API:

```
"List all pods in search-sre namespace with label app=search-api"
```

Without authentication and authorization, the K8s API rejects this call with 403
Forbidden. Prometheus can't discover any targets and the /targets page shows
0 discovered endpoints.

---

## The Three Kubernetes Objects

### 1. ServiceAccount — The Identity

**File:** `k8s/prometheus-rbac.yaml`

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: prometheus
  namespace: monitoring
```

A ServiceAccount is a non-human identity for a process running in a pod.
On its own it has **zero permissions** — it's just a named identity.

When Prometheus starts, its deployment spec includes:
```yaml
spec:
  serviceAccountName: prometheus
```

Kubernetes automatically mounts a signed JWT token for this ServiceAccount
into the pod at:
```
/var/run/secrets/kubernetes.io/serviceaccount/token
```

Prometheus reads this token and includes it in every K8s API call as a
Bearer token in the Authorization header.

### 2. ClusterRole — The Permissions

**File:** `k8s/prometheus-rbac.yaml`

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: prometheus
rules:
- apiGroups: [""]
  resources:
  - nodes
  - nodes/proxy
  - services
  - endpoints
  - pods
  verbs: ["get", "list", "watch"]
- nonResourceURLs: ["/metrics"]
  verbs: ["get"]
```

A ClusterRole defines **what is allowed**. Kubernetes RBAC is deny-by-default:
if an action isn't explicitly listed here, it's forbidden.

**Why these verbs only?**

| Verb | Purpose |
|------|---------|
| `get` | Read a specific resource by name |
| `list` | List all resources of a type |
| `watch` | Stream changes in real time (used for live target updates) |

`create`, `delete`, `update`, `patch` are intentionally excluded. Prometheus
reads pod metadata — it never needs to modify anything. If Prometheus were
compromised, an attacker could read pod names and IPs but couldn't delete or
modify any workloads.

**Why ClusterRole, not Role?**

A `Role` grants permissions in one specific namespace. Prometheus scrapes pods
across namespaces (`search-sre`, potentially `monitoring`, etc.). A `ClusterRole`
with a `ClusterRoleBinding` grants permissions cluster-wide without needing to
create a Role in every namespace you want to monitor.

### 3. ClusterRoleBinding — The Connection

**File:** `k8s/prometheus-rbac.yaml`

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: prometheus
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: prometheus        # references the ClusterRole above
subjects:
- kind: ServiceAccount
  name: prometheus        # references the ServiceAccount above
  namespace: monitoring
```

The ClusterRoleBinding is the glue. Without it:
- The ServiceAccount exists but has zero permissions
- The ClusterRole exists but is bound to nothing

The binding says: "grant the permissions defined in the `prometheus` ClusterRole
to the `prometheus` ServiceAccount in the `monitoring` namespace."

---

## The Complete Authorization Flow

```
Prometheus pod starts
│
├── Kubernetes mounts ServiceAccount token at:
│   /var/run/secrets/kubernetes.io/serviceaccount/token
│
├── Prometheus reads the token
│
├── Every 30s: Prometheus calls K8s API
│   GET https://kubernetes.default.svc/api/v1/namespaces/search-sre/pods
│   Authorization: Bearer <serviceaccount-token>
│
├── K8s API server authenticates the token
│   → "This is system:serviceaccount:monitoring:prometheus"
│
├── K8s API server checks authorization (RBAC)
│   → Is there a ClusterRoleBinding for this ServiceAccount?
│   → Yes: ClusterRoleBinding "prometheus"
│   → Does the bound ClusterRole allow listing pods?
│   → Yes: ClusterRole "prometheus" allows [get, list, watch] on pods
│
├── K8s API returns list of pods with label app=search-api
│
└── Prometheus extracts pod IPs and scrapes /metrics on each
```

---

## Verification Commands

```bash
# Confirm Prometheus CAN list pods (service discovery permission)
kubectl auth can-i list pods \
  --as=system:serviceaccount:monitoring:prometheus \
  -n search-sre
# Expected: yes

# Confirm Prometheus CANNOT delete pods (least privilege)
kubectl auth can-i delete pods \
  --as=system:serviceaccount:monitoring:prometheus \
  -n search-sre
# Expected: no

# Confirm Prometheus CANNOT create pods
kubectl auth can-i create pods \
  --as=system:serviceaccount:monitoring:prometheus \
  -n search-sre
# Expected: no

# View the ClusterRole rules
kubectl describe clusterrole prometheus

# View the binding
kubectl describe clusterrolebinding prometheus
```

---

## What Happens Without Each Object

| Missing Object | Symptom |
|----------------|---------|
| ServiceAccount | Pod runs as default SA, which has no permissions — 403 on every API call |
| ClusterRole | SA exists but no permissions defined — 403 on every API call |
| ClusterRoleBinding | Both exist but aren't connected — SA still has no permissions |
| `serviceAccountName` in deployment | Pod runs as default SA — even if ClusterRoleBinding exists, it binds a different SA |

All four must be present and consistent for service discovery to work.

---

## Security Considerations

**What an attacker can do if Prometheus is compromised:**
- Read pod names, IPs, labels, and annotations across all namespaces
- Read service and endpoint metadata
- Scrape `/metrics` endpoints on any pod

**What an attacker cannot do:**
- Delete, create, or modify any pods, services, or other resources
- Access pod logs or exec into pods
- Read Secrets or ConfigMaps (not in the ClusterRole)
- Escalate privileges

**Further hardening (not implemented — future work):**
- Replace ClusterRole + ClusterRoleBinding with namespace-scoped Role + RoleBinding
  for each monitored namespace (more RBAC objects, tighter scope)
- Add network policies to restrict Prometheus egress to only the scrape targets
- Use short-lived tokens (TokenRequest API) instead of long-lived ServiceAccount tokens
