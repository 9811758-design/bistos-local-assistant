from typing import Annotated

from fastapi import APIRouter, Depends, Query

from bistos.api_models import (
    ConfigureModelInput,
    ManualDefaultInput,
    QuestionInput,
    TranslateInput,
)
from bistos.manual_defaults import ManualDefaults
from bistos.models import Evidence, Job, ManualDefault, Model, ModelStatus, SearchFilters, User
from bistos.routes_auth import Access
from bistos.services import Services


class SearchResult(Model):
    evidence: list[Evidence]


def question_router(services: Services, access: Access) -> APIRouter:
    router = APIRouter(prefix="/api")
    defaults = ManualDefaults(services.store)

    @router.get("/manual-defaults")
    def manual_defaults(user: Annotated[User, Depends(access.read)]) -> list[ManualDefault]:
        return defaults.entries(user)

    @router.put("/manual-defaults/{product_id}")
    def set_manual_default(
        product_id: str,
        payload: ManualDefaultInput,
        user: Annotated[User, Depends(access.require("manage_documents"))],
    ) -> list[ManualDefault]:
        return defaults.set(user, product_id, payload.country, payload.document_id)

    @router.get("/search")
    def search(
        user: Annotated[User, Depends(access.read)],
        product_id: Annotated[str, Query(min_length=1, max_length=60)],
        q: Annotated[str, Query(min_length=2, max_length=2000)],
        document_id: Annotated[str, Query(max_length=100)] = "",
        document_language: Annotated[str, Query(max_length=20)] = "",
        country: Annotated[str, Query(max_length=80)] = "",
        kind: Annotated[str, Query(max_length=20)] = "",
        revision: Annotated[str, Query(max_length=80)] = "",
    ) -> SearchResult:
        filters = SearchFilters(
            document_id=document_id, document_language=document_language,
            country=country, kind=kind, revision=revision,
        )
        return SearchResult(evidence=services.search.search(user, product_id, q, filters=filters))

    @router.post("/ask", status_code=202)
    def ask(payload: QuestionInput, user: Annotated[User, Depends(access.write)]) -> Job:
        return services.jobs.submit(
            user, payload.product_id, payload.question, payload.use_ai, payload.language,
            SearchFilters.model_validate(payload.model_dump(include=set(SearchFilters.model_fields))),
            parent_job_id=payload.parent_job_id, inquiry_mode=payload.inquiry_mode,
        )

    @router.get("/jobs/{job_id}")
    def job(job_id: str, user: Annotated[User, Depends(access.read)]) -> Job:
        return services.jobs.get(user, job_id)

    @router.post("/jobs/{job_id}/cancel")
    def cancel(job_id: str, user: Annotated[User, Depends(access.write)]) -> Job:
        return services.jobs.cancel(user, job_id)

    @router.post("/jobs/{job_id}/translate", status_code=202)
    def translate(
        job_id: str,
        user: Annotated[User, Depends(access.write)],
        payload: TranslateInput | None = None,
    ) -> Job:
        return services.jobs.translate(user, job_id, payload.language if payload else "en")

    @router.get("/history")
    def history(user: Annotated[User, Depends(access.read)]) -> list[Job]:
        return services.jobs.history(user)

    @router.get("/model")
    def model(_user: Annotated[User, Depends(access.read)]) -> ModelStatus:
        return services.answers.model_status()

    @router.post("/model")
    def configure_model(
        payload: ConfigureModelInput,
        user: Annotated[User, Depends(access.require("manage_documents"))],
    ) -> ModelStatus:
        return services.answers.configure_model(user, payload.model_name, payload.enabled)

    return router
