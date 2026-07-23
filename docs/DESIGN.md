# Design Decisions

This document explains the engineering decisions made at each stage of the
search-api project — what was built, why it was built that way, and what
alternatives were considered.

---

## Day 2 — Semantic Search Engine (`app/model.py`)

### Why sentence-transformers?

The corpus is SRE terminology. A user querying "what stops cascading failures"
has no way to know that the relevant term is "Circuit Breaker" — they may never
have encountered that exact phrase. Keyword search returns nothing for this query.
Semantic search understands the *meaning* and returns Circuit Breaker correctly.

`sentence-transformers` with `all-MiniLM-L6-v2` was chosen because:
- Lightweight: 80MB model, ~512MB RAM when loaded — fits comfortably in K8s limits
- Fast: encodes a query in ~200ms on CPU without a GPU
- Accurate: pre-trained on large text corpora, understands SRE/technical vocabulary
- No infrastructure: runs in-process, no external vector database needed

### Why cosine similarity?

Cosine similarity measures the angle between two vectors, not their magnitude.
This makes it scale-invariant — a long definition and a short query can still
match well if they point in the same semantic direction. The alternative,
dot product, favors longer texts with larger magnitude vectors.

### Why load the corpus embeddings at startup?

The corpus is 15 static definitions. Embedding all 15 at startup (one-time cost,
~2 seconds) means each search query only needs to embed the query itself (~200ms),
not the entire corpus. The 15 pre-computed embedding vectors are held in memory —
a 15 × 384 float32 matrix, approximately 23KB. Negligible memory cost, major
latency benefit.

### Why MODEL_NAME from environment variable?

Decouples the model choice from the Docker image. Changing the model requires
only a ConfigMap update and rolling restart — no new image build, no new push,
no CI pipeline run. This is the Twelve-Factor App principle: store config in
the environment, not in the code.

---

## Day 3 — FastAPI Service (`app/main.py`)

### Why separate /healthz and /readyz?

They serve fundamentally different purposes and have different failure modes:

`/healthz` (liveness): "Is the process alive and not deadlocked?"
- Returns 200 as soon as uvicorn starts accepting connections
- Failure means: restart this container
- Should NOT check external dependencies (database, downstream services)
  — if it did, an external failure would cause a restart loop that can't fix anything

`/readyz` (readiness): "Is this pod ready to serve traffic right now?"
- Returns 503 until the model finishes loading (~30 seconds)
- Returns 200 after model is loaded and the first search would succeed
- Failure means: remove from load balancer, don't restart
- CAN check external dependencies — if they're unavailable, stop sending traffic

Using the same endpoint for both would mean either: traffic routes to pods mid-startup
(causing 500s), or pods restart unnecessarily when temporarily unable to serve.

### Why synchronous model loading in lifespan?

The initial implementation used a background thread to load the model, allowing
the server to start accepting connections immediately. This caused PyTorch segfaults
when a search request arrived while the background thread was still running model
inference — two threads calling the same PyTorch model simultaneously causes a
crash in the underlying C++ layer.

Synchronous loading in the lifespan context is correct for several reasons:
1. No thread safety issues — model is fully loaded before any request can arrive
2. Kubernetes handles the slow startup via readiness probe + initialDelaySeconds
3. The startup probe pattern was designed exactly for this: give the container
   time to initialize before liveness/readiness probes begin

### Why FastAPI over Flask?

- Built-in async support (uvicorn ASGI)
- Automatic OpenAPI/Swagger UI at /docs — free documentation
- Pydantic validation on query parameters (min_length, max_length, ge, le)
- Type hints throughout — faster to write, easier to test

---

## Day 4 — Prometheus Metrics (`app/metrics.py`)

### Four Golden Signals mapping

Google's SRE Book defines four golden signals for monitoring any service:

| Signal | What it measures | Implementation |
|--------|-----------------|----------------|
| **Traffic** | How much demand is the service receiving? | `REQUEST_COUNT` counter by endpoint |
| **Latency** | How long do requests take? | `REQUEST_LATENCY` histogram by endpoint |
| **Errors** | What fraction of requests are failing? | `REQUEST_COUNT` with `status` label (200, 503, 500) |
| **Saturation** | How full is the service? | CPU/memory from K8s cAdvisor (not in app code) |

Saturation comes from the Kubernetes metrics pipeline (cAdvisor → metrics-server →
Prometheus), not application code. The application doesn't know how much CPU it's
using — the kernel does.

### Why a histogram for latency, not a gauge or summary?

A **gauge** (current value) would only show the latest request's latency — useless
for understanding distribution.

A **summary** computes percentiles in the application process but can't be
aggregated across pods — if 3 pods each compute p99 independently, you can't
combine them to get the true fleet-wide p99.

A **histogram** counts observations in pre-defined buckets (0–10ms, 10–50ms, etc.)
and stores raw counts. Prometheus can aggregate histograms across pods and compute
accurate percentiles from the combined data:
```
histogram_quantile(0.99, rate(search_api_request_latency_seconds_bucket[5m]))
```
This gives the true p99 across all 3 replicas — impossible with summaries.

### Why the @track decorator pattern?

Separates observability concerns from business logic. The search endpoint
does semantic search — it shouldn't contain metric recording code. The decorator
wraps each endpoint transparently:
```python
@app.get("/search")
@track("/search")          # observability layer
def search(q: str):        # business logic
    ...
```
If we want to change how metrics are recorded (different library, different labels),
we change the decorator — not every endpoint. This is the Open/Closed principle:
open for extension (new metric types), closed for modification (endpoint logic).

### Why 9 histogram buckets at those specific values?

The buckets (0.01, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.0, 5.0 seconds) are chosen
to match the SLO target of < 300ms. Having buckets at 0.2 and 0.3 means we can
see exactly how many requests land in the 200–300ms range — the critical window
for SLO compliance. Generic buckets (e.g. 0.1, 1.0, 10.0) would hide this.

---

## Day 5 — Docker (`Dockerfile`)

### Why python:3.10-slim?

`python:3.10-slim` is a Debian-based image with only Python installed, no
development tools, compilers, or documentation. The tradeoff:
- `python:3.10` (full): ~900MB, includes compilers, pip cache, docs
- `python:3.10-slim`: ~130MB, Python only
- `python:3.10-alpine`: ~50MB, but Alpine uses musl libc which breaks some
  PyTorch wheels — caused segfaults in testing. Slim is the right balance.

### Why copy requirements.txt before application code?

Docker builds images as a stack of layers. Each instruction creates a new layer,
and layers are cached. If a layer's inputs haven't changed, Docker reuses the
cached version rather than rebuilding.

```dockerfile
# WRONG — requirements installed after code
COPY . .                          # code changes → this layer invalidates
RUN pip install -r requirements.txt  # pip reinstalls EVERY time code changes

# CORRECT — requirements installed first
COPY requirements.txt .           # only changes when dependencies change
RUN pip install -r requirements.txt  # cached unless requirements.txt changes
COPY app/ ./app/                  # code changes only invalidate this layer
```

With PyTorch in requirements (~2GB download), the wrong order means waiting
2+ minutes on every code change. The correct order means rebuilds take ~1 second
for code-only changes.

### Why --host 0.0.0.0 in CMD?

Without it, uvicorn defaults to `127.0.0.1` (loopback). Inside a container,
`127.0.0.1` means "only accept connections from within this container's network
namespace." Port mapping (`-p 8000:8000`) can't reach a loopback-bound service
because the mapping operates at the host network namespace level.

`0.0.0.0` means "accept connections on all network interfaces" — including the
container's `eth0` that receives traffic from the port mapping and from kube-proxy.

### Why no CMD for running tests?

The Dockerfile defines how to run the service, not how to develop it. Tests are
run locally via `python -m pytest` before building the image. The image only
contains the minimum needed to serve traffic — no test dependencies, no test files.

---

## Day 6 — Kubernetes Deployment (`k8s/`)

### Why imagePullPolicy: Never?

In Minikube with the Docker driver, the image is built directly inside Minikube's
Docker daemon via `eval $(minikube docker-env)`. The image exists locally but is
not pushed to any registry (Docker Hub, GCR, ECR). `imagePullPolicy: Never` tells
kubelet: "don't try to pull this image from a registry, use what's already in the
local cache."

Without this, kubelet gets `ErrImagePull` because `search-api:v1` doesn't exist
on Docker Hub.

In production (Day 14+), this would change to `imagePullPolicy: IfNotPresent`
with a real registry URL.

### Why initialDelaySeconds: 30 on the readiness probe?

The sentence-transformers model takes ~28 seconds to load into memory and compute
the corpus embeddings. Without an initial delay, the readiness probe would fire
immediately after the container starts, find the model not yet loaded, and the
pod would never become Ready — stuck in a restart loop.

30 seconds is calibrated from the Day 7 chaos test data:
```
New pod appeared: 09:35:13
Pod ready (1/1):  09:35:41   ← 28 seconds later
```

The liveness probe uses `initialDelaySeconds: 60` — longer than readiness —
because we want the model to have a chance to load before we start killing
the container for not being alive.

### Why resource requests and limits?

**Requests (250m CPU, 512Mi memory):**
Used by the scheduler to decide which node to place the pod on. A node with 4
CPUs and three search-api pods has 3 × 250m = 750m of CPU requests consumed,
leaving 3.25 CPUs of schedulable headroom. Requests represent the guaranteed
minimum — the container always gets at least this much when the node is saturated.

**Limits (1000m CPU, 700Mi memory):**
The absolute ceiling. If the container tries to use more than 1000m CPU, the
CFS scheduler throttles it (pulls it off the run queue). If it tries to use more
than 700Mi memory, the cgroup OOM killer fires — SIGKILL, exit code 137.

512Mi request vs 700Mi limit: the model uses ~509MB in practice (confirmed by
`docker stats`). The 700Mi limit gives ~190MB headroom for request handling
overhead without being tight enough to cause OOM kills on normal traffic.

### Why ClusterIP service type?

ClusterIP is a virtual IP that only exists inside the cluster — accessible from
other pods but not from outside. This is correct for an internal service.

External access in development comes from `kubectl port-forward` — a temporary
tunnel for testing, not a permanent exposure. In production, an Ingress controller
in front of the ClusterIP service would handle external traffic with TLS termination
and routing rules.

NodePort or LoadBalancer would expose the service externally — unnecessary and
a security concern for a service not meant to be public.

### Why port 8080 on the Service, 8000 on the container?

The Service port (8080) is what consumers use: `curl http://localhost:8080/search`.
The container port (8000) is what uvicorn listens on inside the pod.

The Service's `targetPort: 8000` translates between them. This decoupling means:
- The internal implementation (uvicorn on 8000) can change without affecting consumers
- Multiple versions could run on different ports simultaneously during a migration
- Follows convention: 8080 for HTTP services externally, application-specific ports internally

---

## Day 7 — Chaos Engineering (`chaos/chaos_test.sh`)

### Why chaos test before scaling to 3 replicas?

Testing with 1 replica first gives a clean measurement of the recovery baseline:
- Container creation time: 8 seconds (pure infrastructure cost)
- Model loading time: 28 seconds (application startup cost)
- Total: 36 seconds per pod failure

This baseline reveals that **model loading is 78% of the outage window** — the
dominant factor. This directly drove the architecture decision on Day 9: scaling
to 3 replicas doesn't reduce model loading time, but it makes pod failures
invisible to users (2 healthy pods continue serving traffic while 1 restarts).

If we had scaled to 3 replicas first, we would have never measured the single-pod
recovery time and wouldn't have the data to drive future optimizations (model
caching, smaller model, etc.).

### Why record timestamps rather than just timing?

Real timestamps (09:35:05, 09:35:13, 09:35:41) are more useful than durations
("took 36 seconds") for several reasons:
- They correlate with Prometheus metrics and logs for a complete incident picture
- They're what goes in a real postmortem — "at 09:35:05 the pod was deleted"
- They provide a baseline for future chaos tests — if recovery time grows to 90
  seconds, you can see when in the sequence it slowed down

### Why a blameless postmortem for a planned chaos test?

The postmortem format creates a habit. Writing a blameless postmortem for a
planned experiment trains you to write them the same way for real incidents.
The format (timeline → impact → root cause → contributing factors → action items)
is identical whether the trigger was intentional or accidental.

The STAR story at the end translates the technical work into behavioral interview
format — a concrete example with a measured result (36 seconds, 0.08% error budget).

---

## Day 9 — ConfigMap + 3 Replicas

### Why ConfigMap for MODEL_NAME instead of a hardcoded default?

Three reasons:

1. **Operational flexibility:** If we want to switch from `all-MiniLM-L6-v2`
   to a larger/smaller model, we update the ConfigMap and do a rolling restart.
   No new Docker image, no new CI run, no new deployment. Changing config should
   not require rebuilding artifacts.

2. **Environment parity:** The same Dockerfile and image works in dev, staging,
   and prod — only the ConfigMap differs. This is the Twelve-Factor App principle.

3. **Audit trail:** ConfigMap changes are tracked in git. You can see exactly when
   and why the model changed. Hardcoded defaults in Python files mix configuration
   with code.

### Why 3 replicas specifically?

3 is the minimum for meaningful high availability:
- Tolerates 1 pod failure while 2 pods serve traffic
- Tolerates a rolling update (1 pod unavailable at a time, per maxUnavailable: 1)
  while 2 pods continue serving
- Fits in Minikube's resource budget (3 × 512Mi = 1.5GB, well within 8GB allocation)

With 2 replicas: losing 1 pod drops to 1 replica — 0% headroom during a rolling update.
With 4+ replicas: better headroom, but 4 × 512Mi = 2GB just for the app, leaving
less for Prometheus, Grafana, and other system components.

### Why envFrom instead of individual env entries?

```yaml
# envFrom — inject all ConfigMap keys as env vars
envFrom:
- configMapRef:
    name: search-api-config

# Alternative — individual key references
env:
- name: MODEL_NAME
  valueFrom:
    configMapKeyRef:
      name: search-api-config
      key: MODEL_NAME
```

`envFrom` is simpler when you want all keys from a ConfigMap. As the ConfigMap
grows (adding LOG_LEVEL, MAX_RESULTS, etc.), the deployment doesn't need to be
updated — new keys automatically appear as environment variables.

Individual `env` entries are better when you only want specific keys, or when
you need to rename them.

---

## Day 10 — Prometheus RBAC

### Why RBAC for Prometheus?

Prometheus uses the Kubernetes API to discover pods dynamically (kubernetes_sd_configs).
Without RBAC, it has no credentials to call the API and can't discover any targets.

The principle of least privilege applies: Prometheus needs to *read* pod metadata
to find IPs and ports to scrape. It doesn't need to *create*, *delete*, or *modify*
anything. The ClusterRole grants only `get`, `list`, `watch` on pods, services,
and endpoints — the minimum required for service discovery.

### Why a ClusterRole instead of a Role?

A `Role` grants permissions in one specific namespace. Prometheus discovers pods
across all namespaces — currently `search-sre`, potentially `monitoring` and others
later. A `ClusterRole` with a `ClusterRoleBinding` grants permissions cluster-wide.

For tighter security, you could use `Role` + `RoleBinding` in each namespace
separately — but this requires updating RBAC every time you add a new namespace
to scrape. `ClusterRole` is the standard approach for monitoring agents.

### Why kubernetes_sd_configs instead of static_configs?

```yaml
# Static config — hardcoded pod IPs
static_configs:
  - targets: ['10.244.0.5:8000', '10.244.0.6:8000', '10.244.0.7:8000']

# Kubernetes SD — dynamic discovery via K8s API
kubernetes_sd_configs:
  - role: pod
    namespaces:
      names: [search-sre]
```

Pod IPs change constantly — when pods restart, they get new IPs. Hardcoded static
configs would break every time a pod is replaced. Kubernetes service discovery
calls the K8s API on every scrape interval to get current pod IPs — zero
configuration required when pods change.

This is also why the ClusterRole grants `list` and `watch` on pods — Prometheus
maintains a watch on the pod list and gets notified immediately when pods change.

### Why relabel_configs?

Kubernetes SD discovers *all* pods in the namespace by default. relabel_configs
filter and transform the discovered targets:

```yaml
# Filter: only keep pods with label app=search-api
- source_labels: [__meta_kubernetes_pod_label_app]
  action: keep
  regex: search-api

# Transform: scrape on port 8000, not whatever port K8s reports
- source_labels: [__address__]
  action: replace
  regex: ([^:]+)(?::\d+)?
  replacement: $1:8000
  target_label: __address__

# Enrich: add pod name as a label on every metric
- source_labels: [__meta_kubernetes_pod_name]
  action: replace
  target_label: pod
```

The `pod` label added by relabel_configs is what makes individual pod metrics
distinguishable in Prometheus queries — without it, all 3 pods' metrics merge
into indistinguishable time series.

### Verification

```bash
# Confirm Prometheus can list pods (what service discovery needs)
kubectl auth can-i list pods \
  --as=system:serviceaccount:monitoring:prometheus \
  -n search-sre
# → yes

# Confirm Prometheus cannot delete pods (least privilege)
kubectl auth can-i delete pods \
  --as=system:serviceaccount:monitoring:prometheus \
  -n search-sre
# → no
```
## Day 11 — Grafana Dashboard (`k8s/grafana-deployment.yaml`)

### Why Grafana over the Prometheus built-in UI?

Prometheus's `/graph` UI is functional but limited — single queries, no multi-panel
layouts, no persistent dashboards, no threshold lines. Grafana provides:
- Multi-panel dashboards that persist across sessions
- Visual threshold lines (e.g. the 300ms SLO boundary on p99 latency)
- Per-series coloring and legend labels
- Time range selection and auto-refresh
- Dashboard sharing via JSON export

For an SRE interview, a Grafana screenshot showing the four golden signals on one
screen is significantly more impactful than a Prometheus expression box.

### Why anonymous admin access?

```yaml
- name: GF_AUTH_ANONYMOUS_ENABLED
  value: "true"
- name: GF_AUTH_ANONYMOUS_ORG_ROLE
  value: "Admin"
- name: GF_AUTH_DISABLE_LOGIN_FORM
  value: "true"
```

Local development only — eliminates the friction of managing credentials in a
non-production environment. In production, remove these three env vars and configure
proper authentication (LDAP, OAuth, or Grafana's built-in user management).

The tradeoff is deliberate: development velocity vs security. These settings would
never appear in a production deployment.

### Why emptyDir for storage?

Grafana stores dashboards in a SQLite database at `/var/lib/grafana`. An `emptyDir`
volume persists this data as long as the pod is running but loses it on pod restart.

For development, this is acceptable — dashboards can be recreated in minutes.
For production, a `PersistentVolumeClaim` (PVC) backed by a cloud storage class
would preserve dashboards across pod restarts and node replacements.

Not using a PVC here keeps the setup simple and avoids StorageClass configuration
in Minikube, which varies by cluster configuration.

### Why grafana/grafana:11.1.0 (pinned version)?

Pinned to a specific version rather than `latest` for reproducibility. `latest`
changes when Grafana releases a new version — what works today may break tomorrow
if a new version changes a default setting or API. Pinning ensures the deployment
is reproducible: the same YAML produces the same result next week, next month.

In production, pinned versions also make vulnerability scanning tractable —
you know exactly what you're running.

### Why prometheus.monitoring.svc.cluster.local as the datasource URL?

Grafana and Prometheus both run in the `monitoring` namespace. Kubernetes DNS
provides a predictable hostname for any Service:
```
<service-name>.<namespace>.svc.cluster.local:<port>
```

This is preferable to:
- `localhost:9090` — would look for Prometheus inside the Grafana container, not a separate pod
- A ClusterIP address — IPs change when Services are recreated; the DNS name is stable
- A NodePort — exposes Prometheus externally, unnecessary for internal communication

### The Three Panels — PromQL Explained

**Panel 1: Request Rate (Traffic golden signal)**
```
rate(search_api_requests_total[1m])
```
`rate()` computes the per-second rate of increase of a counter over the last 1 minute.
Counters only go up — `rate()` handles resets (pod restarts) gracefully by detecting
when a counter drops and treating it as a reset to zero.

Result: multiple time series, one per {endpoint, pod} combination. Shows traffic
distribution across pods and endpoints simultaneously.

**Panel 2: p99 Latency (Latency golden signal)**
```
histogram_quantile(0.99,
  sum by (le) (rate(search_api_request_latency_seconds_bucket[1m]))
)
```
Breaking this down inside-out:
- `rate(..._bucket[1m])`: per-second rate of observations falling into each bucket
- `sum by (le)`: aggregate across all pods (summing bucket counts is valid for histograms)
- `histogram_quantile(0.99, ...)`: compute the 99th percentile from the aggregated buckets

The `sum by (le)` is critical — it correctly merges histograms from all 3 pods into
one fleet-wide p99. This is why we chose histogram over summary: summaries from
different pods cannot be meaningfully summed.

Observed: ~10ms for probe traffic, 300–500ms for search queries.
The 300ms SLO threshold line makes violations immediately visible.

**Panel 3: Error Rate (Errors golden signal)**
```
rate(search_api_requests_total{status=~"5.."}[1m])
```
`status=~"5.."` is a regex label matcher — matches any status starting with 5
(500, 503, 504, etc.). "No data" is the correct and desired state — it means
zero server errors in the observed window.

The panel is correctly configured even with no data. When errors occur (overload,
dependency failure, bug), this panel immediately shows the rate.

### What the Dashboard Proved

- Request Rate panel showed 3 separate `/healthz` lines — one per pod — confirming
  the `pod` label from Day 10's relabel_configs is working correctly
- p99 Latency confirmed ~10ms for probes, consistent with pure in-memory health checks
- Error Rate showing "No data" confirmed zero 5xx responses across all pods
- All four golden signals now visible in one dashboard: Traffic ✅ Latency ✅
  Errors ✅ Saturation (from K8s cAdvisor, visible in Grafana's built-in K8s dashboards)
## Day 12 — NetworkPolicy + RBAC Validation (`k8s/networkpolicy.yaml`)

### Why NetworkPolicy?

Without a NetworkPolicy, Kubernetes allows all pod-to-pod traffic by default —
any pod in any namespace can reach any other pod on any port. This is the
"flat network" model: convenient but a security liability.

A NetworkPolicy restricts which traffic is allowed to reach search-api pods
and which traffic they can send. It implements the principle of least privilege
at the network layer, complementing the RBAC least privilege at the API layer.

**Threat model without NetworkPolicy:**
- A compromised pod in any namespace can reach search-api on port 8000
- A compromised search-api pod can reach any other service in the cluster
- Lateral movement is unrestricted

**Threat model with NetworkPolicy:**
- Only pods in search-sre and monitoring namespaces can reach search-api
- search-api pods can only reach DNS, HTTPS external endpoints, and each other
- Lateral movement from a compromised search-api pod is significantly restricted

### Why policyTypes: [Ingress, Egress]?

Specifying both types means the NetworkPolicy controls both directions:
- Ingress: who can send traffic TO search-api pods
- Egress: where search-api pods can send traffic

If only Ingress is specified, egress remains unrestricted — a compromised pod
could still exfiltrate data or reach internal services. Specifying both gives
complete control over the pod's network posture.

### Ingress Rules — Why These Sources?

**Rule 1: Allow from search-sre namespace (podSelector: {})**
```yaml
- from:
  - podSelector: {}   # empty selector = all pods in this namespace
```
`podSelector: {}` with no `namespaceSelector` means "any pod in the same
namespace as this policy" (search-sre). This allows:
- kubectl port-forward connections (which appear to come from within the namespace)
- Future intra-namespace services that might call search-api

**Rule 2: Allow from monitoring namespace**
```yaml
- from:
  - namespaceSelector:
      matchLabels:
        kubernetes.io/metadata.name: monitoring
```
Without this rule, Prometheus (in the monitoring namespace) can't reach
search-api pods' /metrics endpoint. The NetworkPolicy would silently drop
Prometheus's scrape requests, causing targets to show as DOWN.

`kubernetes.io/metadata.name` is an automatic label Kubernetes adds to every
namespace — its value equals the namespace name. This is the standard way to
select a namespace by name in a NetworkPolicy.

### Egress Rules — Why These Destinations?

**Rule 1: DNS on port 53**
```yaml
- ports:
  - port: 53
    protocol: UDP
  - port: 53
    protocol: TCP
```
DNS resolution is required for almost everything. Without this:
- The model can't resolve HuggingFace CDN hostnames on first download
- Any external HTTP call fails at DNS resolution
- Even internal K8s service names fail (they resolve via kube-dns)

Both UDP and TCP are needed — DNS uses UDP by default, but falls back to TCP
for responses larger than 512 bytes (common with DNSSEC or large record sets).

**Rule 2: HTTPS on port 443**
```yaml
- ports:
  - port: 443
    protocol: TCP
```
The sentence-transformers model downloads from HuggingFace's CDN over HTTPS
on first pod start. Without this egress rule, the model download would be
blocked and the pod would fail to start.

In a fully air-gapped production environment, the model would be baked into
the Docker image (eliminating the download) and this rule could be removed.

**Rule 3: Intra-namespace egress**
```yaml
- to:
  - podSelector: {}
```
Allows search-api pods to reach other pods in search-sre. Currently no other
services exist here, but this allows future services (a caching layer, a
feature flag service) without requiring NetworkPolicy updates.

### Why Verify with kubectl auth can-i After NetworkPolicy?

NetworkPolicy controls network-level traffic; RBAC controls API-level access.
They're independent — NetworkPolicy doesn't affect what the Prometheus
ServiceAccount can do via the K8s API, and RBAC doesn't affect TCP connections.

Verifying both after each change confirms:
1. Network: Prometheus can still scrape /metrics (targets show UP in /targets)
2. API: Prometheus SA still has list/watch on pods (auth can-i list = yes)
3. API: Prometheus SA still cannot delete pods (auth can-i delete = no)

This dual verification ensures neither the network layer nor the API layer
was accidentally misconfigured by the change.

### Verification Results

```bash
# NetworkPolicy applied
kubectl get networkpolicy -n search-sre
# NAME                  POD-SELECTOR       AGE
# search-api-netpol     app=search-api     Xs

# Prometheus targets still UP after NetworkPolicy (Rule 2 working)
# http://localhost:9090/targets → 3/3 search-api targets UP

# RBAC unchanged by NetworkPolicy (separate control plane)
kubectl auth can-i list pods \
  --as=system:serviceaccount:monitoring:prometheus -n search-sre
# yes

kubectl auth can-i delete pods \
  --as=system:serviceaccount:monitoring:prometheus -n search-sre
# no
```
