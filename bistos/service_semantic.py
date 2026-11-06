"""Opt-in, offline-persisted semantic retrieval for service records.

The module intentionally has no model dependency.  A small local Ollama
embedding endpoint can be supplied explicitly; without it ServiceHistory
continues to use its existing lexical search.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from collections.abc import Sequence
from pathlib import Path
from threading import RLock
from typing import Protocol, cast
from urllib.parse import urlparse

import httpx2 as httpx


class EmbeddingProvider(Protocol):
    model_digest: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class CaseIdentity(Protocol):
    @property
    def case_id(self) -> str: ...

    @property
    def import_id(self) -> str: ...

    @property
    def symptom(self) -> str: ...


def _text_hash(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.casefold()).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _vector(values: object, *, expected: int | None = None) -> list[float]:
    if not isinstance(values, list):
        raise TypeError("embedding dimensions are invalid")
    raw = cast("list[object]", values)
    if not raw or len(raw) > 4096:
        raise ValueError("embedding dimensions are invalid")
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in raw):
        raise ValueError("embedding values are invalid")
    result = [float(value) for value in cast("list[int | float]", raw)]
    if any(not math.isfinite(value) for value in result):
        raise ValueError("embedding contains a non-finite value")
    if expected is not None and len(result) != expected:
        raise ValueError("embedding dimensions do not match")
    return result


def validate_vector(values: object, *, expected: int | None = None) -> list[float]:
    """Validate a local embedding vector for companion indexes."""
    return _vector(values, expected=expected)


class LocalOllamaEmbeddings:
    """Strict loopback-only client for Ollama's ``/api/embed`` endpoint."""

    def __init__(
        self,
        model: str,
        model_digest: str,
        *,
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.port != 11434
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("embedding endpoint must be loopback HTTP")
        if client is not None:
            client_base = str(getattr(client, "base_url", ""))
            client_parsed = urlparse(client_base)
            if (
                client_parsed.hostname != "127.0.0.1"
                or client_parsed.port != 11434
                or client_parsed.path not in {"", "/"}
            ):
                raise ValueError("embedding client must target loopback Ollama")
        self.model = model
        self.model_digest = model_digest
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client
        self._verified_until = 0.0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts or len(texts) > 64:
            raise ValueError("embedding batch must contain 1 to 64 texts")
        if any(not text or len(text) > 4000 for text in texts):
            raise ValueError("embedding text is empty or too long")
        if time.monotonic() >= self._verified_until:
            self.verify_local_model()
        own = self._client is None
        client = self._client or httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            trust_env=False,
            follow_redirects=False,
        )
        try:
            response = client.post("/api/embed", json={"model": self.model, "input": list(texts)})
            response.raise_for_status()
            raw_payload = response.json()
            payload = (
                cast("dict[str, object]", raw_payload)
                if isinstance(raw_payload, dict)
                else {}
            )
        finally:
            if own:
                client.close()
        values = payload.get("embeddings")
        if not isinstance(values, list):
            raise TypeError("embedding response count is invalid")
        values = cast("list[object]", values)
        if len(values) != len(texts):
            raise ValueError("embedding response count is invalid")
        result: list[list[float]] = []
        dimensions: int | None = None
        for item in values:
            parsed = _vector(item, expected=dimensions)
            dimensions = len(parsed)
            result.append(parsed)
        return result

    def verify_local_model(self) -> None:
        """Reject remote models and require the digest pinned in configuration."""
        own = self._client is None
        client = self._client or httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            trust_env=False,
            follow_redirects=False,
        )
        try:
            response = client.post("/api/show", json={"name": self.model})
            response.raise_for_status()
            raw_payload = response.json()
            payload = (
                cast("dict[str, object]", raw_payload)
                if isinstance(raw_payload, dict)
                else {}
            )
            tags_response = client.get("/api/tags")
            tags_response.raise_for_status()
            raw_tags = tags_response.json()
            tags_payload = cast("dict[str, object]", raw_tags) if isinstance(raw_tags, dict) else {}
        finally:
            if own:
                client.close()
        if (
            payload.get("remote_model")
            or payload.get("remote_host")
        ):
            raise ValueError("remote embedding models are not allowed")
        capabilities = payload.get("capabilities")
        capabilities = (
            cast("list[object]", capabilities) if isinstance(capabilities, list) else None
        )
        if capabilities is not None and "embedding" not in capabilities:
            raise ValueError("installed model does not provide embeddings")
        models = tags_payload.get("models")
        models = cast("list[dict[str, object]]", models) if isinstance(models, list) else []
        installed = next(
            (
                item
                for item in models or []
                if item.get("name") == self.model
            ),
            None,
        )
        digest = installed.get("digest") if isinstance(installed, dict) else None
        if not digest or digest != self.model_digest:
            raise ValueError("installed embedding model digest does not match the pinned digest")
        self._verified_until = time.monotonic() + 300


class SemanticSearch:
    """Persistent semantic index; callers explicitly invoke ``rebuild``."""

    def __init__(
        self,
        path: Path,
        provider: EmbeddingProvider,
        *,
        min_score: float = 0.78,
        query_prefix: str = "",
    ) -> None:
        self.path = path
        self.provider = provider
        self.min_score = min_score
        self.query_prefix = query_prefix
        self._query_cache: dict[str, list[float]] = {}
        self._vector_cache: dict[tuple[str, str], list[float]] = {}
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """CREATE TABLE IF NOT EXISTS embeddings (
                    text_hash TEXT NOT NULL, model_digest TEXT NOT NULL,
                    text TEXT NOT NULL, vector TEXT NOT NULL, dimensions INTEGER NOT NULL,
                    PRIMARY KEY(text_hash, model_digest));
                CREATE TABLE IF NOT EXISTS case_embeddings (
                    case_id TEXT NOT NULL, import_id TEXT NOT NULL,
                    text_hash TEXT NOT NULL, model_digest TEXT NOT NULL,
                    PRIMARY KEY(case_id, import_id));
                CREATE INDEX IF NOT EXISTS case_embeddings_hash
                    ON case_embeddings(text_hash, model_digest);"""
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def rebuild(self, cases: Sequence[CaseIdentity]) -> int:
        texts: dict[str, str] = {}
        for case in cases:
            text = str(getattr(case, "symptom", "")).strip()
            if text and len(text) <= 4000:
                texts.setdefault(_text_hash(text), text)
        vectors: dict[str, list[float]] = {}
        pending = list(texts.items())
        with self._connect() as db:
            for key, _text in pending:
                row = db.execute(
                    "SELECT vector FROM embeddings WHERE text_hash=? AND model_digest=?",
                    (key, self.provider.model_digest),
                ).fetchone()
                if row:
                    vectors[key] = _vector(json.loads(row[0]))
        pending = [(key, text) for key, text in pending if key not in vectors]
        for offset in range(0, len(pending), 64):
            batch = pending[offset : offset + 64]
            embeddings = self.provider.embed([item[1] for item in batch])
            for key, vector in zip((item[0] for item in batch), embeddings, strict=True):
                vectors[key] = _vector(vector)
            with self._connect() as db:
                for (key, text), vector in zip(batch, embeddings, strict=True):
                    parsed = _vector(vector)
                    db.execute(
                        "INSERT OR REPLACE INTO embeddings VALUES (?,?,?,?,?)",
                        (key, self.provider.model_digest, text, json.dumps(parsed), len(parsed)),
                    )
        with self._connect() as db:
            mappings: list[tuple[str, str, str, str]] = []
            for case in cases:
                text = str(getattr(case, "symptom", "")).strip()
                if text and len(text) <= 4000 and _text_hash(text) in vectors:
                    mappings.append(
                        (
                            str(case.case_id),
                            str(case.import_id),
                            _text_hash(text),
                            self.provider.model_digest,
                        )
                    )
            db.execute("DELETE FROM case_embeddings")
            db.executemany("INSERT INTO case_embeddings VALUES (?,?,?,?)", mappings)
        return len(texts)

    def rank(  # noqa: C901, PLR0912, PLR0915
        self, query: str, cases: Sequence[CaseIdentity]
    ) -> dict[tuple[str, str], float]:
        query = query.strip()
        if not query:
            return {}
        embedded_query = f"{self.query_prefix}{query}"
        key = self.provider.model_digest + ":" + embedded_query
        with self._lock:
            vector = self._query_cache.get(key)
        if vector is None:
            vector = _vector(self.provider.embed([embedded_query])[0])
            with self._lock:
                if len(self._query_cache) >= 128:
                    self._query_cache.pop(next(iter(self._query_cache)))
                self._query_cache[key] = vector
        current = {(str(case.case_id), str(case.import_id)) for case in cases}
        if not current:
            return {}
        mappings: list[tuple[str, str, str]] = []
        with self._connect() as db:
            identities = list(current)
            for offset in range(0, len(identities), 400):
                batch = identities[offset : offset + 400]
                marks = ",".join("(?,?)" for _ in batch)
                params = [value for identity in batch for value in identity]
                sql = f"""SELECT c.case_id,c.import_id,c.text_hash FROM case_embeddings c
                    WHERE c.model_digest=? AND (c.case_id,c.import_id) IN ({marks})"""  # noqa: S608
                mappings.extend(db.execute(sql, [self.provider.model_digest, *params]).fetchall())
        hashes = {text_hash for _, _, text_hash in mappings}
        vectors: dict[str, list[float]] = {}
        missing: list[str] = []
        with self._lock:
            for text_hash in hashes:
                cached = self._vector_cache.get((text_hash, self.provider.model_digest))
                if cached is None:
                    missing.append(text_hash)
                else:
                    vectors[text_hash] = cached
        if missing:
            with self._connect() as db:
                for offset in range(0, len(missing), 400):
                    batch = missing[offset : offset + 400]
                    marks = ",".join("?" for _ in batch)
                    sql = (
                        f"SELECT text_hash,vector FROM embeddings WHERE model_digest=? "  # noqa: S608
                        f"AND text_hash IN ({marks})"
                    )
                    for text_hash, raw in db.execute(
                        sql, [self.provider.model_digest, *batch]
                    ).fetchall():
                        candidate = _vector(json.loads(raw), expected=len(vector))
                        vectors[text_hash] = candidate
                        with self._lock:
                            if len(self._vector_cache) >= 4096:
                                self._vector_cache.pop(next(iter(self._vector_cache)))
                            self._vector_cache[(text_hash, self.provider.model_digest)] = candidate
        result: dict[tuple[str, str], float] = {}
        scores: dict[str, float] = {}
        for text_hash, candidate in vectors.items():
            denominator = math.sqrt(sum(x * x for x in vector) * sum(x * x for x in candidate))
            scores[text_hash] = (
                sum(x * y for x, y in zip(vector, candidate, strict=True)) / denominator
                if denominator
                else 0.0
            )
        for case_id, import_id, text_hash in mappings:
            identity = (case_id, import_id)
            if identity not in current:
                continue
            score = scores.get(text_hash, 0.0)
            if score >= self.min_score:
                result[identity] = score
        return result
