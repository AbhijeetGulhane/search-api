import time
import functools
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

# ── Metrics ───────────────────────────────────────────────────────────────────

REQUEST_COUNT = Counter(
    "search_api_requests_total",
    "Total number of requests by endpoint and status",
    ["endpoint", "status"],          # labels: /search + 200, /search + 503, etc.
)

REQUEST_LATENCY = Histogram(
    "search_api_request_latency_seconds",
    "Request latency in seconds by endpoint",
    ["endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.0, 5.0],
)

# ── Four Golden Signals mapping ───────────────────────────────────────────────
# Traffic    → REQUEST_COUNT (total requests per endpoint)
# Latency    → REQUEST_LATENCY (histogram of response times)
# Errors     → REQUEST_COUNT with status="5xx" or status="4xx" label
# Saturation → CPU/memory from K8s cAdvisor (not in app code — comes from K8s)


# ── Decorator ─────────────────────────────────────────────────────────────────

def track(endpoint: str):
    """Decorator that records request count and latency for an endpoint."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            status = "200"
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                # Capture HTTP status from FastAPI HTTPException
                status = str(getattr(e, "status_code", "500"))
                raise
            finally:
                duration = time.time() - start
                REQUEST_COUNT.labels(endpoint=endpoint, status=status).inc()
                REQUEST_LATENCY.labels(endpoint=endpoint).observe(duration)
        return wrapper
    return decorator
