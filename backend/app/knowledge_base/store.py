"""
ChromaDB wrapper — one collection per repository.

Collections are named `repo_<repo_id>`.
Storage is persistent in data/chromadb/.
All public functions are synchronous — callers must use asyncio.to_thread().
"""

import logging
import os
from typing import Any

from config import settings

logger = logging.getLogger(__name__)

_client: Any = None  # Lazy-loaded singleton


def _get_client():
    """Return the shared ChromaDB client, creating it on first call."""
    global _client
    if _client is None:
        import chromadb

        chroma_path = os.path.join(settings.data_dir, "chromadb")
        os.makedirs(chroma_path, exist_ok=True)
        logger.info("Initializing ChromaDB at %s", chroma_path)
        _client = chromadb.PersistentClient(path=chroma_path)
    return _client


def _collection_name(repo_id: str) -> str:
    return f"repo_{repo_id.replace('-', '_')}"


def create_collection(repo_id: str) -> None:
    """Create the ChromaDB collection for this repo. No-op if already exists."""
    client = _get_client()
    name = _collection_name(repo_id)
    client.get_or_create_collection(name=name)
    logger.debug("Collection ready: %s", name)


def delete_collection(repo_id: str) -> None:
    """Drop the ChromaDB collection for this repo."""
    client = _get_client()
    name = _collection_name(repo_id)
    try:
        client.delete_collection(name=name)
        logger.info("Deleted collection: %s", name)
    except Exception as e:
        logger.warning("Could not delete collection %s: %s", name, e)


def add_documents(
    repo_id: str,
    ids: list[str],
    texts: list[str],
    embeddings: list[list[float]],
    metadatas: list[dict],
) -> None:
    """
    Add chunks to the ChromaDB collection.
    Existing IDs are upserted (updated if changed).
    """
    if not ids:
        return

    client = _get_client()
    collection = client.get_or_create_collection(_collection_name(repo_id))

    # ChromaDB metadata values must be str, int, float, or bool — sanitize
    sanitized = []
    for m in metadatas:
        clean = {}
        for k, v in m.items():
            if isinstance(v, (str, int, float, bool)):
                clean[k] = v
            else:
                clean[k] = str(v)
        sanitized.append(clean)

    collection.upsert(
        ids=ids,
        documents=texts,
        embeddings=embeddings,
        metadatas=sanitized,
    )


def query(
    repo_id: str,
    query_embedding: list[float],
    n_results: int = 10,
    where: dict | None = None,
) -> list[dict]:
    """
    Semantic search within the repo's collection.

    Returns a list of dicts with keys: id, document, metadata, distance.
    Distance is ChromaDB's L2 distance — lower = more similar.
    """
    client = _get_client()
    name = _collection_name(repo_id)

    try:
        collection = client.get_collection(name=name)
    except Exception:
        logger.warning("Collection not found for repo %s", repo_id)
        return []

    kwargs: dict = {
        "query_embeddings": [query_embedding],
        "n_results": n_results,
        "include": ["documents", "metadatas", "distances"],
    }
    if where:
        kwargs["where"] = where

    try:
        results = collection.query(**kwargs)
    except Exception as e:
        logger.error("ChromaDB query failed: %s", e)
        return []

    items = []
    ids = results.get("ids", [[]])[0]
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]

    for chunk_id, doc, meta, dist in zip(ids, docs, metas, dists):
        items.append({
            "id": chunk_id,
            "document": doc,
            "metadata": meta,
            "distance": dist,
        })

    return items


def get_stored_ids(repo_id: str) -> set[str]:
    """Return the set of chunk IDs already stored in this repo's collection."""
    client = _get_client()
    name = _collection_name(repo_id)
    try:
        collection = client.get_collection(name=name)
        result = collection.get(include=[])  # IDs only
        return set(result.get("ids", []))
    except Exception:
        return set()


def collection_count(repo_id: str) -> int:
    """Return the number of documents stored in the repo's collection."""
    client = _get_client()
    name = _collection_name(repo_id)
    try:
        collection = client.get_collection(name=name)
        return collection.count()
    except Exception:
        return 0
