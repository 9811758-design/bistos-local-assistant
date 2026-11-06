"""Optional local semantic retrieval for manual pages.

This index is deliberately separate from the document store.  It contains only
page text embeddings and immutable document/page digests; publication and
permission checks remain in :mod:`bistos.search` for every query.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from threading import RLock
from typing import Protocol

from bistos.models import Document, Page
from bistos.service_semantic import (
    EmbeddingProvider,
    validate_vector,
)


class ManualPageIdentity(Protocol):
    document_id: str
    number: int
    text: str
    document_sha256: str


def _hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.casefold().split()).encode("utf-8")).hexdigest()


def _chunks(text: str, size: int = 1500, overlap: int = 220) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n|\r?\n", text) if part.strip()]
    if not paragraphs:
        return [text[:size]] if text else []
    result: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > size:
            if current:
                result.append(current)
                current = ""
            start = 0
            while start < len(paragraph):
                end = min(len(paragraph), start + size)
                result.append(paragraph[start:end])
                if end == len(paragraph):
                    break
                start = max(start + 1, end - overlap)
            continue
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if current and len(candidate) > size:
            result.append(current)
            current = f"{current[-overlap:]}\n\n{paragraph}"
        else:
            current = candidate
    if current:
        result.append(current)
    return result


class ManualSemanticSearch:
    """Persistent page index with digest checks and a local embedding provider."""

    def __init__(self, path: Path, provider: EmbeddingProvider, *, min_score: float = 0.64) -> None:
        self.path = path
        self.provider = provider
        self.min_score = min_score
        self._query_cache: dict[str, list[float]] = {}
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS manual_embeddings (
                    text_hash TEXT NOT NULL, model_digest TEXT NOT NULL,
                    text TEXT NOT NULL, vector TEXT NOT NULL, dimensions INTEGER NOT NULL,
                    PRIMARY KEY(text_hash, model_digest));
                CREATE TABLE IF NOT EXISTS manual_pages (
                    document_id TEXT NOT NULL, page_number INTEGER NOT NULL,
                    chunk_number INTEGER NOT NULL, document_sha256 TEXT NOT NULL,
                    page_text_hash TEXT NOT NULL, text_hash TEXT NOT NULL,
                    model_digest TEXT NOT NULL,
                    PRIMARY KEY(document_id, page_number, chunk_number));
                CREATE INDEX IF NOT EXISTS manual_pages_hash
                    ON manual_pages(text_hash, model_digest);
            """)
            columns = {str(row[1]) for row in db.execute("PRAGMA table_info(manual_pages)")}
            if "chunk_number" not in columns or "page_text_hash" not in columns:
                db.execute("DROP TABLE manual_pages")
                db.execute("""CREATE TABLE manual_pages (
                    document_id TEXT NOT NULL, page_number INTEGER NOT NULL,
                    chunk_number INTEGER NOT NULL, document_sha256 TEXT NOT NULL,
                    page_text_hash TEXT NOT NULL, text_hash TEXT NOT NULL,
                    model_digest TEXT NOT NULL,
                    PRIMARY KEY(document_id, page_number, chunk_number))""")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def rebuild(self, pages: Sequence[tuple[Document, Page]]) -> int:
        texts: dict[str, str] = {}
        rows: list[tuple[str, int, int, str, str, str]] = []
        for document, page in pages:
            text = str(page.text).strip()
            if not text:
                continue
            page_hash = _hash(text)
            for chunk_number, chunk in enumerate(_chunks(text)):
                key = _hash(chunk)
                texts.setdefault(key, chunk)
                rows.append((
                    str(document.id), int(page.number), chunk_number,
                    str(document.original_sha256), page_hash, key,
                ))
        vectors: dict[str, list[float]] = {}
        with self._connect() as db:
            for key in texts:
                found = db.execute(
                    "SELECT vector FROM manual_embeddings WHERE text_hash=? AND model_digest=?",
                    (key, self.provider.model_digest),
                ).fetchone()
                if found:
                    vectors[key] = validate_vector(json.loads(found[0]))
        pending = [(key, text) for key, text in texts.items() if key not in vectors]
        for offset in range(0, len(pending), 64):
            batch = pending[offset : offset + 64]
            embedded = self.provider.embed([text for _, text in batch])
            with self._connect() as db:
                for (key, text), vector in zip(batch, embedded, strict=True):
                    parsed = validate_vector(vector)
                    vectors[key] = parsed
                    db.execute(
                        "INSERT OR REPLACE INTO manual_embeddings VALUES (?,?,?,?,?)",
                        (key, self.provider.model_digest, text, json.dumps(parsed), len(parsed)),
                    )
        with self._connect() as db:
            db.execute("DELETE FROM manual_pages")
            db.executemany(
                "INSERT INTO manual_pages VALUES (?,?,?,?,?,?,?)",
                [
                    (doc, number, chunk_number, digest, page_hash, key, self.provider.model_digest)
                    for doc, number, chunk_number, digest, page_hash, key in rows
                ],
            )
        return len(rows)

    def rank(  # noqa: C901
        self, query: str, pages: Sequence[tuple[Document, Page]]
    ) -> dict[tuple[str, int], float]:
        query = query.strip()
        if not query:
            return {}
        current = {
            (
                str(document.id), int(page.number), str(document.original_sha256),
                _hash(str(page.text).strip()),
            )
            for document, page in pages
        }
        if not current:
            return {}
        key = self.provider.model_digest + ":" + query
        with self._lock:
            vector = self._query_cache.get(key)
        if vector is None:
            vector = validate_vector(self.provider.embed([query])[0])
            with self._lock:
                if len(self._query_cache) >= 128:
                    self._query_cache.pop(next(iter(self._query_cache)))
                self._query_cache[key] = vector
        hashes: dict[tuple[str, int], list[str]] = {}
        with self._connect() as db:
            # The old loop issued one SELECT per candidate page. Batch the
            # exact same identity predicates while retaining every digest and
            # model guard. A bounded batch keeps SQLite variable usage safe.
            current_items = list(current)
            for offset in range(0, len(current_items), 180):
                batch = current_items[offset : offset + 180]
                predicates = " OR ".join(
                    "(document_id=? AND page_number=? AND document_sha256=? AND page_text_hash=?)"
                    for _ in batch
                )
                params: list[object] = [
                    value
                    for doc, number, digest, page_hash in batch
                    for value in (doc, number, digest, page_hash)
                ]
                sql = f"SELECT document_id,page_number,text_hash FROM manual_pages WHERE model_digest=? AND ({predicates})"  # noqa: E501, S608
                found = db.execute(
                    sql,
                    [self.provider.model_digest, *params],
                ).fetchall()
                for doc, number, text_hash in found:
                    hashes.setdefault((str(doc), int(number)), []).append(str(text_hash))

            text_hashes = {item for values in hashes.values() for item in values}
            vectors: dict[str, list[float]] = {}
            hash_list = list(text_hashes)
            for offset in range(0, len(hash_list), 400):
                batch = hash_list[offset : offset + 400]
                marks = ",".join("?" for _ in batch)
                sql = f"SELECT text_hash,vector FROM manual_embeddings WHERE model_digest=? AND text_hash IN ({marks})"  # noqa: E501, S608
                found = db.execute(
                    sql,
                    [self.provider.model_digest, *batch],
                ).fetchall()
                for text_hash, raw_vector in found:
                    parsed = validate_vector(json.loads(raw_vector), expected=len(vector))
                    vectors[str(text_hash)] = parsed
        result: dict[tuple[str, int], float] = {}
        denominator = math.sqrt(sum(item * item for item in vector))
        for identity, text_hashes in hashes.items():
            scores: list[float] = []
            for text_hash in text_hashes:
                candidate = vectors.get(text_hash)
                if candidate is None:
                    continue
                candidate_denominator = math.sqrt(sum(item * item for item in candidate))
                scores.append(
                    sum(left * right for left, right in zip(vector, candidate, strict=True))
                    / (denominator * candidate_denominator)
                    if denominator and candidate_denominator else 0.0
                )
            score = max(scores, default=0.0)
            if score >= self.min_score:
                result[identity] = score
        return result
