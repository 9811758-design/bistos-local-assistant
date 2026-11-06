"""Read-only migration from the verified source corpus into unpublished versions."""

import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bistos.document_files import digest, pdf_page_count
from bistos.errors import AppError
from bistos.models import Document, DocumentKind, Page, User
from bistos.store import Store


class CorpusDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    id: str
    model: str
    kind: DocumentKind
    language: str
    revision: str
    pdf: str
    pages_dir: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    pdf_pages: int = Field(ge=1, le=1500)
    original: str = ""
    original_member: str = ""
    pdf_is_derived: bool = False
    derived_pdf_sha256: str = ""


class CorpusManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    schema_version: Literal[2]
    documents: list[CorpusDocument]


def source_path(root: Path, value: str) -> Path:
    normalized = re.sub(r"\\+", "/", value)
    path = Path(normalized).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise AppError("자료 목록에 허용된 자료실 밖의 경로가 있습니다.")
    return path


def import_corpus(store: Store, corpus_dir: Path, user: User) -> tuple[int, int]:
    store.require(user, "manage_documents")
    try:
        manifest = CorpusManifest.model_validate_json((corpus_dir / "manifest.json").read_bytes())
    except (OSError, ValidationError) as exc:
        raise AppError("기존 자료실 목록을 읽을 수 없습니다.") from exc
    imported = skipped = 0
    with store.lock:
        known = {(doc.product_id, doc.original_sha256) for doc in store.documents(user)}
        for source in manifest.documents:
            if (source.model, source.sha256) in known:
                skipped += 1
                continue
            original = source_path(corpus_dir, source.original or source.pdf)
            render = source_path(corpus_dir, source.pdf)
            page_dir = source_path(corpus_dir, source.pages_dir)
            try:
                original_content, render_content = original.read_bytes(), render.read_bytes()
                if digest(original_content) != source.sha256:
                    raise AppError(f"{source.id}: 원본 해시가 다릅니다.")
                expected_render = (
                    source.derived_pdf_sha256 if source.pdf_is_derived else source.sha256
                )
                if digest(render_content) != expected_render:
                    raise AppError(f"{source.id}: PDF 해시가 다릅니다.")
                document_id = uuid.uuid4().hex
                if pdf_page_count(render) != source.pdf_pages:
                    raise AppError(f"{source.id}: 페이지 수가 다릅니다.")
                pages: list[Page] = []
                for number in range(1, source.pdf_pages + 1):
                    page_file = source_path(corpus_dir, str(page_dir / f"page-{number:03d}.txt"))
                    text = page_file.read_text(encoding="utf-8-sig")
                    if f"SOURCE: {source.id}" not in text or f"PDF_PAGE: {number}/" not in text:
                        raise AppError(f"{source.id}: 페이지 연결 정보를 확인할 수 없습니다.")
                    pages.append(Page(document_id=document_id, number=number, text=text))
            except OSError as exc:
                raise AppError(f"{source.id}: 자료 파일을 읽을 수 없습니다.") from exc
            store.add_product(source.model, source.model)
            directory = store.data_dir / "documents" / document_id
            directory.mkdir()
            original_target = directory / f"original{original.suffix.casefold()}"
            render_target = directory / "render.pdf"
            shutil.copyfile(original, original_target)
            shutil.copyfile(render, render_target)
            document = Document(
                id=document_id,
                product_id=source.model,
                title=source.id,
                kind=source.kind,
                revision=source.revision,
                language=source.language,
                state="draft",
                page_count=len(pages),
                original_name=source.original_member or original.name,
                original_sha256=source.sha256,
                render_sha256=digest(render_content),
                created_at=datetime.now(UTC).isoformat(),
                needs_review=True,
            )
            store.save_document(document, pages, original_target, render_target)
            known.add((source.model, source.sha256))
            imported += 1
    return imported, skipped
