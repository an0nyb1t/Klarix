"""
Retrieval interface — used by the chat module to find relevant context.

retrieve() is the main entry point. It embeds the query, searches ChromaDB,
and returns ranked chunks with similarity scores.
"""

import asyncio
import logging

from app.knowledge_base import embedding_service, store
from app.knowledge_base.schemas import RetrievedChunk

logger = logging.getLogger(__name__)

# ChromaDB uses L2 distance. Convert to a 0-1 similarity score.
# Typical L2 distances for MiniLM are in the 0-2 range.
_MAX_DISTANCE = 2.0


def _distance_to_score(distance: float) -> float:
    """Convert L2 distance to a 0-1 similarity score (1 = identical)."""
    return max(0.0, 1.0 - (distance / _MAX_DISTANCE))


async def retrieve(
    repo_id: str,
    query: str,
    n_results: int = 10,
    content_types: list[str] | None = None,
) -> list[RetrievedChunk]:
    """
    Find the most relevant chunks for a query.

    Args:
        repo_id: The repository to search in.
        query: Natural language query from the user.
        n_results: Number of chunks to return.
        content_types: Optional filter — e.g. ["code", "commit"].
                       If None, search all types.

    Returns:
        List of RetrievedChunk sorted by similarity (highest first).
    """
    # Embed the query
    query_embedding = await asyncio.to_thread(embedding_service.embed_query, query)

    # Build ChromaDB where filter if content_types specified
    where = None
    if content_types and len(content_types) == 1:
        where = {"type": content_types[0]}
    elif content_types and len(content_types) > 1:
        where = {"type": {"$in": content_types}}

    # Query ChromaDB
    raw_results = await asyncio.to_thread(
        store.query, repo_id, query_embedding, n_results, where
    )

    chunks = []
    for r in raw_results:
        score = _distance_to_score(r["distance"])
        chunks.append(RetrievedChunk(
            chunk_id=r["id"],
            content=r["document"],
            metadata=r["metadata"],
            similarity_score=score,
        ))

    # Sort by score descending (should already be sorted by ChromaDB, but be explicit)
    chunks.sort(key=lambda c: c.similarity_score, reverse=True)
    return chunks
