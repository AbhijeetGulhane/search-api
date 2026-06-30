# search-api

A semantic search service over an SRE glossary, built and operated
as a complete reliability engineering project.

> Not just a model behind an API — this is about everything *around*
> the model: how it's monitored, how it scales, how it fails, and
> how it recovers.

## Status

��� In progress (Week 3-4, Google SRE Interview Prep)

## Stack

FastAPI · sentence-transformers · Prometheus · Grafana ·
Docker · Kubernetes (Minikube) · pytest · GitHub Actions

## Running locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## SLOs

| SLI | Target |
|---|---|
| Availability (/search HTTP 200) | 99.5% over 30 days |
| p99 latency (/search) | < 300ms |
| Readiness (/readyz) | 99.9% over 30 days |

## Architecture

(diagram coming Day 14)

## Postmortems

See [docs/POSTMORTEM.md](docs/POSTMORTEM.md)
