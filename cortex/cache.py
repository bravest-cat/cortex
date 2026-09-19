"""
Content-Hash Incremental Ingestion Cache for Cortex.
Tracks file paths, mtimes, sha256 checksums, and Qdrant point IDs using SQLite.
Allows sub-second incremental indexing by skipping unchanged files and purging
stale vectors when files are modified or deleted.
"""
import hashlib
import json
import os
import sqlite3
import struct
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


DEFAULT_CACHE_DIR = Path.home() / ".cortex"
DEFAULT_CACHE_FILE = DEFAULT_CACHE_DIR / "index_cache.db"
CACHE_DB_PATH = Path(os.getenv("CORTEX_CACHE_DB", str(DEFAULT_CACHE_FILE)))


class IngestCache:
    """Manages file hashes and point IDs for incremental indexing."""

    def __init__(self, db_path: Path = CACHE_DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS file_cache (
                    file_path TEXT NOT NULL,
                    collection TEXT NOT NULL,
                    mtime REAL NOT NULL,
                    sha256 TEXT NOT NULL,
                    point_ids TEXT NOT NULL, -- JSON list of node IDs
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (file_path, collection)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cache_collection ON file_cache (collection)"
            )
            conn.commit()

    @staticmethod
    def compute_sha256(path: Path) -> str:
        """Compute SHA-256 checksum of a file in 64KB blocks."""
        hasher = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception:
            return ""

    @staticmethod
    def get_file_stats(path: Path) -> Tuple[float, str]:
        """Return (mtime, sha256) for a given file path."""
        try:
            if not path.exists():
                return 0.0, ""
            return path.stat().st_mtime, IngestCache.compute_sha256(path)
        except OSError:
            return 0.0, ""

    def is_file_changed(
        self, file_path: str, collection: str
    ) -> Tuple[bool, float, str, List[str]]:
        """
        Check if a file has changed compared to cached state.
        Returns:
            (changed: bool, current_mtime: float, current_sha256: str, old_point_ids: List[str])
        """
        p = Path(file_path)
        if not p.exists():
            return False, 0.0, "", []

        try:
            current_mtime = p.stat().st_mtime
        except OSError:
            return True, 0.0, "", []

        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT mtime, sha256, point_ids FROM file_cache WHERE file_path = ? AND collection = ?",
                (str(p.resolve()), collection),
            ).fetchone()

        if not row:
            # Not in cache -> changed / new
            current_sha = self.compute_sha256(p)
            return True, current_mtime, current_sha, []

        cached_mtime = row["mtime"]
        cached_sha = row["sha256"]
        try:
            old_point_ids = json.loads(row["point_ids"])
        except Exception:
            old_point_ids = []

        # Fast path: mtime identical
        if abs(current_mtime - cached_mtime) < 1e-4:
            return False, current_mtime, cached_sha, old_point_ids

        # Slow path: mtime changed -> compute sha256 to check if content actually changed
        current_sha = self.compute_sha256(p)
        if current_sha == cached_sha:
            # Content identical despite touched mtime: update cached mtime and return unchanged
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE file_cache SET mtime = ?, updated_at = ? WHERE file_path = ? AND collection = ?",
                    (current_mtime, time.time(), str(p.resolve()), collection),
                )
                conn.commit()
            return False, current_mtime, current_sha, old_point_ids

        # File content truly changed
        return True, current_mtime, current_sha, old_point_ids

    def record_file(
        self,
        file_path: str,
        collection: str,
        mtime: float,
        sha256: str,
        point_ids: List[str],
    ):
        """Record or update a file's state in the cache."""
        resolved = str(Path(file_path).resolve())
        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO file_cache (file_path, collection, mtime, sha256, point_ids, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path, collection) DO UPDATE SET
                    mtime = excluded.mtime,
                    sha256 = excluded.sha256,
                    point_ids = excluded.point_ids,
                    updated_at = excluded.updated_at
                """,
                (resolved, collection, mtime, sha256, json.dumps(point_ids), now),
            )
            conn.commit()

    def remove_file(self, file_path: str, collection: str) -> List[str]:
        """Remove a file from the cache and return its old point IDs for deletion."""
        resolved = str(Path(file_path).resolve())
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT point_ids FROM file_cache WHERE file_path = ? AND collection = ?",
                (resolved, collection),
            ).fetchone()
            if not row:
                return []
            try:
                point_ids = json.loads(row["point_ids"])
            except Exception:
                point_ids = []
            conn.execute(
                "DELETE FROM file_cache WHERE file_path = ? AND collection = ?",
                (resolved, collection),
            )
            conn.commit()
            return point_ids

    def get_all_cached_files(self, collection: str) -> Dict[str, List[str]]:
        """Return dict of {file_path: point_ids} for all files in a collection."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT file_path, point_ids FROM file_cache WHERE collection = ?",
                (collection,),
            ).fetchall()
            out = {}
            for r in rows:
                try:
                    out[r["file_path"]] = json.loads(r["point_ids"])
                except Exception:
                    out[r["file_path"]] = []
            return out

    def purge_missing_files(
        self, collection: str, current_files: Set[str]
    ) -> List[Tuple[str, List[str]]]:
        """
        Compare collection cache against current existing files on disk.
        Purges missing files from cache and returns list of (file_path, point_ids) to delete from vector store.
        """
        resolved_current = {str(Path(f).resolve()) for f in current_files}
        cached_files = self.get_all_cached_files(collection)
        purged = []
        with self._get_connection() as conn:
            for file_path, point_ids in cached_files.items():
                if file_path not in resolved_current and not Path(file_path).exists():
                    conn.execute(
                        "DELETE FROM file_cache WHERE file_path = ? AND collection = ?",
                        (file_path, collection),
                    )
                    purged.append((file_path, point_ids))
            conn.commit()
        return purged

    def clear_collection(self, collection: str):
        """Clear cache entries for a specific collection."""
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM file_cache WHERE collection = ?",
                (collection,),
            )
            conn.commit()


class ChunkEmbeddingCache:
    """
    High-performance SQLite cache mapping SHA-256(chunk_raw_text) -> dense vector.
    Uses struct.pack for ultra-compact, sub-millisecond serialization of 4096-dim float32 vectors.
    Dramatically accelerates re-indexing by skipping oMLX GPU embedding calls for unchanged chunks.
    """

    def __init__(self, db_path: Path = CACHE_DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunk_embedding_cache (
                    chunk_sha256 TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    dim INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (chunk_sha256, model_name)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunk_cache_model ON chunk_embedding_cache (model_name)"
            )
            conn.commit()

    @staticmethod
    def compute_chunk_sha(text: str) -> str:
        """Computes SHA-256 hex digest of normalized chunk text."""
        return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()

    def get_embedding(self, text: str, model_name: str) -> Optional[List[float]]:
        """Look up embedding for a single text."""
        sha = self.compute_chunk_sha(text)
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT embedding, dim FROM chunk_embedding_cache WHERE chunk_sha256 = ? AND model_name = ?",
                (sha, model_name),
            ).fetchone()
            if not row:
                return None
            blob = row["embedding"]
            dim = row["dim"]
            return list(struct.unpack(f"{dim}f", blob))

    def get_batch(
        self, texts: List[str], model_name: str
    ) -> Tuple[Dict[int, List[float]], List[Tuple[int, str]]]:
        """
        Batch lookup for chunk texts.
        Returns:
            hits:   {original_index: embedding_vector}
            misses: [(original_index, text)]
        """
        hits: Dict[int, List[float]] = {}
        misses: List[Tuple[int, str]] = []

        if not texts:
            return hits, misses

        # Map sha -> list of (idx, text) in case of identical chunks in same batch
        sha_to_indices: Dict[str, List[int]] = {}
        for idx, text in enumerate(texts):
            sha = self.compute_chunk_sha(text)
            sha_to_indices.setdefault(sha, []).append(idx)

        shas = list(sha_to_indices.keys())
        # SQLite query in chunks of 500 parameters
        found_shas = set()
        with self._get_connection() as conn:
            for i in range(0, len(shas), 500):
                batch_shas = shas[i:i + 500]
                placeholders = ",".join("?" * len(batch_shas))
                params = list(batch_shas) + [model_name]
                rows = conn.execute(
                    f"SELECT chunk_sha256, embedding, dim FROM chunk_embedding_cache WHERE chunk_sha256 IN ({placeholders}) AND model_name = ?",
                    params,
                ).fetchall()
                for row in rows:
                    sha = row["chunk_sha256"]
                    blob = row["embedding"]
                    dim = row["dim"]
                    vec = list(struct.unpack(f"{dim}f", blob))
                    found_shas.add(sha)
                    for orig_idx in sha_to_indices[sha]:
                        hits[orig_idx] = vec

        # Gather misses
        for sha, indices in sha_to_indices.items():
            if sha not in found_shas:
                for orig_idx in indices:
                    misses.append((orig_idx, texts[orig_idx]))

        # Sort misses by index
        misses.sort(key=lambda x: x[0])
        return hits, misses

    def put_batch(self, items: List[Tuple[str, List[float]]], model_name: str):
        """
        Stores newly computed embeddings in the cache.
        items: List of (chunk_text, vector)
        """
        if not items:
            return
        now = time.time()
        records = []
        for text, vec in items:
            sha = self.compute_chunk_sha(text)
            dim = len(vec)
            blob = struct.pack(f"{dim}f", *vec)
            records.append((sha, model_name, blob, dim, now))

        with self._get_connection() as conn:
            conn.executemany(
                """
                INSERT INTO chunk_embedding_cache (chunk_sha256, model_name, embedding, dim, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chunk_sha256, model_name) DO NOTHING
                """,
                records,
            )
            conn.commit()

    def get_stats(self) -> Dict[str, Any]:
        """Returns cache stats."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT count(*), sum(length(embedding)) FROM chunk_embedding_cache").fetchone()
            count = row[0] or 0
            size_bytes = row[1] or 0
            return {
                "total_cached_chunks": count,
                "storage_size_bytes": size_bytes,
                "storage_size_mb": round(size_bytes / (1024 * 1024), 2),
            }

    def clear(self) -> int:
        """Purges all cached chunk embeddings and returns the number of deleted records."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT count(*) FROM chunk_embedding_cache").fetchone()
            count = int(row[0] or 0)
            conn.execute("DELETE FROM chunk_embedding_cache")
            conn.commit()
            return count

