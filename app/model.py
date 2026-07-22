"""
app/model.py — Semantic search engine

Wraps sentence-transformers to provide semantic similarity search over a
static SRE glossary corpus. The model is loaded once at startup; all
subsequent searches are fast in-memory operations.

Design decisions:
- Why sentence-transformers: understands semantic meaning, not just keywords.
  "what stops cascading failures" → Circuit Breaker, even with no word overlap.
- Why all-MiniLM-L6-v2: 80MB, ~200ms CPU inference, good accuracy balance.
- Why cosine similarity: scale-invariant, works well for comparing short queries
  against longer definitions.
- Why pre-compute corpus embeddings at startup: 15 embeddings × one-time cost
  vs embedding the corpus on every query. The corpus is static.
- Why MODEL_NAME from env: decouples model choice from the image. Change the
  ConfigMap → rolling restart → new model. No rebuild required.
"""

import json
import os
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

# Corpus lives at the project root, one level above this file.
CORPUS_PATH = Path(__file__).parent.parent / "data" / "corpus.json"

# Read from environment variable with a sensible default.
# In K8s: set via ConfigMap (k8s/configmap.yaml) → envFrom in deployment.
# Locally: defaults to all-MiniLM-L6-v2 if MODEL_NAME not set.
MODEL_NAME = os.getenv("MODEL_NAME", "all-MiniLM-L6-v2")


class SearchModel:
    """
    Semantic search over the SRE glossary corpus.

    Lifecycle:
    1. __init__: load model + corpus + pre-compute embeddings (~30s first run)
    2. search(): encode query + cosine similarity + return top-k (~200ms per query)

    Thread safety: NOT thread-safe. Do not call search() from multiple threads
    simultaneously — PyTorch's C++ layer will segfault. The FastAPI lifespan
    pattern (synchronous loading before server starts) prevents concurrent access
    during startup.
    """

    def __init__(self):
        print(f"[SearchModel] Loading model: {MODEL_NAME}")

        # Downloads model on first run (~80MB), cached to ~/.cache/huggingface
        # after that. In K8s, each pod downloads independently on first start.
        # Future optimization: pre-bake the cache into the Docker image or
        # use a shared emptyDir volume to avoid repeated downloads.
        self.model = SentenceTransformer(MODEL_NAME)

        self.corpus = self._load_corpus()

        # Pre-compute embeddings for all corpus definitions once.
        # Result: float32 matrix of shape (15, 384) — 15 definitions × 384 dims.
        # Stored in memory: 15 × 384 × 4 bytes = ~23KB. Negligible.
        self.embeddings = self._embed_corpus()

        print(f"[SearchModel] Ready. {len(self.corpus)} terms indexed.")

    def _load_corpus(self) -> list[dict]:
        """Load the SRE glossary from data/corpus.json."""
        with open(CORPUS_PATH) as f:
            return json.load(f)

    def _embed_corpus(self) -> np.ndarray:
        """
        Embed all corpus definitions into a 2D float32 matrix.
        Called once at startup — results reused for every search query.
        """
        definitions = [item["definition"] for item in self.corpus]
        return self.model.encode(definitions, convert_to_numpy=True)

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        """
        Return the top-k most semantically similar terms for the given query.

        Algorithm:
        1. Encode the query into a 384-dim vector (~200ms on CPU)
        2. Compute cosine similarity between query vector and all corpus vectors
        3. Return top-k results sorted by descending similarity score

        Cosine similarity: measures the angle between two vectors.
        Score range: -1 (opposite meaning) to 1 (identical meaning).
        Typical scores: >0.5 = strong match, 0.2-0.5 = related, <0.2 = weak.

        Args:
            query: Natural language search query
            top_k: Number of results to return (1-10)

        Returns:
            List of dicts with keys: term, definition, score
        """
        # Encode the query (single string → 384-dim vector)
        query_embedding = self.model.encode(query, convert_to_numpy=True)

        # Cosine similarity = dot product / (norm of A × norm of B)
        # Computed for all corpus vectors simultaneously using numpy broadcasting.
        # This is O(n) in the corpus size — fast for 15 items, acceptable for ~10k.
        scores = np.dot(self.embeddings, query_embedding) / (
            np.linalg.norm(self.embeddings, axis=1) * np.linalg.norm(query_embedding)
        )

        # argsort returns indices sorted ascending; [::-1] reverses to descending.
        # [:top_k] takes only the top results.
        top_indices = np.argsort(scores)[::-1][:top_k]

        return [
            {
                "term": self.corpus[i]["term"],
                "definition": self.corpus[i]["definition"],
                "score": float(scores[i]),
            }
            for i in top_indices
        ]
