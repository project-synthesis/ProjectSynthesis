"""Singleton sentence-transformers cross-encoder reranker service.

Used for high-precision reranking to catch compositional nuances
(negation, binding, scope) that bi-encoders miss.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RankedDocument:
    """A scored and ranked document resulting from the reranker."""
    index: int
    document: str
    score: float


class RerankerError(RuntimeError):
    """Raised when cross-encoder operations fail."""


class RerankerService:
    """Singleton cross-encoder service using sentence-transformers."""

    _model: Any = None
    _model_name: str = ""

    def __init__(self, model_name: str | None = None) -> None:
        # Default to a fast, accurate cross-encoder if none provided
        self._requested_model = model_name or "cross-encoder/stsb-distilroberta-base"

    @property
    def model(self) -> Any:
        """Lazy-load the CrossEncoder model (singleton)."""
        if RerankerService._model is None:
            self._load_model()
        return RerankerService._model

    def _load_model(self) -> None:
        """Load the model safely."""
        try:
            from sentence_transformers import CrossEncoder

            logger.info("Loading reranker model: %s", self._requested_model)
            model = CrossEncoder(self._requested_model)

            RerankerService._model = model
            RerankerService._model_name = self._requested_model

            logger.info("Reranker model loaded: %s", self._requested_model)
        except ImportError:
            raise RerankerError(
                "sentence-transformers is not installed. "
                "Run: pip install sentence-transformers"
            )
        except Exception as exc:
            raise RerankerError(
                f"Failed to load reranker model '{self._requested_model}': {exc}"
            ) from exc

    def score_batch(self, query: str, documents: list[str]) -> list[float]:
        """Score a batch of documents against a query.

        Args:
            query: The target text to match against.
            documents: A list of candidate strings.

        Returns:
            A list of floats representing the logits/scores.
        """
        if not documents:
            return []

        # pairs = [(query, doc1), (query, doc2), ...]
        pairs = [(query, doc) for doc in documents]

        try:
            scores = self.model.predict(pairs)
            # if single doc, predict returns a float instead of list/array
            if not isinstance(scores, (list, tuple)) and not hasattr(scores, 'shape'):
                return [float(scores)]
            return [float(s) for s in scores]
        except Exception as exc:
            raise RerankerError(f"Failed to score batch: {exc}") from exc

    def rerank(self, query: str, documents: list[str], top_k: int | None = None) -> list[RankedDocument]:
        """Sort a list of documents by their cross-encoder score.

        Args:
            query: The target text.
            documents: Candidate texts to rerank.
            top_k: Optional limit on the number of results to return.

        Returns:
            A sorted list of RankedDocument objects (highest score first).
        """
        if not documents:
            return []

        scores = self.score_batch(query, documents)

        results = [
            RankedDocument(index=i, document=doc, score=score)
            for i, (doc, score) in enumerate(zip(documents, scores))
        ]

        results.sort(key=lambda x: x.score, reverse=True)

        if top_k is not None:
            results = results[:top_k]

        return results
