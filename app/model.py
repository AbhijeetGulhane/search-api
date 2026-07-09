import json
import os
from pathlib import Path
from sentence_transformers import SentenceTransformer
import numpy as np


CORPUS_PATH = Path(__file__).parent.parent / "data" / "corpus.json"
MODEL_NAME = os.getenv("MODEL_NAME", "all-MiniLM-L6-v2")


class SearchModel:
    def __init__(self):
        print(f"[SearchModel] Loading model: {MODEL_NAME}")
        self.model = SentenceTransformer(MODEL_NAME)
        self.corpus = self._load_corpus()
        self.embeddings = self._embed_corpus()
        print(f"[SearchModel] Ready. {len(self.corpus)} terms indexed.")

    def _load_corpus(self):
        with open(CORPUS_PATH) as f:
            return json.load(f)

    def _embed_corpus(self):
        definitions = [item["definition"] for item in self.corpus]
        return self.model.encode(definitions, convert_to_numpy=True)

    def search(self, query: str, top_k: int = 3):
        query_embedding = self.model.encode(query, convert_to_numpy=True)
        scores = np.dot(self.embeddings, query_embedding) / (
            np.linalg.norm(self.embeddings, axis=1) * np.linalg.norm(query_embedding)
        )
        top_indices = np.argsort(scores)[::-1][:top_k]
        return [
            {
                "term": self.corpus[i]["term"],
                "definition": self.corpus[i]["definition"],
                "score": float(scores[i]),
            }
            for i in top_indices
        ]
