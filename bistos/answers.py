import re
import time
import uuid
from typing import Literal, Protocol

from bistos.answer_conflicts import annotate_wavelength_conflicts
from bistos.answer_explanation import explain_answer
from bistos.answer_gates import evidence_gate, validity_notice, version_gate
from bistos.answer_selection import select_answer
from bistos.answer_topics import (
    Topic,
    ambiguous_battery_life,
    compound_topics,
    dependent_topics,
    explicit_compound,
    qualifiers_unclear,
)
from bistos.answer_translation import translate_answer
from bistos.audio_support import audio_support
from bistos.errors import AppError
from bistos.inquiry import (
    InquiryMode,
    clarification,
    has_new_troubleshooting_detail,
    remove_repeated_steps,
    resolved_report,
    retry_report,
    symptom_choices,
    symptom_detail_question,
)
from bistos.inquiry import (
    inquiry_mode as resolve_inquiry_mode,
)
from bistos.library import answer_eligible, matches_filters, normalized_language
from bistos.models import (
    AnswerContext,
    AnswerResult,
    AnswerTopic,
    Claim,
    Conflict,
    Document,
    Evidence,
    ModelStatus,
    Product,
    SearchFilters,
    User,
)
from bistos.ollama import INPUT_BYTES, Ollama, SettingsStore
from bistos.product_identity import model_mismatch
from bistos.query_translation import retrieval_query
from bistos.question_intent import focused_question, question_intent, service_question
from bistos.reviewed_sections import candidate_pages, reviewed_section_answer
from bistos.search import SearchEngine, SearchStore, page_evidence
from bistos.settings import Settings


class AnswerStore(SearchStore, SettingsStore, Protocol):
    def products(self) -> list[Product]: ...
    def documents(self, user: User) -> list[Document]: ...
    def document(self, doc_id: str, user: User) -> Document: ...
    def epoch(self) -> int: ...
    def user(self, user_id: str) -> User: ...


def current_result(store: AnswerStore, user: User, result: AnswerResult) -> AnswerResult:
    """Recheck answer text access, including publication and changed service permissions."""
    actor = store.user(user.id)
    if not actor.active or "ask" not in actor.capabilities:
        raise AppError("현재 질문 기록을 볼 수 있는 권한이 없습니다.", 403)
    for evidence in result.evidence:
        document = store.document(evidence.document_id, actor)
        if not answer_eligible(document) or (
            document.kind == "service" and "read_service" not in actor.capabilities
        ):
            raise AppError(
                "근거의 게시 상태 또는 권한이 변경되어 이전 답변을 표시할 수 없습니다.", 403
            )
        if (
            document.original_sha256 != evidence.original_sha256
            or document.render_sha256 != evidence.render_sha256
            or (
                evidence.source_record
                and (document.country, document.revision, document.language,
                     document.approval_status, document.validity)
                != (evidence.country, evidence.revision, evidence.language,
                    evidence.approval_status, evidence.validity)
            )
        ):
            raise AppError("근거 문서 버전이 변경되어 답변을 다시 검색해야 합니다.", 409)
    return result


def contextual_question(question: str, context: AnswerContext) -> str:
    """Prior answers identify the topic only; they never become new evidence."""
    parts = [
        "같은 제품·매뉴얼의 후속 질문입니다. 이전 답변은 대화 맥락이며 새로운 근거가 아닙니다.",
        "후속 질문에 직접 답하세요. 다음 단계를 물으면 앞선 설명을 반복하지 마세요.",
        "이전 답변은 확인된 일부 내용일 수 있으며 전체 고장 원인을 확정한 것이 아닙니다.",
        "다음 단계나 해결 방법이 제공된 원문에 없으면 확인되지 않는다고 답하세요.",
    ]
    for turn in context.turns:
        parts.extend([
            f"이전 질문: {turn.question}",
            "이전 답변: " + "\n".join(turn.claims),
            "이전 확인 질문: " + "\n".join(turn.questions),
        ])
    parts.append(f"현재 질문: {question}")
    prompt = "\n".join(parts)
    if len(prompt.encode("utf-8")) > INPUT_BYTES // 2:
        raise AppError(
            "이전 대화가 너무 깁니다. 내용을 구체화해 새 질문을 보내 주세요.",
            413,
        )
    return prompt


def _new_troubleshooting_subject(current: str, context: AnswerContext) -> bool:
    aliases = {
        "touch": r"터치|touch",
        "audio": r"소리|sound|음량|volume",
        "printer": r"프린터|printer|용지|paper",
        "battery": r"배터리|battery",
        "screen": r"화면|screen|display",
        "power": r"전원|power",
        "measurement": r"측정값|reading|measurement",
    }
    def subjects(text: str) -> set[str]:
        return {
            name for name, pattern in aliases.items()
            if re.search(pattern, text, re.IGNORECASE)
        }

    prior = " ".join(
        [context.initial_question, *(turn.question for turn in context.turns)]
    )
    return question_intent(current) == "troubleshooting" and any(
        subject not in subjects(prior) for subject in subjects(current)
    )


def _recent_retry_claims(context: AnswerContext) -> list[str]:
    claims: list[str] = []
    seen: set[str] = set()
    budget = 1200
    # Keep only a bounded tail of recent claims. This preserves the last
    # reported checks without allowing a long consultation to pollute search.
    for turn in reversed(context.turns):
        for claim in reversed(turn.claims):
            key = re.sub(r"\s+", " ", claim).strip().casefold()
            if not key or key in seen:
                continue
            if len(claim) > budget:
                continue
            seen.add(key)
            claims.insert(0, claim)
            budget -= len(claim)
            if len(claims) >= 8 or budget <= 0:
                break
        if len(claims) >= 8 or budget <= 0:
            break
    return claims


def context_search_question(question: str, context: AnswerContext) -> str:  # noqa: PLR0911
    current = focused_question(question)
    setting_context = _setting_followup_question(current, context)
    if setting_context is not None:
        return setting_context
    if context.turns[-1].questions and question in {
        "전원이 켜지지 않아요", "측정값이 표시되지 않음",
        "프린터 용지가 나오지 않아요", "용지는 나오지만 인쇄가 안 돼요",
    }:
        return question
    # A new factual topic must not inherit an old symptom as its search intent.
    if question_intent(current) in {"spec", "principle", "usage"} and re.search(
        "|".join((  # noqa: FLY002
            r"제품|장치|기기|배터리|충전|온도|습도|혈압|산소|심전도|프린터|터치|화면",
            r"product|device|battery|charg|temperature|humidity|ECG|SpO2|NIBP|printer|touch|screen",
        )),
        current, re.IGNORECASE,
    ):
        return current or question
    # An explicit current subject or correction supersedes a previous symptom.
    if current != question or re.search(r"터치|touch", current, re.IGNORECASE):
        return current or question
    if _new_troubleshooting_subject(current, context):
        return current or question
    # Retry reports are elliptical: retain both the original symptom and the
    # actions already reported, so retrieval can propose the next grounded
    # step instead of treating "already tried" as a fresh general question.
    if retry_report(current):
        return "\n".join([
            context.initial_question,
            *(turn.question for turn in context.turns),
            *_recent_retry_claims(context),
            question,
        ])
    if context.turns[-1].questions and re.search(
        r"터치|touch", context.turns[-1].question, re.IGNORECASE,
    ):
        return "터치 증상 확인: " + question
    return "\n".join([
        context.initial_question, *(turn.question for turn in context.turns), question,
    ])


def answer_question_for_context(question: str, context: AnswerContext | None) -> str:
    """Give answer generation the resolved topic while preserving the user text."""
    focused = focused_question(question) or question
    if context is not None:
        setting_context = _setting_followup_question(focused, context)
        if setting_context is not None:
            return setting_context
    if context is not None and retry_report(question):
        return context_search_question(question, context)
    return focused


def _setting_followup_question(question: str, context: AnswerContext) -> str | None:
    """Resolve an elliptical numeric-setting follow-up from the prior question only."""
    if not re.match(r"(?:그럼|그러면)\s*[+-]?\d", question):
        return None
    if not re.search(r"설정|바꾸|변경", question):
        return None
    prior = context.turns[-1].question
    # Explicitly introducing a different feature must not carry over the old setting.
    subjects = (
        r"배터리|battery", r"온도|temperature", r"음량|volume", r"밝기|brightness",
        r"알람|경보|alarm", r"파형\s*속도|wave\s*speed", r"오프셋|offset",
        r"대기\s*모드|standby", r"환자\s*(?:유형|종류)|patient\s*type",
        r"SpO2|산소포화도", r"ECG|심전도", r"RESP|호흡", r"NIBP|혈압",
    )
    if any(re.search(term, question, re.IGNORECASE)
           and not re.search(term, prior, re.IGNORECASE) for term in subjects):
        return None
    return f"이전 질문의 주제(사실 근거가 아님): {prior}\n현재 후속 질문에만 답하세요: {question}"


def _compound_topic_status(result: AnswerResult, retained: list[Claim]) -> str:
    if result.status == "answered" and not any(c.presentation == "direct" for c in retained):
        return "evidence_missing"
    if len(retained) < len(result.claims) or "incomplete_evidence" in result.limitation_codes:
        return "partial_answer"
    return result.status


class AnswerEngine:
    def __init__(
        self, store: AnswerStore, settings: Settings, search: SearchEngine | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.search = search or SearchEngine(store)
        self.ollama = Ollama(settings.ollama_url, store)

    def model_status(self) -> ModelStatus:
        return self.ollama.status()

    def configure_model(self, user: User, name: str, enabled: bool) -> ModelStatus:
        actor = self.store.user(user.id)
        if not actor.active or "manage_documents" not in actor.capabilities:
            raise AppError("모델 설정은 문서 관리자만 변경할 수 있습니다.", 403)
        return self.ollama.configure(name, enabled)

    def translate(
        self, user: User, result: AnswerResult, language: Literal["ko", "en"] = "en"
    ) -> AnswerResult:
        started = time.monotonic()
        current_result(self.store, user, result)
        translated = translate_answer(result, self.ollama, self.settings.root, language)
        current_result(self.store, user, translated)
        return translated.model_copy(
            update={"elapsed_seconds": result.elapsed_seconds + time.monotonic() - started}
        )

    def answer(
        self, user: User, product_id: str, question: str, use_ai: bool = True,
        filters: SearchFilters | None = None, context: AnswerContext | None = None,
        inquiry_mode: InquiryMode = "auto",
    ) -> AnswerResult:
        mode = resolve_inquiry_mode(question, inquiry_mode, context)
        if context is not None and mode == "troubleshooting" and resolved_report(question):
            actor = self.store.user(user.id)
            if not actor.active or "ask" not in actor.capabilities:
                raise AppError("질문 권한이 없습니다.", 403)
            return AnswerResult(
                status="resolved", mode="search", inquiry_mode=mode,
                consultation_state="resolved",
                message="사용자가 해결됨으로 표시했습니다. 상담을 마무리합니다.",
            )
        result = self._answer_dispatch(user, product_id, question, use_ai, filters, context)
        if mode == "troubleshooting":
            if (context is not None and retry_report(question)
                    and result.status in {"answered", "partial_answer"}):
                result = remove_repeated_steps(result, context)
            if result.status == "answered" and any(
                claim.presentation == "direct" for claim in result.claims
            ):
                result = result.model_copy(update={
                    "consultation_state": "active",
                    "followup_options": ["해결됨", "해결 안 됨", "이미 해봤음"],
                })
        return result.model_copy(update={"inquiry_mode": mode})

    def _answer_dispatch(
        self, user: User, product_id: str, question: str, use_ai: bool = True,
        filters: SearchFilters | None = None,
        context: AnswerContext | None = None,
    ) -> AnswerResult:
        """Answer a compound question one bounded topic at a time."""
        actor = self.store.user(user.id)
        if not actor.active or "ask" not in actor.capabilities:
            raise AppError("질문 권한이 없습니다.", 403)
        if not question.strip() or len(question) > 2000:
            raise AppError("질문은 1~2000자로 입력해 주세요.")
        topics = compound_topics(question)
        if service_question(question):
            return self._answer_single(user, product_id, question, use_ai, filters, context)
        catalog = {product.id for product in self.store.products()}
        if product_id not in catalog:
            raise AppError("등록된 제품을 선택해 주세요.")
        if model_mismatch(product_id, question, catalog=catalog):
            mismatch = evidence_gate(actor, product_id, question, [], catalog=catalog)
            if mismatch is not None:
                return mismatch
        if ambiguous_battery_life(question):
            return AnswerResult(
                status="clarification_required", mode="search",
                limitation_codes=["question_scope_unclear"],
                questions=["완전 충전 후 사용 시간과 배터리 수명 중 어느 항목인가요?"],
                message="배터리 사용 시간과 수명을 구분해 질문해 주세요.",
            )
        if (len(topics) > 3 or (not topics and explicit_compound(question))
                or (topics and (context is not None or dependent_topics(question)
                                or qualifiers_unclear(question)))):
            return AnswerResult(
                status="clarification_required", mode="search",
                limitation_codes=["question_scope_unclear"],
                message=("질문의 항목을 명확히 나누기 어렵습니다. "
                         "확인할 항목을 세 개 이내로 구체적으로 적어 주세요."),
            )
        if not topics:
            return self._answer_single(user, product_id, question, use_ai, filters, context)
        results = [self._answer_single(
            actor, product_id, topic.query, use_ai, filters, context
        ) for topic in topics[:3]]
        return self._merge_compound(actor, question, topics, results)

    def _merge_compound(
        self, actor: User, question: str, topics: list[Topic], results: list[AnswerResult]
    ) -> AnswerResult:
        """Merge bounded per-topic results while preserving every citation."""
        claims: list[Claim] = []
        evidence: list[Evidence] = []
        conflicts: list[Conflict] = []
        questions: list[str] = []
        limitations: list[str] = []
        missing: list[str] = []
        sections: list[AnswerTopic] = []
        for topic, result in zip(topics[:3], results, strict=True):
            ids: list[str] = []
            for claim in result.claims:
                if len(claims) >= 8:
                    missing.append(topic.label)
                    break
                claims.append(claim.model_copy(update={
                    "id": f"C{len(claims) + 1}",
                }))
                ids.append(claims[-1].id)
            topic_status = _compound_topic_status(result, [c for c in claims if c.id in ids])
            if topic_status != "answered":
                missing.append(topic.label)
            sections.append(AnswerTopic(
                label=topic.label,
                status=topic_status,
                claim_ids=ids,
            ))
            for source in result.evidence:
                if source.evidence_id not in {item.evidence_id for item in evidence}:
                    evidence.append(source)
            conflicts.extend(result.conflicts)
            questions.extend(result.questions)
            limitations.extend(result.limitation_codes)
        if len(topics) > 3:
            missing.extend(topic.label for topic in topics[3:])
        missing = list(dict.fromkeys(missing))
        limitations = list(dict.fromkeys(limitations))
        if missing:
            limitations.append("incomplete_evidence")
        complete = not missing and all(result.status == "answered" for result in results)
        has_direct = any(claim.presentation == "direct" for claim in claims)
        status = "answered" if complete else "partial_answer" if has_direct else "evidence_missing"
        versions = version_gate(question, evidence)
        if versions is not None:
            return current_result(self.store, actor, validity_notice(versions))
        message = ("각 항목을 별도로 확인했습니다." if complete else
                   "일부 항목은 추가 확인이 필요합니다. 항목별 상태와 근거를 확인해 주세요.")
        mode = "ai" if any(result.mode == "ai" for result in results) else "search"
        return current_result(self.store, actor, validity_notice(AnswerResult(
            status=status, mode=mode,
            claims=claims, conflicts=conflicts, questions=list(dict.fromkeys(questions)),
            limitation_codes=limitations, evidence=evidence,
            message=message, topics=sections,
            elapsed_seconds=sum(result.elapsed_seconds for result in results),
        )))

    def _answer_single(  # noqa: C901, PLR0911 - independent fail-closed answer paths
        self, user: User, product_id: str, question: str, use_ai: bool = True,
        filters: SearchFilters | None = None,
        context: AnswerContext | None = None,
    ) -> AnswerResult:
        started = time.monotonic()
        actor = self.store.user(user.id)
        if not actor.active or "ask" not in actor.capabilities:
            raise AppError("질문 권한이 없습니다.", 403)
        if not question.strip() or len(question) > 2000:
            raise AppError("질문은 1~2000자로 입력해 주세요.")
        catalog = {product.id for product in self.store.products()}
        if product_id not in catalog:
            raise AppError("등록된 제품을 선택해 주세요.")
        # Context only resolves the subject of the question. All answer claims must
        # still cite newly retrieved, eligible original evidence.
        answer_question = answer_question_for_context(question, context)
        conversation_context = contextual_question(question, context) if context else ""
        search_question = context_search_question(question, context) if context else question
        if model_mismatch(product_id, search_question, catalog=catalog):
            mismatch = evidence_gate(actor, product_id, search_question, [], catalog=catalog)
            if mismatch is not None:
                return mismatch.model_copy(update={"elapsed_seconds": time.monotonic() - started})
        search_question = focused_question(search_question) or search_question
        ranking_question, sources = self._retrieve(
            actor, product_id, search_question, use_ai, filters, context,
        )
        options = symptom_choices(question)
        detail_question = symptom_detail_question(question)
        if detail_question and sources and use_ai:
            result = clarification(sources, [], detail_question=detail_question)
            return current_result(self.store, actor, validity_notice(result)).model_copy(
                update={"elapsed_seconds": time.monotonic() - started},
            )
        if context is None and options and sources and use_ai:
            result = clarification(sources, options)
            return current_result(self.store, actor, validity_notice(result)).model_copy(
                update={"elapsed_seconds": time.monotonic() - started},
            )
        # Access, model and genuine conflict checks run before documented controls.
        gated = evidence_gate(
            actor, product_id, answer_question, sources, catalog=catalog,
        ) or audio_support(
            question if context is not None else search_question, sources,
            subject=context.turns[-1].question if context is not None else "",
        )
        if gated is not None:
            return current_result(self.store, actor, validity_notice(gated)).model_copy(
                update={"elapsed_seconds": time.monotonic() - started},
            )
        fallback = AnswerResult(
            status="search_results",
            mode="search",
            evidence=sources,
            message="관련 매뉴얼 원문을 찾았습니다. AI 요약을 생성하지 않은 검색 결과입니다.",
        )
        if not use_ai:
            return current_result(self.store, actor, validity_notice(fallback))
        reviewed = reviewed_section_answer(answer_question, fallback, self.settings.root)
        if reviewed is not None:
            reviewed = annotate_wavelength_conflicts(reviewed, sources)
            return current_result(self.store, actor, validity_notice(reviewed)).model_copy(
                update={"elapsed_seconds": time.monotonic() - started},
            )
        try:
            result = select_answer(
                answer_question, sources, self.ollama, self.settings.root,
                ranking_question=ranking_question, conversation_context=conversation_context,
            )
            result = explain_answer(
                answer_question, result, self.ollama, self.settings.root,
                conversation_context=conversation_context,
            )
            result = annotate_wavelength_conflicts(result, sources)
        except AppError as error:
            result = fallback.model_copy(
                update={"message": f"{error.detail} 관련 원문을 표시합니다."}
            )
        if (context is not None
                and resolve_inquiry_mode(question, "auto", context) == "troubleshooting"
                and result.status in {"evidence_missing", "search_results", "answered"}
                and not any(claim.presentation == "direct" for claim in result.claims)):
            # The repeated-step guard may still need a bounded observation from
            # the user. Keep the consultation open while asking for it; an
            # exhausted guide with no pending question remains exhausted.
            result = clarification(
                sources,
                [],
                repeated=True,
                exhausted=(
                    retry_report(question)
                    and not has_new_troubleshooting_detail(question, context)
                ),
            )
        return current_result(self.store, actor, validity_notice(result)).model_copy(
            update={"elapsed_seconds": time.monotonic() - started},
        )

    def _retrieve(
        self, actor: User, product_id: str, question: str, use_ai: bool,
        filters: SearchFilters | None, context: AnswerContext | None,
    ) -> tuple[str, list[Evidence]]:
        ranking_question = question
        if use_ai and re.search(r"[가-힣]", question) and any(
            doc.product_id == product_id and answer_eligible(doc)
            and matches_filters(doc, filters or SearchFilters())
            and (context is None or not context.document_ids or doc.id in context.document_ids)
            and "en" in normalized_language(doc.language).split("+")
            for doc in self.store.documents(actor)
        ):
            ranking_question = retrieval_query(question, self.ollama, self.settings.root)
        if getattr(self.search, "semantic", None) is not None:
            sources = self.search.search(
                actor, product_id, ranking_question, limit=12, filters=filters,
                document_ids=(set(context.document_ids)
                              if context is not None and context.document_ids else None),
            )
        elif context is not None and context.document_ids:
            sources = self.search.search(
                actor, product_id, ranking_question, filters=filters,
                document_ids=set(context.document_ids),
            )
        else:
            sources = (
                self.search.search(actor, product_id, ranking_question, filters=filters)
                if filters is not None else self.search.search(actor, product_id, ranking_question)
            )
        return ranking_question, self._reviewed_pages(
            actor, product_id, question, sources, filters, context,
        )

    def _reviewed_pages(
        self, actor: User, product_id: str, question: str, sources: list[Evidence],
        filters: SearchFilters | None, context: AnswerContext | None,
    ) -> list[Evidence]:
        """Supplement retrieval with reviewed sections, through current access filters.

        Retain ordinary results so genuine contradictory instructions still reach
        the existing conflict gates. A registry reference grants no document access.
        """
        wanted = candidate_pages(question, self.settings.root)
        present = {(source.document_id, source.physical_page) for source in sources}
        missing = wanted - present
        if not missing:
            return sources
        selected_filters = filters or SearchFilters()
        request_id = uuid.uuid4().hex[:16]
        additions = [
            page_evidence(doc, page, f"{request_id}:R{index}")
            for index, (doc, page) in enumerate(
                self.store.searchable_pages(actor, product_id), 1,
            )
            if (doc.id, page.number) in missing
            and doc.product_id == product_id and answer_eligible(doc)
            and (doc.kind != "service" or "read_service" in actor.capabilities)
            and matches_filters(doc, selected_filters)
            and (context is None or not context.document_ids or doc.id in context.document_ids)
        ]
        return [*sources, *additions]
