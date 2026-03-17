"""
Embedding service — wraps sentence-transformers.

The model is loaded once (singleton) and reused for all embedding calls.
All public functions run synchronously; callers must use asyncio.to_thread().
"""

import logging
from typing import Any

from config import settings

logger = logging.getLogger(__name__)

_model: Any = None  # Lazy-loaded singleton


def _get_model():
    """Load the embedding model once and cache it."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading embedding model: %s", settings.embedding_model)
        _model = SentenceTransformer(settings.embedding_model)
        logger.info("Embedding model loaded.")
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of texts. Returns a list of float vectors.
    Processes in batches of 100 for memory efficiency.
    Must be called inside asyncio.to_thread().
    """
    model = _get_model()
    results = []
    batch_size = 100

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        embeddings = model.encode(batch, convert_to_numpy=True, show_progress_bar=False)
        results.extend(embeddings.tolist())

    return results


def embed_query(query: str) -> list[float]:
    """
    Embed a single query string.
    Must be called inside asyncio.to_thread().
    """
    model = _get_model()
    embedding = model.encode([query], convert_to_numpy=True, show_progress_bar=False)
    return embedding[0].tolist()
