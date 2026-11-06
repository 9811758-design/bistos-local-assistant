"""Explicit document publication and immutable original-version storage."""

import shutil
import uuid
from datetime import UTC, datetime

from bistos.api_models import (
    BulkAvailabilityItem,
    BulkAvailabilityResult,
    BulkTrashItem,
    BulkTrashResult,
)
from bistos.document_files import check_docx, checked_filename, digest, export_docx, pdf_pages
from bistos.errors import AppError
from bistos.importer import import_corpus
from bistos.library import BLOCKED_APPROVALS, BLOCKED_VALIDITY
from bistos.models import Document, DocumentKind, Page, User
from bistos.settings import Settings
from bistos.store import Store


class DocumentService:
    def __init__(self, store: Store, settings: Settings) -> None:
        self.store = store
        self.settings = settings

    def upload(
        self,
        user: User,
        filename: str,
        content: bytes,
        product_id: str,
        title: str,
        kind: DocumentKind,
        revision: str,
        language: str,
        country: str = "",
    ) -> Document:
        self.store.require(user, "manage_documents")
        if kind == "service":
            self.store.require(user, "read_service")
        extension = checked_filename(filename)
        if not content or len(content) > 100 * 1024 * 1024:
            raise AppError("업로드 파일은 0바이트 초과, 100MiB 이하여야 합니다.")
        if product_id not in {item.id for item in self.store.products()}:
            raise AppError("먼저 제품을 등록하세요.")
        if (
            not title.strip() or len(title) > 200 or len(revision) > 80
            or len(language) > 20 or len(country) > 80
        ):
            raise AppError("제목 200자, 개정 80자, 언어 20자 이내로 입력하세요.")
        if extension == ".docx":
            check_docx(content)
        with self.store.lock:
            document_id = uuid.uuid4().hex
            directory = self.store.data_dir / "documents" / document_id
            directory.mkdir()
            original = directory / f"original{extension}"
            render = directory / "render.pdf"
            original.write_bytes(content)
            failure = ""
            pages: list[Page] = []
            try:
                if extension == ".pdf":
                    shutil.copyfile(original, render)
                else:
                    export_docx(original, render)
                pages = pdf_pages(render, document_id)
            except AppError as exc:
                failure = exc.detail
            document = Document(
                id=document_id,
                product_id=product_id,
                title=title.strip(),
                kind=kind,
                revision=revision.strip(),
                language=language.strip(),
                state="processing_failed" if failure else "review",
                page_count=len(pages),
                original_name=filename,
                original_sha256=digest(content),
                render_sha256=digest(render.read_bytes()) if render.is_file() else "",
                created_at=datetime.now(UTC).isoformat(),
                country=country.strip(),
                review_note=failure,
                needs_review=True,
            )
            self.store.save_document(document, pages, original, render)
            return document

    def publish(  # noqa: C901
        self,
        user: User,
        doc_id: str,
        review_note: str,
        layout_reviewed: bool,
    ) -> Document:
        with self.store.lock:
            self.store.require(user, "manage_documents")
            document = self.store.document(doc_id, user)
            if document.trashed:
                raise AppError("휴지통 문서는 게시할 수 없습니다.", 409)
            if (
                document.approval_status.casefold() in BLOCKED_APPROVALS
                or document.validity.casefold() in BLOCKED_VALIDITY
            ):
                raise AppError("미승인·효력상실·격리 자료는 게시할 수 없습니다.", 409)
            if document.kind == "service":
                self.store.require(user, "read_service")
            if document.state == "processing_failed" or not document.page_count:
                raise AppError("문서 처리가 완료되지 않아 게시할 수 없습니다.")
            if not review_note.strip() or len(review_note) > 2000:
                raise AppError("원문 검토 내용을 2,000자 이내로 입력하세요.")
            if not layout_reviewed:
                raise AppError("페이지·표·도면과 추출 결과의 검토 확인이 필요합니다.")
            original = self.store.file_path(doc_id, user, original=True)
            render = self.store.file_path(doc_id, user)
            if digest(original.read_bytes()) != document.original_sha256:
                raise AppError("원본 파일 해시가 달라 게시할 수 없습니다.")
            if digest(render.read_bytes()) != document.render_sha256:
                raise AppError("표시 PDF 해시가 달라 게시할 수 없습니다.")
            pages = self.store.pages(doc_id, user)
            if len(pages) != document.page_count:
                raise AppError("페이지 색인이 완전하지 않아 게시할 수 없습니다.")
            updated = document.model_copy(
                update={
                    "state": "published",
                    "review_note": review_note.strip(),
                    "needs_review": False,
                }
            )
            with self.store.connection() as db:
                db.execute(
                    "UPDATE documents SET payload=? WHERE id=?", (updated.model_dump_json(), doc_id)
                )
                for page in pages:
                    reviewed = page.model_copy(update={"layout_reviewed": True})
                    db.execute(
                        "UPDATE pages SET payload=? WHERE document_id=? AND number=?",
                        (reviewed.model_dump_json(), doc_id, page.number),
                    )
                self.store.bump_epoch(db)
            return updated

    def withdraw(self, user: User, doc_id: str) -> Document:
        with self.store.lock:
            self.store.require(user, "manage_documents")
            document = self.store.document(doc_id, user)
            updated = document.model_copy(update={"state": "withdrawn"})
            with self.store.connection() as db:
                db.execute(
                    "UPDATE documents SET payload=? WHERE id=?", (updated.model_dump_json(), doc_id)
                )
                self.store.bump_epoch(db)
            return updated

    def set_availability(
        self, user: User, document_ids: list[str], enabled: bool
    ) -> BulkAvailabilityResult:
        """Toggle answer/library availability without changing publication metadata."""
        with self.store.lock:
            current = self.store.require(user, "manage_documents")
            unique_ids = list(dict.fromkeys(document_ids))
            if not 1 <= len(unique_ids) <= 500:
                raise AppError("문서는 1개 이상 500개 이하로 선택하세요.", 422)

            items: list[BulkAvailabilityItem] = []
            updates: list[Document] = []
            for doc_id in unique_ids:
                try:
                    document = self.store.document(doc_id, current)
                except AppError as exc:
                    if exc.status_code == 404:
                        items.append(BulkAvailabilityItem(
                            document_id=doc_id,
                            status="excluded",
                            enabled=False,
                            reason="문서를 찾을 수 없습니다.",
                        ))
                        continue
                    raise

                reason = self._availability_exclusion(document, current, enabled)
                if reason:
                    items.append(BulkAvailabilityItem(
                        document_id=doc_id,
                        status="excluded",
                        enabled=document.enabled,
                        reason=reason,
                    ))
                    continue

                if document.enabled == enabled:
                    items.append(BulkAvailabilityItem(
                        document_id=doc_id,
                        status="unchanged",
                        enabled=document.enabled,
                    ))
                    continue
                updates.append(document.model_copy(update={"enabled": enabled}))
                items.append(BulkAvailabilityItem(
                    document_id=doc_id,
                    status="updated",
                    enabled=enabled,
                ))

            updated = sum(item.status == "updated" for item in items)
            unchanged = sum(item.status == "unchanged" for item in items)
            excluded = sum(item.status == "excluded" for item in items)
            with self.store.connection() as db:
                for document in updates:
                    db.execute(
                        "UPDATE documents SET payload=? WHERE id=?",
                        (document.model_dump_json(), document.id),
                    )
                if updates:
                    self.store.bump_epoch(db)
                self.store.record_availability_audit(
                    db,
                    current.id,
                    enabled,
                    unique_ids,
                    [item.model_dump() for item in items],
                    updated,
                    unchanged,
                    excluded,
                )
            return BulkAvailabilityResult(
                items=items, updated=updated, unchanged=unchanged, excluded=excluded
            )

    def set_trash(
        self, user: User, document_ids: list[str], trashed: bool, reason: str = ""
    ) -> BulkTrashResult:
        """Move documents to or from a recoverable trash state without deleting files."""
        with self.store.lock:
            current = self.store.require(user, "manage_documents")
            unique_ids = list(dict.fromkeys(document_ids))
            if not 1 <= len(unique_ids) <= 500:
                raise AppError("문서는 1개 이상 500개 이하로 선택하세요.", 422)
            clean_reason = reason.strip()
            if trashed and not clean_reason:
                raise AppError("삭제 사유를 입력하세요.", 422)
            items: list[BulkTrashItem] = []
            updates: list[Document] = []
            for doc_id in unique_ids:
                try:
                    document = self.store.document(doc_id, current)
                except AppError as exc:
                    if exc.status_code == 404:
                        items.append(BulkTrashItem(
                            document_id=doc_id, status="excluded", trashed=False,
                            reason="문서를 찾을 수 없습니다.",
                        ))
                        continue
                    raise
                if document.kind == "service":
                    self.store.require(current, "read_service")
                if document.trashed == trashed:
                    items.append(BulkTrashItem(
                        document_id=doc_id, status="unchanged", trashed=trashed,
                        reason="",
                    ))
                    continue
                # Restore is deliberately disabled until a separate availability action.
                updates.append(document.model_copy(update={
                    "trashed": trashed,
                    "enabled": False,
                    "trash_reason": clean_reason if trashed else "",
                }))
                items.append(BulkTrashItem(
                    document_id=doc_id, status="updated", trashed=trashed,
                ))
            with self.store.connection() as db:
                for document in updates:
                    db.execute(
                        "UPDATE documents SET payload=? WHERE id=?",
                        (document.model_dump_json(), document.id),
                    )
                if updates:
                    self.store.bump_epoch(db)
                self.store.record_trash_audit(
                    db, current.id, trashed, unique_ids,
                    [item.model_dump() for item in items], clean_reason,
                )
            return BulkTrashResult(
                items=items,
                updated=sum(item.status == "updated" for item in items),
                unchanged=sum(item.status == "unchanged" for item in items),
                excluded=sum(item.status == "excluded" for item in items),
            )

    @staticmethod
    def _availability_exclusion(  # noqa: PLR0911
        document: Document, user: User, enabled: bool
    ) -> str:
        if document.kind == "service" and "read_service" not in user.capabilities:
            return "서비스 문서 권한이 없습니다."
        if not enabled:
            return ""
        if document.trashed:
            return "휴지통 문서는 먼저 복원해야 합니다."
        if document.state != "published":
            return "게시된 문서만 활성화할 수 있습니다."
        if (
            document.approval_status.casefold() in BLOCKED_APPROVALS
            or document.validity.casefold() in BLOCKED_VALIDITY
        ):
            return "미승인·효력상실·격리 문서는 활성화할 수 없습니다."
        if document.page_count <= 0:
            return "페이지가 있는 문서만 활성화할 수 있습니다."
        return ""

    def import_corpus(self, user: User) -> tuple[int, int]:
        return import_corpus(self.store, self.settings.corpus_dir, user)
