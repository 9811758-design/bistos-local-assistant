"""Bounded context reconstructed from owned, currently accessible answer records."""

from bistos.errors import AppError
from bistos.job_history import JobHistory
from bistos.library import matches_filters
from bistos.models import AnswerContext, AnswerResult, ConversationTurn, Job, SearchFilters, User


def cited_documents(result: AnswerResult) -> list[str]:
    if (result.inquiry_mode == "troubleshooting" and not result.claims
            and result.consultation_state in {"active", "exhausted"}
            and "symptom_confirmation_required" in result.limitation_codes):
        return list(dict.fromkeys(item.document_id for item in result.evidence))
    citations = {identifier for claim in result.claims for identifier in claim.citation_ids}
    return list(dict.fromkeys(
        item.document_id for item in result.evidence if item.evidence_id in citations
    ))


def followup_scope(
    history: JobHistory, user: User, product_id: str, parent_id: str, requested: SearchFilters,
) -> tuple[Job, SearchFilters]:
    parent = history.get(user, parent_id)
    if parent.source_job_id:
        parent = history.get(user, parent.source_job_id)
    candidate = Job(
        id="pending-followup", status="queued", question="", product_id=product_id,
        parent_job_id=parent.id,
    )
    history.ancestors(user, candidate)
    if parent.result is None:
        raise AppError("완료된 답변에만 이어서 질문할 수 있습니다.", 409)
    documents = [
        history.store.document(identifier, user)
        for identifier in cited_documents(parent.result)
    ]
    if not documents:
        raise AppError("이전 답변의 근거 문서를 확인할 수 없습니다.", 409)
    updates = parent.filters.model_dump()
    if len(documents) == 1 and parent.result.inquiry_mode != "troubleshooting":
        updates["document_id"] = documents[0].id
    for name, value in requested.model_dump().items():
        if value:
            field_filter = SearchFilters.model_validate({name: value})
            if (
                (updates[name] and updates[name].casefold() != value.casefold())
                or not all(matches_filters(doc, field_filter) for doc in documents)
            ):
                raise AppError(
                    "후속 질문의 근거 범위가 달라졌습니다. 새 질문으로 시작해 주세요.", 409,
                )
            updates[name] = value
    return parent, SearchFilters.model_validate(updates)


def answer_context(history: JobHistory, user: User, job: Job) -> AnswerContext:
    parents = history.ancestors(user, job)
    if not parents or parents[0].result is None:
        raise AppError("이전 답변의 근거를 확인할 수 없습니다.", 409)
    troubleshooting = parents[0].result.inquiry_mode == "troubleshooting"
    return AnswerContext(
        inquiry_mode=parents[0].result.inquiry_mode,
        initial_question=parents[-1].question if troubleshooting else "",
        attempted_claims=list(dict.fromkeys(
            claim.text for parent in parents if parent.result is not None
            for claim in parent.result.claims if claim.presentation == "direct"
        ))[:64] if troubleshooting else [],
        turns=[
            ConversationTurn(
                question=parent.question,
                claims=[claim.text for claim in parent.result.claims] if parent.result else [],
                questions=parent.result.questions if parent.result else [],
            )
            for parent in reversed(parents[:2])
        ],
        document_ids=[] if troubleshooting else cited_documents(parents[0].result),
    )
