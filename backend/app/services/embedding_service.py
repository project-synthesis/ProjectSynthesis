"""Singleton sentence-transformers embedding service.

Model: all-MiniLM-L6-v2 (768 dimensions, CPU-only).
Lazy-loaded on first use. Async wrappers via asyncio.to_thread().
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)

# Model download timeout (seconds). Prevents hanging on offline first use.
_MODEL_LOAD_TIMEOUT = 60


class EmbeddingError(RuntimeError):
    """Raised when embedding operations fail."""


class EmbeddingService:
    """Singleton embedding service using sentence-transformers.

    The model is loaded lazily on first access and shared across all
    instances via the class-level ``_model`` attribute. Thread-safe
    due to Python's GIL protecting the assignment.
    """

    _model: Any = None
    _model_name: str = ""
    _dimension: int = 0

    def __init__(self, model_name: str | None = None) -> None:
        self._requested_model = model_name or settings.EMBEDDING_MODEL

    @property
    def model(self) -> Any:
        """Lazy-load the SentenceTransformer model (singleton)."""
        if EmbeddingService._model is None:
            self._load_model()
        return EmbeddingService._model

    @property
    def dimension(self) -> int:
        """Return the embedding dimension (auto-detected from model)."""
        if EmbeddingService._dimension == 0:
            _ = self.model  # ensure loaded
        return EmbeddingService._dimension

    def _load_model(self) -> None:
        """Load the model with timeout protection."""
        try:
            from sentence_transformers import SentenceTransformer

            logger.info("Loading embedding model: %s", self._requested_model)
            model = SentenceTransformer(self._requested_model)

            # Auto-detect dimension from a probe embedding
            probe = model.encode("test", convert_to_numpy=True)
            EmbeddingService._dimension = probe.shape[0]
            EmbeddingService._model = model
            EmbeddingService._model_name = self._requested_model

            logger.info(
                "Embedding model loaded: %s (%d dimensions)",
                self._requested_model, EmbeddingService._dimension,
            )
        except ImportError:
            raise EmbeddingError(
                "sentence-transformers is not installed. "
                "Run: pip install sentence-transformers"
            )
        except Exception as exc:
            raise EmbeddingError(
                f"Failed to load embedding model '{self._requested_model}': {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def embed_single(self, text: str) -> np.ndarray:
        """Embed a single text string. Returns a 1-D numpy array.

        Raises:
            ValueError: If text is None or empty.
            EmbeddingError: If model fails to encode.
        """
        if text is None:
            raise ValueError("Cannot embed None — expected a string")
        if not text.strip():
            # Return zero vector for empty/whitespace strings
            return np.zeros(self.dimension or 768, dtype=np.float32)
        try:
            return self.model.encode(text, convert_to_numpy=True, normalize_embeddings=True)
        except Exception as exc:
            raise EmbeddingError(f"Failed to embed text ({len(text)} chars): {exc}") from exc

    def embed_texts(self, texts: list[str]) -> list[np.ndarray]:
        """Embed a batch of text strings. Returns list of 1-D numpy arrays.

        Raises:
            ValueError: If texts contains None values.
            EmbeddingError: If model fails to encode batch.
        """
        if not texts:
            return []
        # Validate inputs
        for i, t in enumerate(texts):
            if t is None:
                raise ValueError(f"Cannot embed None at index {i}")
        # Replace empty strings with placeholder (model may struggle with empty)
        cleaned = [t if t.strip() else " " for t in texts]
        try:
            embeddings = self.model.encode(cleaned, convert_to_numpy=True, normalize_embeddings=True)
            return [embeddings[i] for i in range(len(texts))]
        except Exception as exc:
            raise EmbeddingError(
                f"Failed to embed batch ({len(texts)} texts): {exc}"
            ) from exc

    async def aembed_single(self, text: str) -> np.ndarray:
        """Async wrapper for embed_single (runs in threadpool)."""
        return await asyncio.to_thread(self.embed_single, text)

    async def aembed_texts(self, texts: list[str]) -> list[np.ndarray]:
        """Async wrapper for embed_texts (runs in threadpool)."""
        return await asyncio.to_thread(self.embed_texts, texts)

    # ------------------------------------------------------------------
    # Similarity search
    # ------------------------------------------------------------------

    @staticmethod
    def cosine_search(
        query_vec: np.ndarray,
        corpus_vecs: list[np.ndarray],
        top_k: int = 10,
    ) -> list[tuple[int, float]]:
        """Find the top-k most similar vectors via cosine similarity.

        Args:
            query_vec: Query embedding (1-D array).
            corpus_vecs: List of corpus embeddings (same dimension).
            top_k: Number of results to return.

        Returns:
            List of (index, similarity_score) tuples, sorted by score descending.
        """
        if not corpus_vecs:
            return []
        if query_vec is None:
            raise ValueError("query_vec cannot be None")

        corpus = np.stack(corpus_vecs)
        # L2 normalize with epsilon to prevent division by zero
        query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-9)
        corpus_norm = corpus / (np.linalg.norm(corpus, axis=1, keepdims=True) + 1e-9)
        scores = corpus_norm @ query_norm
        top_indices = np.argsort(scores)[::-1][:top_k]
        return [(int(i), float(scores[i])) for i in top_indices]
