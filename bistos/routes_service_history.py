from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import Field

from bistos.errors import AppError
from bistos.models import Model, User
from bistos.routes_auth import Access
from bistos.service_history import CaseMatch, ImportSummary, ServiceCase
from bistos.services import Services


class ServiceSearchInput(Model):
    query: str = Field(min_length=1, max_length=1000)
    model: str = Field(default="", max_length=100)
    source: Literal["", "domestic", "overseas"] = ""
    limit: int = Field(default=5, ge=1, le=5)
    language: Literal["ko", "en"] = "ko"
    sort: Literal["similarity", "newest", "oldest"] = "similarity"
    date_field: Literal["received", "completed"] = "received"
    page: int = Field(default=1, ge=1, le=10000)
    page_size: Literal[5] = 5


def service_history_router(services: Services, access: Access) -> APIRouter:
    router = APIRouter(prefix="/api/service-history")

    @router.get("/sources")
    def sources(user: Annotated[User, Depends(access.read)]) -> list[ImportSummary]:
        return services.service_history.sources(user)

    @router.get("/models")
    def models(user: Annotated[User, Depends(access.read)]) -> list[dict[str, str | int]]:
        return services.service_history.models(user)

    @router.get("/case/{case_id}")
    def detail(case_id: str, user: Annotated[User, Depends(access.read)]) -> ServiceCase:
        return services.service_history.detail(user, case_id)

    @router.get("/source/{source}/original")
    def original(
        source: Literal["domestic", "overseas"], user: Annotated[User, Depends(access.read)]
    ) -> FileResponse:
        path, filename = services.service_history.original(user, source)
        return FileResponse(
            path,
            filename=filename,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    @router.post("/import")
    def import_file(
        user: Annotated[User, Depends(access.require("manage_documents"))],
        file: Annotated[UploadFile, File()],
        source: Annotated[Literal["domestic", "overseas"], Form()],
    ) -> ImportSummary:
        services.store.require(user, "read_service")
        content = file.file.read(25 * 1024 * 1024 + 1)
        if len(content) > 25 * 1024 * 1024:
            raise AppError("서비스 이력 파일은 25MB 이하만 등록할 수 있습니다.", 413)
        result = services.service_history.import_content(
            user, file.filename or "service.xlsx", content, source
        )
        if services.semantic_runtime is not None:
            services.semantic_runtime.schedule_refresh(
                services.service_history.semantic_cases,
            )
        return result

    @router.post("/search")
    def search(
        payload: ServiceSearchInput, user: Annotated[User, Depends(access.write)]
    ) -> list[CaseMatch]:
        return services.service_history.search(
            user, payload.query, payload.model, payload.source, payload.limit, payload.language
        )

    @router.post("/browse")
    def browse(
        payload: ServiceSearchInput, user: Annotated[User, Depends(access.write)]
    ) -> dict[str, object]:
        return services.service_history.search_page(
            user, payload.query, payload.model, payload.source, payload.language,
            sort=payload.sort, date_field=payload.date_field, page=payload.page,
        )

    @router.post("/question")
    def question(
        payload: ServiceSearchInput, user: Annotated[User, Depends(access.write)]
    ) -> dict[str, object]:
        return services.service_history.service_question(
            user, payload.query, payload.model, payload.source,
            language=payload.language, limit=payload.limit,
            sort=payload.sort, date_field=payload.date_field, page=payload.page,
        )

    return router
