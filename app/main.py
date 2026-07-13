import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from app.model import SearchModel

# ── State ─────────────────────────────────────────────────────────────────────
_model: SearchModel | None = None
_start_time: float = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load model synchronously before server accepts any connections."""
    global _model
    print("[startup] Loading model — server will accept connections when ready.")
    _model = SearchModel()
    print("[startup] Model ready. Server accepting connections.")
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
def healthz():
    """Liveness probe — process is alive and model is loaded."""
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - _start_time, 1),
    }


@app.get("/readyz")
def readyz():
    """Readiness probe — model loaded and ready to serve traffic."""
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not ready")
    return {"status": "ready", "model": "all-MiniLM-L6-v2"}


# ── Search endpoint ───────────────────────────────────────────────────────────
@app.get("/search")
def search(
    q: str = Query(..., min_length=1, max_length=200, description="Search query"),
    top_k: int = Query(3, ge=1, le=10, description="Number of results"),
):
    """Semantic search over the SRE glossary."""
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not ready")
    results = _model.search(q, top_k=top_k)
    return {"query": q, "top_k": top_k, "results": results}


# ── Root ──────────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "service": "search-api",
        "endpoints": ["/healthz", "/readyz", "/search?q=your+query"],
        "docs": "/docs",
    }
