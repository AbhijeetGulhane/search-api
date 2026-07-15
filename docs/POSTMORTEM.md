# Postmortem #1 — Single Replica Pod Crash

**Date:** 2026-07-15
**Author:** Abhijeet Gulhane
**Severity:** SEV-2 (Full service outage, single replica)
**Duration:** 36 seconds (09:35:05 → 09:35:41)
**Status:** Resolved

---

## Summary

The search-api pod was deleted (simulating a crash) in the `search-sre` namespace.
The service was completely unavailable for 36 seconds until the replacement pod
passed its readiness probe. Root cause: single replica deployment with no redundancy.
The 28-second model loading time dominates the recovery window.

---

## Timeline

| Time | Event |
|------|-------|
| 09:35:05 | Pod `search-api-8777bcf98-hkzqn` deleted (chaos test) |
| 09:35:05 | Deployment controller detects replica count = 0, creates replacement |
| 09:35:13 | New pod `search-api-8777bcf98-dbf67` appears (ContainerCreating → Running) |
| 09:35:13 | Pod running but NOT ready — readiness probe failing (model loading) |
| 09:35:41 | Readiness probe passes — pod 1/1 Ready, traffic restored |

**Total outage window: 36 seconds**
**Container creation time: 8 seconds**
**Model loading time: 28 seconds**

---

## Impact

- **Service:** search-api `/search`, `/healthz`, `/readyz` endpoints
- **Namespace:** search-sre
- **Duration:** 36 seconds complete unavailability
- **Requests affected:** All requests during 09:35:05 → 09:35:41 would return
  connection refused or 503 (no healthy endpoints in Service)
- **Data loss:** None — stateless service

---

## Root Cause

Single replica deployment (`replicas: 1`). When the only pod is deleted or crashes,
there are zero healthy pods. The Service has no endpoints to route to — all traffic
fails until the replacement pod passes its readiness probe.

The 28-second model loading time is the dominant factor. The sentence-transformer
model (`all-MiniLM-L6-v2`) must be loaded into memory and corpus embeddings computed
before the pod can serve traffic. During this window, `/readyz` returns 503 and the
pod IP is excluded from the Service's Endpoints.

---

## Contributing Factors

1. **No redundancy:** `replicas: 1` means any pod failure = full outage
2. **Slow startup:** 28 seconds for model load is a long readiness window
3. **No PodDisruptionBudget:** nothing prevented the pod from being deleted
4. **No HPA:** no automatic scaling under load (added Day 13)

---

## Resolution

Pod replaced automatically by the Deployment controller. No manual intervention
required. Service restored at 09:35:41.

---

## Action Items

| # | Action | Priority | Day |
|---|--------|----------|-----|
| 1 | Scale to 3 replicas — eliminates single point of failure | P0 | Day 9 |
| 2 | Add PodDisruptionBudget `minAvailable: 2` | P1 | Day 12 |
| 3 | Cache model weights in an emptyDir volume to reduce load time | P2 | Future |
| 4 | Add HPA to scale under traffic load | P2 | Day 13 |

---

## Lessons Learned

- **Single replica = guaranteed outage on any pod failure.** Even Kubernetes'
  self-healing can't avoid the gap between pod deletion and readiness.
- **Readiness probe is critical.** Without it, traffic would route to the new pod
  before the model loaded — causing 503s from the application instead of clean
  connection refusals from the LB.
- **Model loading time dominates recovery.** Optimizing startup time (caching,
  smaller model, lazy loading) has more impact than container creation speed.
- **Real numbers matter.** 36 seconds is the actual SLO impact — not estimated,
  measured. This feeds directly into error budget calculations:
  36s outage against a 99.5% availability SLO (43,200s budget/month) consumes
  0.08% of the monthly error budget in one incident.

---

## STAR Story (Week 8 Behavioral Prep)

**Situation:** Deployed search-api as a single-replica Kubernetes service. Ran a
chaos test simulating a pod crash to measure real recovery time.

**Task:** Measure the actual outage window and identify the dominant failure mode
to drive architectural improvements.

**Action:** Wrote `chaos/chaos_test.sh` to delete the pod and record exact
timestamps. Observed: 8 seconds container creation + 28 seconds model loading =
36 seconds total outage per pod failure.

**Result:** Identified model loading time as the dominant factor (78% of outage
window). Drove the Day 9 decision to scale to 3 replicas, reducing the probability
of a single-replica gap to near zero. Documented in a blameless postmortem with
four concrete action items tied to specific implementation days.
