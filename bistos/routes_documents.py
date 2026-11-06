from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse

from bistos.api_models import (
    BulkAvailabilityInput,
    BulkAvailabilityResult,
    BulkTrashInput,
    BulkTrashResult,
    ImportResult,
    LibraryAssetReviewInput,
    LibraryAssetReviewResult,
    LibraryAssetView,
    Ok,
    ProductInput,
    PublishInput,
)
from bistos.errors import AppError
from bistos.library import BLOCKED_APPROVALS, BLOCKED_VALIDITY
from bistos.models import Attachment, Document, DocumentKind, LibraryAsset, Page, Product, User
from bistos.routes_auth import Access
from bistos.services import Services


def document_router(services: Services, access: Access) -> APIRouter:  # noqa: PLR0915
    router = APIRouter(prefix="/api")

    @router.get("/products")
    def products(
        user: Annotated[User, Depends(access.read)], include_unavailable: bool = False,
    ) -> list[Product]:
        if include_unavailable and "manage_documents" in user.capabilities:
            return services.store.products()
        return services.store.available_products(user)

    @router.post("/products", status_code=201)
    def create_product(
        payload: ProductInput, _user: Annotated[User, Depends(access.require("manage_documents"))]
    ) -> Product:
        return services.store.add_product(payload.id, payload.name)

    @router.get("/documents")
    def documents(user: Annotated[User, Depends(access.read)]) -> list[Document]:
        return services.store.documents(user)

    @router.post("/documents/bulk-availability")
    def bulk_availability(
        payload: BulkAvailabilityInput,
        user: Annotated[User, Depends(access.require("manage_documents"))],
    ) -> BulkAvailabilityResult:
        return services.documents.set_availability(user, payload.document_ids, payload.enabled)

    @router.post("/documents/bulk-trash")
    def bulk_trash(
        payload: BulkTrashInput,
        user: Annotated[User, Depends(access.require("manage_documents"))],
    ) -> BulkTrashResult:
        return services.documents.set_trash(
            user, payload.document_ids, payload.trashed, payload.reason
        )

    @router.get("/library/catalog")
    def library_catalog(
        user: Annotated[User, Depends(access.read)],
    ) -> list[LibraryAsset]:
        return services.store.library_catalog(user)

    @router.post("/library-assets/{asset_id}/review")
    def review_library_asset(
        asset_id: str,
        payload: LibraryAssetReviewInput,
        user: Annotated[User, Depends(access.require("manage_documents"))],
    ) -> LibraryAssetReviewResult:
        with services.store.lock:
            asset, target = services.store.library_asset(asset_id, user)
            document_id = payload.document_id
            if payload.action == "register":
                if asset.kind == "service" and payload.kind != "service":
                    raise AppError("서비스 첨부는 서비스 매뉴얼로만 등록할 수 있습니다.", 409)
                marker = f"{asset.status} {asset.name} {asset.reason}".casefold()
                if asset.role.casefold() in {"approval", "결재", "history"}:
                    raise AppError("승인·이력 첨부는 매뉴얼로 등록할 수 없습니다.", 409)
                blocked_marker = any(
                    value.casefold() in marker
                    for value in (
                        *(BLOCKED_APPROVALS - {"quarantined"}),
                        *(BLOCKED_VALIDITY - {"quarantined"}),
                        "history",
                    )
                )
                if asset.status in {"linked", "excluded"} or blocked_marker:
                    raise AppError("보류 첨부가 이미 결정되었거나 등록할 수 없는 상태입니다.", 409)
                if not payload.product_id or not payload.title:
                    raise AppError("새 매뉴얼 등록에는 제품과 제목이 필요합니다.", 422)
                document = services.documents.upload(
                    user, asset.name, target.read_bytes(), payload.product_id,
                    payload.title, payload.kind, payload.revision, payload.language,
                    payload.country,
                )
                document = document.model_copy(update={
                    "enabled": False,
                    "source_record": asset.record_id,
                    "source_original_name": asset.name,
                    "ingestion_note": payload.reason.strip(),
                })
                with services.store.connection() as db:
                    db.execute(
                        "UPDATE documents SET payload=? WHERE id=?",
                        (document.model_dump_json(), document.id),
                    )
                document_id = document.id
            linked = services.store.review_library_asset(
                user, asset_id, "link" if payload.action == "register" else payload.action,
                document_id, payload.product_id, payload.reason,
            )
        return LibraryAssetReviewResult(
            asset=LibraryAssetView(
                id=linked.id, name=linked.name, sha256=linked.sha256,
                document_id=linked.document_id, status=linked.status,
                reason=linked.reason, product_id=payload.product_id,
            ),
            document_id=document_id,
        )

    @router.get("/documents/{doc_id}/attachments")
    def attachments(
        doc_id: str, user: Annotated[User, Depends(access.read)],
    ) -> list[Attachment]:
        return services.store.document_attachments(doc_id, user)

    @router.get("/library-assets/{asset_id}")
    def attachment(
        asset_id: str, user: Annotated[User, Depends(access.read)],
    ) -> FileResponse:
        asset, target = services.store.library_asset(asset_id, user)
        return FileResponse(target, filename=asset.name, media_type="application/octet-stream")

    @router.post("/documents", status_code=201)
    def upload(
        user: Annotated[User, Depends(access.require("manage_documents"))],
        file: Annotated[UploadFile, File()],
        product_id: Annotated[str, Form(max_length=60)],
        title: Annotated[str, Form(max_length=200)],
        kind: Annotated[DocumentKind, Form()],
        revision: Annotated[str, Form(max_length=40)] = "",
        language: Annotated[str, Form(max_length=20)] = "KOR",
        country: Annotated[str, Form(max_length=80)] = "",
    ) -> Document:
        content = file.file.read(100 * 1024 * 1024 + 1)
        if len(content) > 100 * 1024 * 1024:
            raise AppError("파일은 100MB 이하로 등록해 주세요.", 413)
        return services.documents.upload(
            user,
            file.filename or "document",
            content,
            product_id,
            title,
            kind,
            revision,
            language,
            country,
        )

    @router.post("/import")
    def import_corpus(
        user: Annotated[User, Depends(access.require("manage_documents"))],
    ) -> ImportResult:
        imported, skipped = services.documents.import_corpus(user)
        return ImportResult(imported=imported, skipped=skipped)

    @router.post("/documents/{doc_id}/publish")
    def publish(
        doc_id: str,
        payload: PublishInput,
        user: Annotated[User, Depends(access.require("manage_documents"))],
    ) -> Document:
        return services.documents.publish(
            user, doc_id, payload.review_note, payload.layout_reviewed
        )

    @router.post("/documents/{doc_id}/withdraw")
    def withdraw(
        doc_id: str, user: Annotated[User, Depends(access.require("manage_documents"))]
    ) -> Document:
        return services.documents.withdraw(user, doc_id)

    @router.get("/documents/{doc_id}/pages")
    def pages(doc_id: str, user: Annotated[User, Depends(access.read)]) -> list[Page]:
        return services.store.pages(doc_id, user)

    @router.get("/documents/{doc_id}/pdf")
    def pdf(doc_id: str, user: Annotated[User, Depends(access.read)]) -> FileResponse:
        target = services.store.file_path(doc_id, user)
        return FileResponse(
            target, media_type="application/pdf", headers={"Content-Disposition": "inline"}
        )

    @router.get("/documents/{doc_id}/original")
    def original(doc_id: str, user: Annotated[User, Depends(access.read)]) -> FileResponse:
        document = services.store.document(doc_id, user)
        return FileResponse(
            services.store.file_path(doc_id, user, original=True), filename=document.original_name
        )

    @router.post("/backup")
    def backup(user: Annotated[User, Depends(access.require("manage_documents"))]) -> FileResponse:
        with services.jobs.maintenance():
            target = services.backup.create(user)
        return FileResponse(target, filename="bistos-backup.zip")

    @router.post("/restore")
    def restore(
        user: Annotated[User, Depends(access.require("manage_documents"))],
        file: Annotated[UploadFile, File()],
    ) -> Ok:
        content = file.file.read(8 * 1024 * 1024 * 1024 + 1)
        if len(content) > 8 * 1024 * 1024 * 1024:
            raise AppError("복원 파일 크기가 8GiB를 초과했습니다.", 413)
        with services.jobs.maintenance():
            services.backup.restore(user, content)
            services.jobs.reset()
        return Ok()

    return router
