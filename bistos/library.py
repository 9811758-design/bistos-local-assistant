"""Local bulk-library metadata and immutable attachment validation."""

import re
from pathlib import Path

from pydantic import TypeAdapter

from bistos.document_integrity import verify_hash
from bistos.errors import AppError
from bistos.models import Document, LibraryAsset, SearchFilters

BLOCKED_APPROVALS = frozenset({
    "pending", "rejected", "unapproved", "approval_only", "quarantined", "invalid", "excluded",
    "미승인", "결재 진행x", "결재 진행 x", "결재진행x",
})
BLOCKED_VALIDITY = frozenset({
    "invalid", "expired", "history", "superseded", "withdrawn", "quarantined", "excluded",
    "metadata_conflict", "효력상실",
})
METADATA_FIELDS = frozenset({
    "country", "approval_date", "approval_status", "validity", "source_record",
    "source_folder", "source_files", "ingestion_note", "language",
    "pdf_is_derived", "source_original_name",
})


def merge_document_metadata(document: Document, payload: str) -> Document:
    """Overlay provenance without rewriting an existing registered document."""
    updates = TypeAdapter(dict[str, object]).validate_json(payload)
    if set(updates) - METADATA_FIELDS:
        raise AppError("자료실 문서 메타데이터 필드가 올바르지 않습니다.")
    return Document.model_validate({**document.model_dump(), **updates})


def answer_eligible(document: Document) -> bool:
    return (
        document.enabled
        and not document.trashed
        and document.state == "published"
        and document.approval_status.casefold() not in BLOCKED_APPROVALS
        and document.validity.casefold() not in BLOCKED_VALIDITY
    )


def normalized_language(value: str) -> str:
    return {"kor": "ko", "eng": "en"}.get(value.casefold(), value.casefold())


def matches_filters(document: Document, filters: SearchFilters) -> bool:
    pairs = (
        (filters.document_id, document.id),
        (normalized_language(filters.document_language), normalized_language(document.language)),
        (filters.country, document.country),
        (filters.kind, document.kind),
        (filters.revision, document.revision),
    )
    return all(
        not requested or requested.casefold() == actual.casefold()
        for requested, actual in pairs
    )


def asset_path(data_dir: Path, asset: LibraryAsset) -> Path:
    """Accept only content-addressed files within the local asset directory."""
    if not re.fullmatch(r"[a-f0-9]{64}", asset.sha256):
        raise AppError("첨부 파일 해시가 올바르지 않습니다.")
    if not re.fullmatch(
        rf"library-assets/{asset.sha256}\.(pdf|docx|doc|ai|xls)", asset.relative_path
    ):
        raise AppError("첨부 파일 경로가 올바르지 않습니다.")
    root = data_dir.resolve()
    path = (root / asset.relative_path).resolve()
    if not path.is_relative_to(root / "library-assets"):
        raise AppError("저장소 밖의 첨부 파일은 허용하지 않습니다.")
    verify_hash(path, asset.sha256)
    return path
