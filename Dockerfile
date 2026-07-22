# search-api Dockerfile
#
# Design decisions:
#
# 1. python:3.10-slim — 130MB vs 900MB (full) vs 50MB (alpine).
#    Alpine uses musl libc which breaks PyTorch wheels → segfaults in testing.
#    Slim is Debian-based (same as full) but without compilers and docs.
#
# 2. Layer ordering: requirements before code.
#    Docker caches layers. If code changes but requirements don't, the pip
#    install layer is reused → rebuild takes <1s instead of 2+ minutes.
#    WRONG: COPY . . then RUN pip install  (code change = full reinstall)
#    RIGHT: COPY requirements.txt then pip then COPY code (code change = fast)
#
# 3. --no-cache-dir: don't store pip's download cache in the image.
#    Reduces image size by ~200MB (PyTorch download cache is large).
#
# 4. --host 0.0.0.0: listen on all interfaces, not just loopback.
#    Without this, uvicorn binds to 127.0.0.1 (loopback inside the container).
#    Port mapping (-p 8000:8000) and kube-proxy DNAT operate at the host network
#    namespace level — they can't reach a loopback-bound service.
#
# 5. No CMD for tests: the image contains only what's needed to serve traffic.
#    Tests run locally before building the image. No test files in the image.

# ── Base image ────────────────────────────────────────────────────────────────
FROM python:3.10-slim

# ── Working directory ─────────────────────────────────────────────────────────
# All subsequent COPY, RUN, CMD instructions use this as the working directory.
# The process inside the container also starts here.
WORKDIR /app

# ── Dependencies (cached layer) ───────────────────────────────────────────────
# Copy ONLY requirements.txt first. This layer is cached and only rebuilds
# when requirements.txt changes — not when application code changes.
COPY requirements.txt .

# Install all dependencies. Key packages and why they're heavy:
# - sentence-transformers: pulls in PyTorch (~1.5GB installed)
# - fastapi + uvicorn: <10MB
# - prometheus-client: <5MB
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ──────────────────────────────────────────────────────────
# These layers rebuild on every code change — fast because no heavy downloads.
COPY app/ ./app/
COPY data/ ./data/

# ── Configuration defaults ────────────────────────────────────────────────────
# Default values for environment variables.
# In K8s: overridden by ConfigMap via envFrom in the Deployment spec.
# Locally: used as-is unless the caller sets them.
ENV MODEL_NAME=all-MiniLM-L6-v2
ENV PORT=8000

# ── Port documentation ────────────────────────────────────────────────────────
# EXPOSE is metadata only — it doesn't open any port.
# The actual port mapping happens at runtime:
#   docker run -p 8000:8000 search-api:v1        (Docker)
#   Service targetPort: 8000 in k8s/service.yaml  (Kubernetes)
EXPOSE 8000

# ── Start command ─────────────────────────────────────────────────────────────
# Use exec form (JSON array) not shell form (string).
# Exec form: PID 1 = uvicorn. Receives signals directly (SIGTERM for graceful shutdown).
# Shell form: PID 1 = /bin/sh. uvicorn is a child process that may not receive signals.
# See: the PID 1 signal quirk in docs/DESIGN.md
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
