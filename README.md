# search-api

A semantic search service over an SRE glossary, built and operated as a
complete reliability engineering project for Google SRE interview preparation.

> Not just a model behind an API — this is about everything *around* the
> model: how it's monitored, how it scales, how it fails, and how it recovers.

---

## What It Does

Accepts a natural language query and returns the most semantically similar
SRE terms from a 15-term glossary, ranked by cosine similarity score.

```bash
curl "http://localhost:8000/search?q=what+stops+cascading+failures"
# → Circuit Breaker (0.553), Readiness Probe (0.290), OOM Killer (0.266)

curl "http://localhost:8000/search?q=repetitive+manual+work"
# → Toil (0.378), HorizontalPodAutoscaler (0.145), Circuit Breaker (0.140)
```

Keyword search would return nothing — these queries share no exact words with
the definitions. The semantic model understands meaning, not just keywords.

---

## Stack

| Layer | Technology |
|-------|-----------|
| API | FastAPI + uvicorn |
| ML model | sentence-transformers `all-MiniLM-L6-v2` |
| Metrics | Prometheus client (Counter + Histogram) |
| Observability | Grafana (Day 11) |
| Container | Docker (`python:3.10-slim`) |
| Orchestration | Kubernetes (Minikube) |
| Testing | pytest + FastAPI TestClient |
| CI | GitHub Actions (Day 14) |

---

## SLOs

| SLI | Target | Notes |
|-----|--------|-------|
| Availability (`/search` HTTP 200) | 99.5% over 30 days | ~43 min error budget/month |
| p99 latency (`/search`) | < 300ms | Currently 300–500ms — at boundary |
| Readiness (`/readyz`) | 99.9% over 30 days | ~4.3 min error budget/month |

---

## Running Locally

```bash
git clone git@github.com:AbhijeetGulhane/search-api.git
cd search-api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
# Model loads in ~30 seconds, then:
curl http://localhost:8000/healthz
curl http://localhost:8000/readyz
curl "http://localhost:8000/search?q=error+budget"
```

---

## Running on Minikube

```bash
# Start Minikube and point Docker at its daemon
minikube start --cpus=4 --memory=8192 --driver=docker
eval $(minikube docker-env)

# Build image inside Minikube
docker build -t search-api:v1 .

# Deploy
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml

# Wait for all 3 pods to be Ready (~40 seconds)
kubectl get pods -n search-sre -w

# Port-forward and test
kubectl port-forward -n search-sre svc/search-api 8080:8080
curl http://localhost:8080/healthz
curl "http://localhost:8080/search?q=cascading+failures"
```

---

## Project Structure

```
search-api/
├── app/
│   ├── model.py        # SearchModel: SentenceTransformer + cosine similarity
│   ├── main.py         # FastAPI: /healthz /readyz /search /metrics endpoints
│   └── metrics.py      # Prometheus: REQUEST_COUNT counter + REQUEST_LATENCY histogram
├── data/
│   └── corpus.json     # 15 SRE term definitions (the search corpus)
├── tests/
│   └── test_api.py     # 4 pytest tests: healthz, readyz, Toil query, metrics format
├── k8s/
│   ├── namespace.yaml  # search-sre namespace
│   ├── configmap.yaml  # MODEL_NAME env var
│   ├── deployment.yaml # 3 replicas, readiness/liveness probes, resource limits
│   └── service.yaml    # ClusterIP: port 8080 → container 8000
├── chaos/
│   └── chaos_test.sh   # Delete pod, record timestamps, measure recovery
├── docs/
│   └── POSTMORTEM.md   # Postmortem #1: 36-second outage, root cause, action items
└── Dockerfile          # python:3.10-slim, layer-optimized build
```

---

## Endpoints

| Endpoint | Type | Description |
|----------|------|-------------|
| `GET /healthz` | Liveness | Process alive check. Always 200 once server starts. |
| `GET /readyz` | Readiness | Model loaded check. 503 during load, 200 after. |
| `GET /search?q=...&top_k=N` | Search | Semantic search, returns top N results (default 3). |
| `GET /metrics` | Prometheus | Prometheus text format metrics. |
| `GET /docs` | Swagger UI | Auto-generated API documentation. |

---

## Reliability Engineering

### Architecture

```
                    ┌─────────────────────────────────┐
                    │  Kubernetes (search-sre ns)      │
                    │                                  │
curl → port-forward │  Service (ClusterIP :8080)       │
                    │       │                          │
                    │  ┌────┴────────────────────┐    │
                    │  │  Deployment (3 replicas) │    │
                    │  │  ┌──────┐ ┌──────┐ ┌──────┐ │
                    │  │  │ Pod  │ │ Pod  │ │ Pod  │ │
                    │  │  │  ✅  │ │  ✅  │ │  ✅  │ │
                    │  └──└──────┘─└──────┘─└──────┘┘│
                    │                                  │
                    │  ConfigMap: MODEL_NAME           │
                    └─────────────────────────────────┘
```

### What Was Built and Why

**Day 2 — Semantic search engine (`app/model.py`)**
sentence-transformers converts text to 384-dimensional vectors. Cosine similarity
finds semantically related terms without keyword matching. MODEL_NAME comes from
an environment variable — decoupled from the image so it can be changed via
ConfigMap without a redeploy.

**Day 3 — FastAPI service (`app/main.py`)**
Separate `/healthz` (liveness) and `/readyz` (readiness) endpoints are not
cosmetic — they serve different purposes. `/healthz` returns 200 as soon as the
process starts. `/readyz` returns 503 until the model finishes loading (~30s).
This prevents Kubernetes from routing traffic to a pod that can't serve it yet.
Model loads synchronously in the lifespan context — no background threads to
cause PyTorch segfaults under concurrent load.

**Day 4 — Prometheus metrics (`app/metrics.py`)**
Four Golden Signals in code: Traffic (REQUEST_COUNT), Latency (REQUEST_LATENCY
histogram), Errors (status label on REQUEST_COUNT), Saturation (CPU/memory from
K8s cAdvisor). The `@track` decorator wraps each endpoint without modifying
business logic — separation of concerns.

**Day 5 — Docker (`Dockerfile`)**
`requirements.txt` copied and installed before application code — Docker layer
caching means pip install only reruns when dependencies change, not on every
code change. `--host 0.0.0.0` in CMD is non-negotiable — without it, uvicorn
listens on loopback only and the container is unreachable.

**Day 6 — Kubernetes deployment (`k8s/`)**
`imagePullPolicy: Never` uses the local Minikube image. Readiness probe with
`initialDelaySeconds: 30` gives the model time to load before probes fire.
Resource requests (250m CPU, 512Mi memory) let the scheduler make informed
placement decisions. Limits (1000m, 700Mi) prevent noisy-neighbor effects.

**Day 7 — Chaos engineering (`chaos/chaos_test.sh`)**
Real numbers: 8 seconds container creation, 28 seconds model loading, 36 seconds
total outage per pod failure. Model loading dominates — 78% of the outage window.
This drove the Day 9 decision to scale to 3 replicas.

**Day 9 — 3 replicas + ConfigMap**
Scaling to 3 replicas eliminates the single-replica outage gap from Postmortem #1.
ConfigMap decouples MODEL_NAME from the image — changing the model requires only
a ConfigMap update and rolling restart, not a new image build and push.

---

## Chaos Test Results

```bash
bash chaos/chaos_test.sh

# RESULTS
# Pod deleted:      09:35:05
# New pod appeared: 09:35:13  (+8s  container creation)
# Pod ready (1/1):  09:35:41  (+36s total outage window)
```

**Error budget impact:** 36 seconds against a 99.5% availability SLO
(43,200 seconds/month budget) = 0.08% of the monthly error budget consumed
in one incident.

---

## Postmortems

- [Postmortem #1 — Single Replica Pod Crash](docs/POSTMORTEM.md)
  36-second outage. Root cause: single replica + 28-second model load time.
  Fixed by Day 9 (scale to 3 replicas).

---

## Test Suite

```bash
source .venv/bin/activate
python -m pytest tests/ -v

# test_healthz        PASSED
# test_readyz         PASSED
# test_search_toil    PASSED  (Toil is top result, score > 0.3)
# test_metrics_format PASSED
# 4 passed in 6.42s
```

---

## Observability (Days 10–14 — In Progress)

- [x] Day 10 — Prometheus scraping pod metrics via RBAC
- [x] Day 11 — Grafana dashboard: request rate, p99 latency, error rate
- [x] Day 12 — NetworkPolicy (restrict traffic to namespace) + RBAC validation
- [x] Day 13 — HPA: auto-scale on CPU, load test with `load_test/loadgen.py`
- [ ] Day 14 — GitHub Actions CI, Postmortem #2, tag v1.0

---

## Known Limitations

- **p99 latency 300–500ms** — at the SLO boundary. Cause: `all-MiniLM-L6-v2`
  inference on CPU without batching. Fix: smaller model, GPU, or response caching.
- **Model reloads on every pod start** — 30-second readiness gap per restart.
  Fix: cache model weights in an emptyDir volume (future action item).
- **WSL2/Minikube requires session restore** — run `mk-restart` after laptop
  restart to rebuild image and redeploy.
