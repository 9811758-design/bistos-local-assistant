"""Reviewed, source-linked structure annotations for table and condition evidence.

This module deliberately does not infer table cells or conditions.  An annotation is
usable only after its document, rendered source, page text, and cited evidence all
match exactly.  Invalid or stale annotations are ignored (fail closed).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Literal, TypedDict, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .models import Evidence

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_RELATION_KEYS = frozenset(
    {
        "scope",
        "when",
        "action",
        "result",
        "exception",
        "component",
        "material",
        "disinfection",
        "condition",
        "after",
        "elapsed",
    }
)
_MAX_SOURCE_CHARS = 20_000
_MAX_RELATIONS = 64
_MAX_CONFIG_BYTES = 2 * 1024 * 1024


def _normalise(value: str) -> str:
    """Collapse PDF line wrapping/whitespace without changing visible characters."""
    # Some stored claim passages carry escaped line breaks from JSON serialization.
    # Treat those as line breaks, while leaving all other literal backslashes alone.
    return " ".join(value.replace("\\r\\n", "\n").replace("\\n", "\n").split())


def _is_sha256(value: str) -> bool:
    return bool(_SHA256.fullmatch(value))


def _validated_relation_row(row: object) -> dict[str, str]:
    if not isinstance(row, dict):
        raise TypeError("each relation row must contain 1 to 8 fields")
    raw_items = cast("dict[object, object]", row)
    if not 1 <= len(raw_items) <= 8:
        raise ValueError("each relation row must contain 1 to 8 fields")
    normalised: dict[str, str] = {}
    for raw_key, raw_value in raw_items.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            raise TypeError("relation keys and values must be strings")
        key = _normalise(raw_key)
        item = _normalise(raw_value)
        if not key or not item:
            raise ValueError("relation keys and values must not be blank")
        if key not in _RELATION_KEYS and not key.startswith("column:"):
            raise ValueError("unknown relation key")
        if key.startswith("column:") and not _normalise(key[7:]):
            raise ValueError("column relation needs a header")
        normalised[key] = item
    return normalised


def _validated_relation_rows(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise TypeError("relations must contain 1 to 64 rows")
    raw_rows = cast("list[object]", value)
    if not 1 <= len(raw_rows) <= _MAX_RELATIONS:
        raise ValueError("relations must contain 1 to 64 rows")
    return [_validated_relation_row(row) for row in raw_rows]


def _validated_text_rows(value: object, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"{field_name} must be a list")
    rows = cast("list[object]", value)
    result: list[str] = []
    for row in rows:
        if not isinstance(row, str):
            raise TypeError(f"{field_name} rows must be strings")
        text = _normalise(row)
        if not text or len(text) > _MAX_SOURCE_CHARS:
            raise ValueError(f"{field_name} rows must be non-empty and bounded")
        result.append(text)
    return result


class SourceStructure(BaseModel):
    """A manually reviewed, page-local source relationship annotation.

    ``translation_ko`` is trusted only as a human-reviewed translation linked to
    this exact source hash.  The schema cannot prove that a translation contains
    no instructions, so callers must never auto-accept or infer it.
    ``translated_relations`` are likewise faithful reviewer translations of each
    relation row, never per-question answers or generated free-form advice.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=160)
    document_id: str = Field(min_length=1, max_length=160)
    render_sha256: str = Field(min_length=64, max_length=64)
    page: int = Field(gt=0, le=100_000)
    page_sha256: str = Field(min_length=64, max_length=64)
    source_text: str = Field(min_length=1, max_length=_MAX_SOURCE_CHARS)
    translation_ko: str = Field(default="", max_length=6_000)
    kind: str = Field(pattern=r"^(table|conditions)$")
    relations: list[dict[str, str]] = Field(min_length=1, max_length=_MAX_RELATIONS)
    translated_relations: list[str] = Field(default_factory=list, max_length=_MAX_RELATIONS)
    row_sources: list[str] = Field(default_factory=list, max_length=_MAX_RELATIONS)
    reviewed: Literal[True]

    @field_validator("reviewed", mode="before")
    @classmethod
    def _strict_reviewed(cls, value: object) -> Literal[True]:
        if type(value) is not bool or value is not True:
            raise ValueError("reviewed must be the boolean true")
        return True

    @field_validator("relations", mode="before")
    @classmethod
    def _validate_relations(cls, value: object) -> list[dict[str, str]]:
        return _validated_relation_rows(value)

    @field_validator("translated_relations", mode="before")
    @classmethod
    def _validate_translated_relations(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("translated_relations must be a list")
        rows = cast("list[object]", value)
        if len(rows) > _MAX_RELATIONS:
            raise ValueError("translated_relations must contain at most 64 rows")
        result: list[str] = []
        for row in rows:
            if not isinstance(row, str):
                raise TypeError("translated relation rows must be strings")
            text = _normalise(row)
            if not 1 <= len(text) <= 1_500:
                raise ValueError("translated relation rows must contain 1 to 1500 characters")
            result.append(text)
        return result

    @field_validator("row_sources", mode="before")
    @classmethod
    def _validate_row_sources(cls, value: object) -> list[str]:
        return _validated_text_rows(value, "row_sources")

    @model_validator(mode="after")
    def _translated_relation_count(self) -> SourceStructure:
        if self.translated_relations and len(self.translated_relations) != len(self.relations):
            raise ValueError("translated_relations must align one-to-one with relations")
        if self.kind == "table" and len(self.row_sources) != len(self.relations):
            raise ValueError("table row_sources must align one-to-one with relations")
        if self.row_sources and len(self.row_sources) != len(self.relations):
            raise ValueError("row_sources must align one-to-one with relations")
        if not self.row_sources:
            return self
        source = _normalise(self.source_text)
        for row_source, relation in zip(self.row_sources, self.relations, strict=True):
            if row_source not in source:
                raise ValueError("row_source must be contained in source_text")
            for key, value in relation.items():
                if value not in row_source:
                    raise ValueError("relation values must be contained in their row_source")
                if key.startswith("column:") and _normalise(key[7:]) not in source:
                    raise ValueError("column header must be contained in source_text")
        return self

    @field_validator("id", "document_id", "source_text")
    @classmethod
    def _normalise_text(cls, value: str) -> str:
        value = _normalise(value)
        if not value:
            raise ValueError("field must not be blank")
        return value

    @field_validator("render_sha256", "page_sha256")
    @classmethod
    def _valid_hash(cls, value: str) -> str:
        if not _is_sha256(value):
            raise ValueError("expected a SHA-256 digest")
        return value.lower()


class SourceRecord(TypedDict):
    id: str
    document_id: str
    render_sha256: str
    page: int
    page_sha256: str
    source_text: str
    translation_ko: str
    kind: str
    relations: list[dict[str, str]]
    translated_relations: list[str]
    row_sources: list[str]
    reviewed: bool

def _config_path(root: str | Path) -> Path:
    return Path(root) / "config" / "source-structures.json"


def _record_is_in_evidence(record: SourceStructure, evidence: Evidence) -> bool:
    if record.document_id != evidence.document_id:
        return False
    if record.render_sha256.lower() != evidence.render_sha256.lower():
        return False
    if record.page != evidence.physical_page:
        return False
    page_digest = hashlib.sha256(evidence.text.encode("utf-8")).hexdigest()
    if record.page_sha256.lower() != page_digest:
        return False
    return _normalise(record.source_text) in _normalise(evidence.text)


def _relation_is_source_bound(record: SourceStructure) -> bool:
    source = _normalise(record.source_text)
    for row in record.relations:
        for key, value in row.items():
            if _normalise(value) not in source:
                return False
            if key.startswith("column:") and _normalise(key[7:]) not in source:
                return False
    return True


def load(root: str | Path) -> list[SourceStructure]:
    """Load reviewed annotations, skipping malformed or untrusted records.

    The file is intentionally small and read on every call so a corrected review
    cannot be hidden behind a stale process cache.  A malformed top-level document
    fails closed to an empty list.
    """
    try:
        path = _config_path(root)
        if path.stat().st_size > _MAX_CONFIG_BYTES:
            return []
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if isinstance(payload, dict) and set(cast("dict[str, object]", payload)) == {"records"}:
        payload = cast("dict[str, object]", payload)["records"]
    if not isinstance(payload, list):
        return []
    raw_records = cast("list[object]", payload)
    if len(raw_records) > 1_000:
        return []

    result: list[SourceStructure] = []
    for raw in raw_records:
        if not isinstance(raw, dict):
            continue
        try:
            record = SourceStructure.model_validate(raw)
        except (ValidationError, TypeError, ValueError):
            continue
        if not _relation_is_source_bound(record):
            continue
        result.append(record)
    return result


def _as_dict(record: SourceStructure) -> SourceRecord:
    return cast("SourceRecord", cast("object", record.model_dump(mode="json")))


def structure_for_passage(
    root: str | Path,
    evidence: Sequence[Evidence],
    citation_ids: Iterable[str],
    passage_text: str,
) -> list[SourceRecord]:
    """Return only complete, cited annotations contained in a generated passage."""
    passage = _normalise(passage_text)
    if not passage:
        return []
    cited = set(citation_ids)
    if not cited:
        return []
    eligible = [item for item in evidence if item.evidence_id in cited]
    result: list[SourceRecord] = []
    for record in load(root):
        if _normalise(record.source_text) not in passage:
            continue
        if any(_record_is_in_evidence(record, item) for item in eligible):
            result.append(_as_dict(record))
    return result


def complete_structured_passage(root: str | Path, source: Evidence, text: str) -> str:
    """Expand a safely contained fragment to its reviewed source passage.

    Short fragments and partial overlaps remain untouched: guessing the missing
    relation would reintroduce the table/condition errors this layer is designed to
    prevent.  The returned text is bounded to the normal answer claim limit.
    """
    original = str(text)
    candidate = _normalise(original)
    if len(candidate) < 50:
        return original
    for record in load(root):
        source_text = _normalise(record.source_text)
        if candidate not in source_text:
            continue
        if len(source_text) > 2_400 or not _record_is_in_evidence(record, source):
            continue
        return source_text
    return original


__all__ = [
    "SourceStructure",
    "complete_structured_passage",
    "load",
    "structure_for_passage",
]
