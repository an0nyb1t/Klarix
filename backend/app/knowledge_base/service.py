"""
Knowledge base service.

Coordinates chunking, embedding, and storage for a repository's extracted data.
Supports checkpointing so interrupted builds can resume without re-embedding
already-stored chunks.

Called by the ingester after data extraction completes.
"""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.ingester.schemas import ExtractedData
from app.knowledge_base import embedding_service, store
from app.knowledge_base.chunkers import (
    chunk_code_file,
    chunk_commit,
    chunk_issue,
    chunk_media_file,
    chunk_pull_request,
    chunk_repo_overview,
)
from app.knowledge_base.schemas import Chunk
from checkpoint import CheckpointManager

logger = logging.getLogger(__name__)

EMBED_BATCH_SIZE = 100


async def build_knowledge_base(
    repo_id: str,
    data: ExtractedData,
    db: AsyncSession,
) -> None:
    """
    Build the knowledge base for a repository from scratch.

    Chunks all data types, generates embeddings in batches of 100,
    and saves a checkpoint after each batch so the process can be resumed.
    """
    cp_mgr = CheckpointManager(db)
    checkpoint = await cp_mgr.load(repo_id, "kb_build")

    chunks_stored_so_far = 0
    last_batch_index = -1

    if checkpoint and checkpoint.state:
        chunks_stored_so_far = checkpoint.state.get("chunks_stored", 0)
        last_batch_index = checkpoint.state.get("last_batch_index", -1)
        logger.info(
            "Resuming KB build for %s — %d chunks already stored, resuming at batch %d",
            repo_id, chunks_stored_so_far, last_batch_index + 1,
        )

    # ── Build all chunks ──────────────────────────────────────────────────────
    all_chunks = _build_all_chunks(repo_id, data)
    total_chunks = len(all_chunks)
    logger.info("Total chunks for %s: %d", repo_id, total_chunks)

    # ── Ensure collection exists ──────────────────────────────────────────────
    await asyncio.to_thread(store.create_collection, repo_id)

    # Skip already-stored chunks
    if last_batch_index >= 0:
        # Get IDs already in ChromaDB to avoid re-embedding
        stored_ids = await asyncio.to_thread(store.get_stored_ids, repo_id)
    else:
        stored_ids = set()

    # ── Embed and store in batches ────────────────────────────────────────────
    batches = _make_batches(all_chunks, EMBED_BATCH_SIZE)

    for batch_index, batch in enumerate(batches):
        # Skip batches already processed
        if batch_index <= last_batch_index:
            continue

        # Skip individual chunks already in store (handles partial batches)
        batch = [c for c in batch if c.id not in stored_ids]
        if not batch:
            continue

        texts = [c.text for c in batch]
        ids = [c.id for c in batch]
        metadatas = [c.metadata for c in batch]

        # Embed synchronously inside thread
        embeddings = await asyncio.to_thread(embedding_service.embed_texts, texts)

        # Store in ChromaDB
        await asyncio.to_thread(
            store.add_documents, repo_id, ids, texts, embeddings, metadatas
        )

        chunks_stored_so_far += len(batch)
        logger.debug(
            "KB build %s: batch %d stored, total %d/%d",
            repo_id, batch_index, chunks_stored_so_far, total_chunks,
        )

        # Save checkpoint after each batch
        await cp_mgr.save(
            repo_id=repo_id,
            operation="kb_build",
            stage="embedding_chunks",
            progress_current=chunks_stored_so_far,
            progress_total=total_chunks,
            state={
                "total_chunks": total_chunks,
                "chunks_stored": chunks_stored_so_far,
                "last_batch_index": batch_index,
            },
        )
        await db.commit()

    # ── Done ─────────────────────────────────────────────────────────────────
    await cp_mgr.clear(repo_id, "kb_build")
    await db.commit()

    final_count = await asyncio.to_thread(store.collection_count, repo_id)
    logger.info(
        "KB build complete for %s — %d chunks in collection.",
        repo_id, final_count,
    )


async def update_knowledge_base(
    repo_id: str,
    new_data: ExtractedData,
    db: AsyncSession,
) -> None:
    """
    Incremental update — add new chunks without rebuilding everything.
    Uses upsert so unchanged chunks are overwritten with same content.
    Supports checkpoint resume for interrupted updates.
    """
    cp_mgr = CheckpointManager(db)
    checkpoint = await cp_mgr.load(repo_id, "kb_update")

    last_batch_index = -1
    chunks_added = 0

    if checkpoint and checkpoint.state:
        last_batch_index = checkpoint.state.get("last_batch_index", -1)
        chunks_added = checkpoint.state.get("chunks_stored", 0)
        logger.info(
            "Resuming KB update for %s — %d chunks already added, resuming at batch %d",
            repo_id, chunks_added, last_batch_index + 1,
        )

    all_chunks = _build_all_chunks(repo_id, new_data)
    stored_ids = await asyncio.to_thread(store.get_stored_ids, repo_id)

    # Only process chunks not yet stored
    new_chunks = [c for c in all_chunks if c.id not in stored_ids]
    if not new_chunks:
        logger.info("No new chunks to add for %s.", repo_id)
        await cp_mgr.clear(repo_id, "kb_update")
        await db.commit()
        return

    logger.info("Adding %d new chunks to KB for %s.", len(new_chunks), repo_id)

    batches = _make_batches(new_chunks, EMBED_BATCH_SIZE)

    for batch_index, batch in enumerate(batches):
        if batch_index <= last_batch_index:
            continue

        texts = [c.text for c in batch]
        ids = [c.id for c in batch]
        metadatas = [c.metadata for c in batch]

        embeddings = await asyncio.to_thread(embedding_service.embed_texts, texts)
        await asyncio.to_thread(
            store.add_documents, repo_id, ids, texts, embeddings, metadatas
        )

        chunks_added += len(batch)

        await cp_mgr.save(
            repo_id=repo_id,
            operation="kb_update",
            stage="embedding_chunks",
            progress_current=chunks_added,
            progress_total=len(new_chunks),
            state={
                "total_chunks": len(new_chunks),
                "chunks_stored": chunks_added,
                "last_batch_index": batch_index,
            },
        )
        await db.commit()

    await cp_mgr.clear(repo_id, "kb_update")
    await db.commit()
    logger.info("KB update complete for %s — %d new chunks added.", repo_id, chunks_added)


def _build_all_chunks(repo_id: str, data: ExtractedData) -> list[Chunk]:
    """Build the complete list of chunks from extracted data."""
    chunks: list[Chunk] = []

    # Code + media files
    for f in data.files:
        if f.is_media_ref:
            chunks.extend(chunk_media_file(f, repo_id))
        else:
            chunks.extend(chunk_code_file(f, repo_id))

    # Commits
    for commit in data.commits:
        chunks.extend(chunk_commit(commit, repo_id))

    # Issues
    for issue in data.issues:
        chunks.extend(chunk_issue(issue, repo_id))

    # Pull requests
    for pr in data.pull_requests:
        chunks.extend(chunk_pull_request(pr, repo_id))

    # Repo overview (always last)
    total_commits = len(data.commits)
    total_files = len(data.files)
    chunks.append(chunk_repo_overview(
        repo_id=repo_id,
        repo_name=data.repo_name,
        metadata=data.metadata,
        total_commits=total_commits,
        total_files=total_files,
    ))

    return chunks


def _make_batches(chunks: list[Chunk], batch_size: int) -> list[list[Chunk]]:
    """Split chunks into batches of batch_size."""
    return [chunks[i:i + batch_size] for i in range(0, len(chunks), batch_size)]
