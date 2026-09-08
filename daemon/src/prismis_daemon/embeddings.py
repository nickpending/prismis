"""Embedding generation for semantic search."""

import time
from typing import List

from sentence_transformers import SentenceTransformer

from .observability import log as obs_log


class Embedder:
    """Generate embeddings for semantic search using sentence-transformers.

    Uses all-MiniLM-L6-v2 model (384 dimensions) for local, offline embeddings.
    Model is cached after first load (~25MB).
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        """Initialize embedder with specified model.

        Args:
            model_name: HuggingFace model name. Default: all-MiniLM-L6-v2
                       (384 dimensions, fast, good semantic matching)
        """
        self.model_name = model_name
        self._model = None

    @property
    def model(self) -> SentenceTransformer:
        """Lazy-load model on first use."""
        if self._model is None:
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def generate_embedding(self, text: str, title: str = "") -> List[float]:
        """Generate embedding vector for text.

        Args:
            text: Content text to embed (article content, summary, etc.)
            title: Optional title to prepend for better context

        Returns:
            List of floats (384 dimensions for all-MiniLM-L6-v2)
        """
        start_time = time.time()

        try:
            # Combine title and text for better semantic representation
            if title:
                combined = f"{title}. {text}"
            else:
                combined = text

            # Truncate if too long (model has token limits)
            if len(combined) > 5000:
                combined = combined[:5000]

            # Generate embedding
            embedding = self.model.encode(combined, convert_to_numpy=True)

            # Convert to list for JSON serialization
            vector = embedding.tolist()
        except Exception as e:
            obs_log(
                "embedding.generate",
                model=self.model_name,
                duration_ms=int((time.time() - start_time) * 1000),
                status="error",
                error=str(e),
            )
            # Re-raised so callers keep the behavior they already have: every caller in
            # the orchestrator wraps this in its own try/except that logs and continues.
            raise

        obs_log(
            "embedding.generate",
            model=self.model_name,
            dimension=len(vector),
            duration_ms=int((time.time() - start_time) * 1000),
            status="success",
        )
        return vector

    def get_dimension(self) -> int:
        """Get embedding dimension for this model.

        Returns:
            Embedding dimension (384 for all-MiniLM-L6-v2)
        """
        dimension = self.model.get_sentence_embedding_dimension()
        if dimension is None:
            raise RuntimeError(
                f"Model {self.model} did not report an embedding dimension"
            )
        return dimension
