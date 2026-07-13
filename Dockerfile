# ── Base image ────────────────────────────────────────────────────────────────
FROM python:3.10-slim

# ── Working directory ─────────────────────────────────────────────────────────
WORKDIR /app

# ── Install dependencies first (layer caching — only rebuilds if requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Copy application code ─────────────────────────────────────────────────────
COPY app/ ./app/
COPY data/ ./data/

# ── Environment defaults ──────────────────────────────────────────────────────
ENV MODEL_NAME=all-MiniLM-L6-v2
ENV PORT=8000

# ── Expose port ───────────────────────────────────────────────────────────────
EXPOSE 8000

# ── Start command ─────────────────────────────────────────────────────────────
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
