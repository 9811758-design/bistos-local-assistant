from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Capability = Literal["ask", "read_service", "manage_documents", "manage_users"]
DocumentKind = Literal["user", "quick", "service", "other"]
DocumentState = Literal["draft", "review", "published", "withdrawn", "processing_failed"]
JobState = Literal["queued", "running", "completed", "failed", "cancelled"]


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class User(Model):
    id: str
    username: str
    display_name: str
    capabilities: list[Capability]
    active: bool = True


class Product(Model):
    id: str
    name: str


class Attachment(Model):
    id: str
    name: str
    sha256: str
    role: str


class LibraryAsset(Attachment):
    record_id: str
    document_id: str = ""
    relative_path: str
    kind: DocumentKind = "other"
    status: str = "registered"
    reason: str = ""


class LibraryAssetOverlay(Model):
    asset_id: str
    document_id: str = ""
    status: Literal["linked", "excluded"]
    product_id: str = ""
    reason: str = ""
    reviewed_at: str
    reviewed_by: str


class SearchFilters(Model):
    document_id: str = Field(default="", max_length=100)
    document_language: str = Field(default="", max_length=20)
    country: str = Field(default="", max_length=80)
    kind: str = Field(default="", max_length=20)
    revision: str = Field(default="", max_length=80)


class ManualDefault(Model):
    product_id: str
    country: str
    document_id: str
    active: bool = True


class Document(Model):
    id: str
    product_id: str
    title: str
    kind: DocumentKind
    revision: str
    language: str
    state: DocumentState
    enabled: bool = True
    trashed: bool = False
    trash_reason: str = ""
    page_count: int
    original_name: str
    original_sha256: str
    render_sha256: str
    created_at: str
    review_note: str = ""
    needs_review: bool = True
    country: str = ""
    approval_date: str = ""
    approval_status: str = "unknown"
    validity: str = "unconfirmed"
    source_record: str = ""
    source_folder: str = ""
    source_files: list[Attachment] = Field(default_factory=list)
    ingestion_note: str = ""
    pdf_is_derived: bool = False
    source_original_name: str = ""


class Page(Model):
    document_id: str
    number: int
    text: str
    layout_reviewed: bool = False


class Evidence(Model):
    evidence_id: str
    document_id: str
    product_id: str
    title: str
    kind: DocumentKind
    revision: str
    language: str
    physical_page: int
    is_derived_pdf: bool
    text: str
    original_sha256: str
    render_sha256: str
    chunk_sha256: str
    layout_reviewed: bool
    original_name: str = ""
    country: str = ""
    approval_date: str = ""
    approval_status: str = "unknown"
    validity: str = "unconfirmed"
    source_record: str = ""


class Claim(Model):
    id: str = Field(pattern=r"^C[1-8]$")
    # Source claims may preserve a complete page-local section. Generated
    # direct explanations remain bounded by ExplanationClaim at 700 chars.
    text: str = Field(min_length=1, max_length=2400)
    citation_ids: list[str] = Field(min_length=1, max_length=6)
    presentation: Literal["direct", "source"] = "source"


class Variant(Model):
    text: str
    citation_ids: list[str]


class Conflict(Model):
    topic: str
    variants: list[Variant]


class AnswerTranslation(Model):
    language: Literal["ko", "en"] = "en"
    texts: dict[str, str]


class AnswerTopic(Model):
    label: str
    status: str
    claim_ids: list[str] = Field(default_factory=list)


class AnswerResult(Model):
    inquiry_mode: Literal["product", "troubleshooting"] = "product"
    followup_options: list[str] = Field(default_factory=list, max_length=6)
    consultation_state: Literal["", "active", "resolved", "exhausted"] = ""
    status: str
    mode: Literal["ai", "search"]
    claims: list[Claim] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    limitation_codes: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    message: str = ""
    elapsed_seconds: float = 0
    translation: AnswerTranslation | None = None
    translation_error: str = ""
    topics: list[AnswerTopic] = Field(default_factory=list)


class Job(Model):
    inquiry_mode: Literal["auto", "product", "troubleshooting"] = "auto"
    id: str
    status: JobState
    question: str
    result: AnswerResult | None = None
    error: str = ""
    queue_position: int = 0
    language: Literal["ko", "en"] = "ko"
    source_job_id: str = ""
    filters: SearchFilters = Field(default_factory=SearchFilters)
    product_id: str = ""
    parent_job_id: str = Field(default="", max_length=100)


class ConversationTurn(Model):
    question: str
    claims: list[str]
    questions: list[str] = Field(default_factory=list)


class AnswerContext(Model):
    inquiry_mode: Literal["product", "troubleshooting"] = "product"
    initial_question: str = ""
    attempted_claims: list[str] = Field(default_factory=list, max_length=64)
    turns: list[ConversationTurn] = Field(min_length=1, max_length=2)
    document_ids: list[str] = Field(default_factory=list, max_length=32)


class ModelStatus(Model):
    ready: bool
    name: str = ""
    digest: str = ""
    enabled: bool = False
    detail: str = ""
