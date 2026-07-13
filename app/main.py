import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response

from app.model import SearchModel
from app.metrics import track, generate_latest, CONTENT_TYPE_LATEST

# ── State ─────────────────────────────────────────────────────────────────────
_model: SearchModel | None = None
_start_time: float = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model
    print("[startup] Loading model...")
    _model = SearchModel()
    print("[startup] Model ready.")
    yield
    print("[shutdown] Shutting down.")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="search-api",
    description="Semantic search over an SRE glossary.",
    version="0.1.0",
    lifespan=lifespan,
)


# ── Health endpoints ──────────────────────────────────────────────────────────
@app.get("/healthz")
@track("/healthz")
def healthz():
    """Liveness probe — process is alive."""
    return {"status": "ok", "uptime_seconds": round(time.time() - _start_time, 1)}


@app.get("/readyz")
@track("/readyz")
def readyz():
    """Readiness probe — model loaded and ready."""
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not ready")
    return {"status": "ready", "model": "all-MiniLM-L6-v2"}


# ── Search endpoint ───────────────────────────────────────────────────────────
@app.get("/search")
@track("/search")
def search(
    q: str = Query(..., min_length=1, max_length=200, description="Search query"),
    top_k: int = Query(3, ge=1, le=10, description="Number of results"),
):
    """Semantic search over the SRE glossary."""
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not ready")
    results = _model.search(q, top_k=top_k)
    return {"query": q, "top_k": top_k, "results": results}


# ── Metrics endpoint ──────────────────────────────────────────────────────────
@app.get("/metrics")
def metrics():
    """Prometheus metrics endpoint."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── Root ──────────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "service": "search-api",
        "endpoints": ["/healthz", "/readyz", "/search?q=your+query", "/metrics"],
        "docs": "/docs",
    }
