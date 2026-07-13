import pytest
from fastapi.testclient import TestClient
from app.main import app, _model


@pytest.fixture(scope="session")
def client():
    """Create test client with lifespan — model loads before any test runs."""
    with TestClient(app) as c:
        yield c


def test_healthz(client):
    """Liveness probe returns 200 and status ok."""
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "uptime_seconds" in data


def test_readyz(client):
    """Readiness probe returns 200 and correct model name."""
    response = client.get("/readyz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["model"] == "all-MiniLM-L6-v2"


def test_search_toil(client):
    """Semantic search returns Toil as top result for repetitive work query."""
    response = client.get("/search?q=repetitive+manual+work+that+should+be+automated")
    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 3
    assert data["results"][0]["term"] == "Toil"
    assert data["results"][0]["score"] > 0.3


def test_metrics_format(client):
    """Metrics endpoint returns valid Prometheus text format."""
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "search_api_requests_total" in response.text
    assert "search_api_request_latency_seconds" in response.text
