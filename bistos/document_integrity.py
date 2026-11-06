"""Bind every document file to its own version directory before serving or restoring."""

import hashlib
import re
import zipfile
from pathlib import Path

from bistos.document_files import pdf_page_count
from bistos.errors import AppError
from bistos.models import Document


def document_paths(
    root: Path, document: Document, original_relative: str, render_relative: str
) -> tuple[Path, Path]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", document.id):
        raise AppError("문서 버전 ID가 유효하지 않습니다.")
    folder = f"documents/{document.id}"
    if original_relative not in {f"{folder}/original.pdf", f"{folder}/original.docx"}:
        raise AppError("원본은 해당 문서 버전의 고정 경로에 있어야 합니다.")
    if render_relative != f"{folder}/render.pdf":
        raise AppError("PDF는 해당 문서 버전의 고정 경로에 있어야 합니다.")
    resolved_root = root.resolve()
    expected_folder = resolved_root / "documents" / document.id
    original = (resolved_root / original_relative).resolve()
    render = (resolved_root / render_relative).resolve()
    if original.parent != expected_folder or render.parent != expected_folder:
        raise AppError("문서 파일 경로가 다른 저장소로 연결되어 있습니다.")
    if Path(document.original_name).suffix.casefold() != original.suffix:
        raise AppError("원본 파일 형식과 문서 등록 정보가 일치하지 않습니다.")
    return original, render


def verify_hash(path: Path, expected: str) -> None:
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise AppError("문서 파일의 SHA-256 값이 유효하지 않습니다.")
    try:
        with path.open("rb") as source:
            actual = hashlib.file_digest(source, "sha256").hexdigest()
    except OSError as exc:
        raise AppError("문서 파일이 없거나 읽을 수 없습니다.") from exc
    if actual != expected:
        raise AppError("문서 파일의 해시가 일치하지 않습니다.")


def verify_format(path: Path) -> int | None:
    if path.suffix == ".pdf":
        with path.open("rb") as source:
            if not source.read(8).startswith(b"%PDF-"):
                raise AppError("표시 파일이 PDF 형식이 아닙니다.")
        return pdf_page_count(path)
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise AppError("원본 파일이 DOCX 형식이 아닙니다.")
    except (zipfile.BadZipFile, OSError) as exc:
        raise AppError("원본 파일이 DOCX 형식이 아닙니다.") from exc
    return None


def verify_document_files(
    root: Path, document: Document, original_relative: str, render_relative: str
) -> None:
    original, render = document_paths(root, document, original_relative, render_relative)
    verify_hash(original, document.original_sha256)
    if document.state == "processing_failed":
        if document.page_count:
            raise AppError("처리 실패 문서는 게시 가능한 페이지를 가질 수 없습니다.")
        if render.exists() or document.render_sha256:
            verify_hash(render, document.render_sha256)
        return
    verify_hash(render, document.render_sha256)
    verify_format(original)
    if verify_format(render) != document.page_count:
        raise AppError("문서 PDF 페이지 수가 등록 정보와 일치하지 않습니다.")
