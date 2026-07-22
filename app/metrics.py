"""
app/metrics.py — Prometheus metrics (Four Golden Signals)

Implements three of the four Golden Signals from Google's SRE Book:

  Traffic    → REQUEST_COUNT: total requests per endpoint (rate over time)
  Latency    → REQUEST_LATENCY: histogram of response times per endpoint
  Errors     → REQUEST_COUNT with status label (200, 503, 500, etc.)
  Saturation → CPU/memory from K8s cAdvisor (not in application code — the
               kernel knows saturation, the app doesn't)

Usage:
    from app.metrics import track

    @app.get("/search")
    @track("/search")        # wraps the function with metric recording
    def search(q: str):
        ...

The @track decorator separates observability from business logic.
Endpoints don't know or care that they're being measured.
"""

import functools
import time

from prometheus_client import (
    Counter,
    Histogram,
    CONTENT_TYPE_LATEST,
    generate_latest,
)

# Re-export for use in main.py's /metrics endpoint.
__all__ = ["track", "generate_latest", "CONTENT_TYPE_LATEST"]


# ── Metrics definitions ────────────────────────────────────────────────────────

REQUEST_COUNT = Counter(
    # Metric name in Prometheus format: lowercase, underscores, _total suffix.
    "search_api_requests_total",
    "Total number of HTTP requests by endpoint and HTTP status code.",
    # Labels: dimensions that can be filtered/grouped in PromQL queries.
    # endpoint: which path was called (/healthz, /readyz, /search)
    # status:   HTTP response code as string ("200", "503", "500")
    #
    # Example query — request rate per endpoint:
    #   rate(search_api_requests_total{endpoint="/search"}[1m])
    #
    # Example query — error rate:
    #   rate(search_api_requests_total{status=~"5.."}[1m])
    #   / rate(search_api_requests_total[1m])
    ["endpoint", "status"],
)

REQUEST_LATENCY = Histogram(
    "search_api_request_latency_seconds",
    "HTTP request latency in seconds by endpoint.",
    ["endpoint"],
    # Buckets define the histogram boundaries in seconds.
    # Chosen to bracket the SLO target of <300ms p99:
    #   0.01, 0.05, 0.1, 0.2 → sub-200ms (fast)
    #   0.3                   → the SLO boundary
    #   0.5, 1.0, 2.0, 5.0   → slow / degraded
    #
    # Having boundaries at 0.2 and 0.3 means we can see exactly what fraction
    # of requests land in the 200-300ms range — the SLO-critical window.
    #
    # Example query — p99 latency across all pods:
    #   histogram_quantile(0.99,
    #     sum by (le) (rate(search_api_request_latency_seconds_bucket[5m]))
    #   )
    #
    # Note: histogram_quantile aggregates across pods correctly because histograms
    # store counts per bucket (additive). Summaries cannot be aggregated this way.
    buckets=[0.01, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.0, 5.0],
)


# ── Decorator ─────────────────────────────────────────────────────────────────

def track(endpoint: str):
    """
    Decorator factory that records request count and latency for an endpoint.

    Usage:
        @app.get("/search")
        @track("/search")
        def search_handler(...):
            ...

    The decorator order matters: @track must be BELOW @app.get so that FastAPI
    registers the original function, but @track wraps it before FastAPI calls it.

    Args:
        endpoint: The endpoint path string used as the Prometheus label value.
                  Should match the @app.get() path for consistency.

    Thread safety: Prometheus client counters and histograms are thread-safe.
    The timing (time.time()) has microsecond resolution, sufficient for HTTP latency.
    """
    def decorator(func):
        @functools.wraps(func)  # preserve original function name and docstring
        def wrapper(*args, **kwargs):
            start = time.time()
            status = "200"   # assume success; override on exception
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                # FastAPI raises HTTPException with a status_code attribute.
                # Other exceptions default to "500".
                status = str(getattr(e, "status_code", "500"))
                raise   # re-raise so FastAPI handles the HTTP response
            finally:
                # finally runs whether or not an exception was raised,
                # ensuring metrics are always recorded — even for failed requests.
                duration = time.time() - start
                REQUEST_COUNT.labels(endpoint=endpoint, status=status).inc()
                REQUEST_LATENCY.labels(endpoint=endpoint).observe(duration)
        return wrapper
    return decorator
