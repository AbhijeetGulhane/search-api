"""
load_test/loadgen.py — Async load generator for search-api

Sends concurrent search requests to drive CPU utilization above the
HPA threshold (50% of 250m request = 125m CPU per pod).

Usage:
    python3 load_test/loadgen.py

Requirements:
    pip install httpx
"""
import asyncio
import time
import httpx

BASE_URL = "http://localhost:8080"

QUERIES = [
    "what stops cascading failures",
    "repetitive manual work that should be automated",
    "memory killed process kernel",
    "how long before error budget runs out",
    "monitor latency traffic errors saturation",
    "kubernetes automatically scales pods",
    "mutual tls service authentication",
    "distributed key value store consensus",
    "readiness probe removes pod from load balancer",
    "postmortem blameless root cause analysis",
]

async def send_request(client: httpx.AsyncClient, query: str, worker_id: int):
    """Send a single search request and print the result."""
    try:
        response = await client.get(
            f"{BASE_URL}/search",
            params={"q": query, "top_k": 3},
            timeout=10.0,
        )
        if response.status_code == 200:
            top = response.json()["results"][0]["term"]
            print(f"[worker-{worker_id:02d}] '{query[:30]}...' → {top}")
        else:
            print(f"[worker-{worker_id:02d}] HTTP {response.status_code}")
    except Exception as e:
        print(f"[worker-{worker_id:02d}] Error: {e}")

async def worker(worker_id: int, duration: int):
    """Continuously send requests for the given duration."""
    async with httpx.AsyncClient() as client:
        end_time = time.time() + duration
        query_idx = worker_id % len(QUERIES)
        while time.time() < end_time:
            await send_request(client, QUERIES[query_idx], worker_id)
            query_idx = (query_idx + 1) % len(QUERIES)
            await asyncio.sleep(0.1)  # 10 req/s per worker

async def main():
    NUM_WORKERS = 10     # 10 concurrent workers = ~100 req/s total
    DURATION    = 120    # run for 2 minutes

    print(f"Starting load test: {NUM_WORKERS} workers × {DURATION}s")
    print(f"Target: {BASE_URL}")
    print(f"HPA threshold: 50% of 250m = 125m CPU per pod")
    print("-" * 60)

    tasks = [worker(i, DURATION) for i in range(NUM_WORKERS)]
    await asyncio.gather(*tasks)

    print("-" * 60)
    print("Load test complete. Watch HPA scale down over next 60s.")

if __name__ == "__main__":
    asyncio.run(main())
