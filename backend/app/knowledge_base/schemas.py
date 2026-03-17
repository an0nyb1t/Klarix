"""
Knowledge base data models.

Chunk: a unit of text with metadata, ready for embedding.
RetrievedChunk: a chunk returned from a similarity search.
"""

from dataclasses import dataclass


@dataclass
class Chunk:
    id: str          # Unique ID: e.g. "code_src/utils.py_1_45", "commit_abc1234"
    text: str        # The text to embed and store
    metadata: dict   # Typed metadata for filtering (type, repo_id, file_path, etc.)


@dataclass
class RetrievedChunk:
    chunk_id: str
    content: str
    metadata: dict
    similarity_score: float
