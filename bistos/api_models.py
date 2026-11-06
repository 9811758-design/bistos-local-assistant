from typing import Annotated, Literal

from pydantic import Field

from bistos.models import Capability, Model, SearchFilters, User


class Credentials(Model):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class SetupInput(Credentials):
    display_name: str = Field(min_length=1, max_length=60)


class UserInput(SetupInput):
    capabilities: list[Capability]


class UserUpdate(Model):
    capabilities: list[Capability]
    active: bool


class SessionView(Model):
    user: User
    csrf: str


class ProductInput(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,60}$")
    name: str = Field(min_length=1, max_length=80)


class PublishInput(Model):
    review_note: str = Field(min_length=1, max_length=2000)
    layout_reviewed: bool


class BulkAvailabilityInput(Model):
    document_ids: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        min_length=1, max_length=500
    )
    enabled: bool


class BulkAvailabilityItem(Model):
    document_id: str
    status: Literal["updated", "unchanged", "excluded"]
    enabled: bool
    reason: str = ""


class BulkAvailabilityResult(Model):
    items: list[BulkAvailabilityItem]
    updated: int
    unchanged: int
    excluded: int


class BulkTrashInput(Model):
    document_ids: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        min_length=1, max_length=500
    )
    trashed: bool
    reason: str = Field(default="", max_length=500)


class BulkTrashItem(Model):
    document_id: str
    status: Literal["updated", "unchanged", "excluded"]
    trashed: bool
    reason: str = ""


class BulkTrashResult(Model):
    items: list[BulkTrashItem]
    updated: int
    unchanged: int
    excluded: int


class LibraryAssetReviewInput(Model):
    action: Literal["link", "register", "exclude"]
    product_id: str = Field(default="", max_length=60)
    document_id: str = Field(default="", max_length=100)
    title: str = Field(default="", max_length=200)
    kind: Literal["user", "quick", "service", "other"] = "user"
    revision: str = Field(default="", max_length=80)
    language: str = Field(default="KOR", max_length=20)
    country: str = Field(default="", max_length=80)
    reason: str = Field(default="", max_length=500)


class LibraryAssetView(Model):
    id: str
    name: str
    sha256: str
    document_id: str = ""
    status: str
    reason: str = ""
    product_id: str = ""


class LibraryAssetReviewResult(Model):
    asset: LibraryAssetView
    document_id: str = ""


class QuestionInput(SearchFilters):
    inquiry_mode: Literal["auto", "product", "troubleshooting"] = "auto"
    product_id: str = Field(min_length=1, max_length=60)
    question: str = Field(min_length=2, max_length=2000)
    use_ai: bool = True
    language: Literal["ko", "en"] = "ko"
    parent_job_id: str = Field(default="", max_length=100)


class ManualDefaultInput(Model):
    country: str = Field(default="", max_length=80)
    document_id: str = Field(default="", max_length=100)


class ConfigureModelInput(Model):
    model_name: str = Field(min_length=1, max_length=100)
    enabled: bool


class TranslateInput(Model):
    language: Literal["ko", "en"] = "en"


class Ok(Model):
    ok: bool = True


class ImportResult(Model):
    imported: int
    skipped: int


class StatusView(Model):
    setup_required: bool
    model_ready: bool
    model_name: str
    generation_enabled: bool
    version: str = "1.0.0"
