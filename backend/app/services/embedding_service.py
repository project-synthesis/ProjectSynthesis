"""Singleton sentence-transformers embedding service."""

import logging

import numpy as np

logger = logging.getLogger(__name__)


class EmbeddingService:
    _model = None

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self._model_name = model_name

    @property
    def model(self):
        if EmbeddingService._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("Loading embedding model: %s", self._model_name)
            EmbeddingService._model = SentenceTransformer(self._model_name)
        return EmbeddingService._model

    def embed_single(self, text: str) -> np.ndarray:
        return self.model.encode(text, convert_to_numpy=True)

    def embed_texts(self, texts: list[str]) -> list[np.ndarray]:
        if not texts:
            return []
        embeddings = self.model.encode(texts, convert_to_numpy=True)
        return [embeddings[i] for i in range(len(texts))]

    @staticmethod
    def cosine_search(
        query_vec: np.ndarray,
        corpus_vecs: list[np.ndarray],
        top_k: int = 10,
    ) -> list[tuple[int, float]]:
        if not corpus_vecs:
            return []
        corpus = np.stack(corpus_vecs)
        query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-9)
        corpus_norm = corpus / (np.linalg.norm(corpus, axis=1, keepdims=True) + 1e-9)
        scores = corpus_norm @ query_norm
        top_indices = np.argsort(scores)[::-1][:top_k]
        return [(int(i), float(scores[i])) for i in top_indices]
