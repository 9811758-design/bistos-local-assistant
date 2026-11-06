from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import Field

from bistos.models import Model, User
from bistos.routes_auth import Access
from bistos.services import Services


class GuideCreateInput(Model):
    query: str = Field(min_length=1, max_length=1000)
    model: str = Field(min_length=1, max_length=100)
    source: str = Field(default="", max_length=20)
    case_id: str = Field(min_length=1, max_length=100)
    mode: Literal["actual", "practice"]
    language: Literal["ko", "en"] = "ko"


class GuideEventInput(Model):
    revision: int = Field(ge=0)
    action: Literal[
        "resolved", "unresolved", "unable", "symptom_changed", "pause", "resume", "stop"
    ]
    note: str = Field(default="", max_length=1000)


def service_guides_router(services: Services, access: Access) -> APIRouter:
    router = APIRouter(prefix="/api/service-history/guides")

    @router.post("")
    def create(
        payload: GuideCreateInput, user: Annotated[User, Depends(access.write)]
    ) -> dict[str, object]:
        return services.service_guides.create(
            user,
            payload.query,
            payload.model,
            payload.source,
            payload.case_id,
            payload.mode,
            payload.language,
        )

    @router.get("")
    def list_guides(user: Annotated[User, Depends(access.read)]) -> dict[str, object]:
        return services.service_guides.list(user)

    @router.get("/{guide_id}")
    def detail(guide_id: str, user: Annotated[User, Depends(access.read)]) -> dict[str, object]:
        return services.service_guides.detail(user, guide_id)

    @router.post("/{guide_id}/events")
    def event(
        guide_id: str,
        payload: GuideEventInput,
        user: Annotated[User, Depends(access.write)],
    ) -> dict[str, object]:
        return services.service_guides.event(
            user, guide_id, payload.revision, payload.action, payload.note
        )

    return router
