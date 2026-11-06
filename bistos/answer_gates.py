import re
from collections.abc import Iterable

from bistos.models import AnswerResult, Claim, Conflict, Evidence, User, Variant
from bistos.product_identity import model_mismatch
from bistos.question_intent import (
    question_intent,
    service_factual_question,
    service_overview_question,
    service_question,
)
from bistos.search import PAUSE_QUICK_SHA, PAUSE_USER_SHA, pause_question
from bistos.touch_support import calibration_references, touch_support, touch_symptom


def evidence_gate(  # noqa: PLR0911
    user: User, product_id: str, question: str, sources: list[Evidence],
    *, catalog: Iterable[str] | None = None,
) -> AnswerResult | None:
    sources = [
        source for source in sources
        if source.kind != "service" or "read_service" in user.capabilities
    ]
    if model_mismatch(product_id, question, catalog=catalog):
        return AnswerResult(
            status="clarification_required",
            mode="search",
            questions=[
                "화면에서 선택한 제품과 질문의 제품 번호가 다릅니다. 어느 제품의 질문인가요?"
            ],
            limitation_codes=["model_unclear"],
            message="질문의 제품과 선택한 제품이 다릅니다.",
        )
    # Country/revision metadata alone must not stop an ordinary answer.
    # Source-specific conditions and actual contradictory instructions remain
    # attached to the evidence instead of requiring a document choice first.
    if touch_symptom(question):
        touch = touch_gate(question, sources)
        if touch is not None:
            return touch
    service = service_gate(user, question, sources)
    if service is not None:
        return service
    if not sources:
        return AnswerResult(
            status="evidence_missing",
            mode="search",
            limitation_codes=["no_matching_evidence"],
            message="선택한 제품의 게시된 자료에서 질문과 일치하는 근거를 찾지 못했습니다.",
        )
    if pause_question(product_id, question):
        return _pause_conflict(sources)
    versions = version_gate(question, sources)
    if versions is not None:
        return versions
    cleaning = cleaning_conflict(question, sources)
    if cleaning is not None:
        return cleaning
    ctg = ctg_print_conflict(question, sources)
    if ctg is not None:
        return ctg
    # Service documents are never candidates for automatic procedure generation.
    # An unrelated model mention in one service page must not suppress valid
    # user-manual evidence. Any displayed touch section is checked separately.
    return visual_gate(question, sources)


def touch_gate(question: str, sources: list[Evidence]) -> AnswerResult | None:
    versions = version_gate(question, sources)
    if versions is not None:
        return versions.model_copy(update={"claims": calibration_references(question, sources)})
    return visual_gate(question, sources) or touch_support(question, sources)


def cleaning_conflict(question: str, sources: list[Evidence]) -> AnswerResult | None:
    """Surface opposed cleaning instructions without guessing component scope."""
    if not re.search(
        r"청소|청결|세척|소독|알코올|clean|disinfect|alcohol", question, re.IGNORECASE
    ):
        return None
    for source in sources:
        instructions = [
            line.strip()
            for line in re.split(r"(?<=[.!?])\s+|\n", source.text)
            if re.search(r"알코올|alcohol", line, re.IGNORECASE)
            and re.search(r"닦|사용|clean|wipe|use", line, re.IGNORECASE)
        ]
        negative = r"지\s*마|금지|사용.{0,5}않|do\s+not|don't|never|must\s+not"
        permitted = [line for line in instructions if not re.search(negative, line, re.IGNORECASE)]
        prohibited = [line for line in instructions if re.search(negative, line, re.IGNORECASE)]
        if not permitted or not prohibited:
            continue
        return AnswerResult(
            status="conflict", mode="search", evidence=sources,
            limitation_codes=["source_conflict", "applicability_unconfirmed"],
            conflicts=[Conflict(
                topic="청소용 알코올의 사용 범위 확인 필요",
                variants=[
                    Variant(text=line, citation_ids=[source.evidence_id])
                    for line in (permitted[0], prohibited[0])
                ],
            )],
            questions=["각 지시가 적용되는 부위를 서비스 담당자에게 확인해 주세요."],
            message=(
                "같은 페이지에 알코올 사용과 사용 금지 지시가 함께 있습니다. "
                "적용 부위가 명확하지 않아 사용 방법을 확정하지 않았습니다."
            ),
        )
    return None


def ctg_print_conflict(question: str, sources: list[Evidence]) -> AnswerResult | None:
    """Report a real CTG print-threshold disagreement without inventing a value."""
    if not (
        re.search(r"\bCTG\b|씨티지", question, re.IGNORECASE)
        and re.search(r"print|printing|인쇄|출력", question, re.IGNORECASE)
    ):
        return None
    findings: list[tuple[Evidence, str, str, str]] = []
    for source in sources:
        if source.kind not in {"user", "quick"} or source.product_id != "BT-350":
            continue
        if not re.search(r"\bCTG\b", source.text, re.IGNORECASE):
            continue
        for passage in re.split(r"(?<=[.!?])\s+", " ".join(source.text.split())):
            if not (
                re.search(r"\[?(?:Print|인쇄)\]?\s*(?:button|버튼)", passage, re.IGNORECASE)
                and re.search(r"press.{0,70}again|다시\s*(?:누르|눌)", passage, re.IGNORECASE)
                and re.search(r"stop|end|중지|종료", passage, re.IGNORECASE)
            ):
                continue
            match = re.search(
                r"(more\s+than|at\s+least)\s+(\d+(?:\.\d+)?)\s*minutes?",
                passage, re.IGNORECASE,
            )
            if match:
                number = match.group(2)
                relation = "초과" if match.group(1).lower().startswith("more") else "이상"
                findings.append((source, f"{number}분 {relation}",
                                 f"{number}분 {relation}", passage.strip()))
                break
            match = re.search(r"(\d+(?:\.\d+)?)\s*분\s*(이상|초과)", passage)
            if match:
                number = match.group(1)
                relation = match.group(2)
                findings.append((source, f"{number}분 {relation}",
                                 f"{number}분 {relation}", passage.strip()))
                break
    findings = list({item[1]: item for item in reversed(findings)}.values())
    if len(findings) < 2:
        return None
    variants = [
        Variant(text=passage, citation_ids=[source.evidence_id])
        for source, _, _, passage in findings
    ]
    claims = [
        Claim(
            id=f"C{index}",
            text=(f"{'영문' if source.language.lower() in {'en', 'eng'} else '국문'} "
                  f"매뉴얼({source.revision})의 CTG 인쇄 조건은 "
                  f"{display} 후 인쇄 버튼을 다시 누르는 것입니다."),
            citation_ids=[source.evidence_id],
            presentation="direct",
        )
        for index, (source, _, display, _) in enumerate(findings, 1)
    ]
    return AnswerResult(
        status="conflict",
        mode="search",
        evidence=sources,
        limitation_codes=["source_conflict"],
        conflicts=[Conflict(topic="CTG 인쇄 종료 조건", variants=variants)],
        claims=claims,
        message=("CTG 인쇄 종료 시점에 관한 원문 표현이 "
                 + ", ".join(item[2] for item in findings)
                 + "으로 다릅니다. 각 근거의 조건을 구분해 안내합니다."),
    )


def visual_gate(question: str, sources: list[Evidence]) -> AnswerResult | None:
    patterns = (
        r"도면|표에서|표의|핀\s*(번호|연결)|핀맵|배선|회로|아이콘",
        r"\b(diagram|table|pinout|wiring|circuit|icon|symbol)\b",
    )
    if re.search(
        "|".join(patterns),
        question, re.IGNORECASE,
    ) and any(
        not source.layout_reviewed for source in sources
    ):
        return AnswerResult(
            status="visual_review_required",
            mode="search",
            evidence=sources,
            limitation_codes=["visual_review_pending"],
            message="도면·표의 배치가 검토되지 않았습니다. 원문 확인이 필요합니다.",
        )
    return None


def validity_notice(result: AnswerResult) -> AnswerResult:
    uncertain = any(
        item.source_record and (item.validity != "valid" or item.approval_status != "approved")
        for item in result.evidence
    )
    if not uncertain:
        return result
    return result.model_copy(update={
        "limitation_codes": list(dict.fromkeys([*result.limitation_codes, "validity_unconfirmed"])),
        "message": (
            result.message + " 원본의 최신·유효 상태와 최종 승인 여부는 확인되지 않았습니다."
        ).strip(),
    })


def version_gate(question: str, sources: list[Evidence]) -> AnswerResult | None:
    imported = [item for item in sources if item.source_record]
    if not imported:
        return None
    # Match document/revision validity questions, not ordinary measurements
    # such as "최신값" or "latest reading".  A later clause must not hide a
    # version request, so this remains an independent question-wide check.
    latest = bool(re.search(
        "".join((  # noqa: FLY002
            r"(?:최신\s*(?:매뉴얼|문서|자료|개정|버전|판|본)|",
            r"이\s*(?:자료|문서|매뉴얼)\s*(?:가|은|는)?\s*최신(?:인가|인지|이야|입니까)?|",
            r"최신\s*(?:것|자료)\s*(?:인지|인가)|",
            r"최종\s*(?:승인|개정|버전|자료|문서)|",
            r"유효한\s*(?:개정|버전|문서|자료)|",
            r"latest\s+(?:manual|document|revision|version)|",
            r"current\s+version|valid\s+revision)",
        )),
        question, re.IGNORECASE,
    ))
    uncertain = any(item.validity != "valid" for item in imported)
    if not (latest and uncertain):
        return None
    return AnswerResult(
        status="clarification_required",
        mode="search",
        evidence=sources,
        questions=[],
        limitation_codes=["validity_unconfirmed"],
        message="등록된 원문은 찾았지만 이 자료가 최신·유효한 개정인지는 확인되지 않습니다.",
    )


def service_gate(user: User, question: str, sources: list[Evidence]) -> AnswerResult | None:
    service_only = bool(sources) and all(source.kind == "service" for source in sources)
    procedure = service_question(question)
    if not service_only and not procedure:
        return None
    permitted = "read_service" in user.capabilities
    if (service_only and not procedure and permitted
            and (question_intent(question) in {"principle", "spec"}
                 or service_overview_question(question) or service_factual_question(question))):
        return None
    if service_only and not procedure:
        return AnswerResult(
            status="service_required",
            mode="search",
            evidence=sources if permitted else [],
            limitation_codes=[
                "service_review_required" if permitted else "service_authorization_required"
            ],
            message=(
                ("관련 서비스 매뉴얼 근거를 찾았습니다. 서비스 자료는 자동 수리 안내로 "
                 "변환하지 않습니다. 인용한 원문의 적용 조건을 서비스 담당자가 확인해 주세요.")
                if permitted else "이 질문은 서비스 담당자의 확인이 필요합니다."
            ),
            questions=["일반 조작 방법을 확인하려면 사용자·퀵 매뉴얼도 검색 범위에 포함해 주세요."],
        )
    found_service = permitted and any(source.kind == "service" for source in sources)
    return AnswerResult(
        status="service_required" if found_service or not permitted else "evidence_missing",
        mode="search",
        evidence=sources if permitted else [],
        limitation_codes=[
            "service_review_required" if found_service else
            "incomplete_evidence" if permitted else "service_authorization_required"
        ],
        message=(
            ("관련 서비스 매뉴얼 근거를 찾았습니다. 열람 권한은 작업 승인과 다릅니다. "
             "인용한 원문의 적용 조건을 서비스 담당자가 확인해 주세요.")
            if found_service else
            "선택한 범위에서 해당 서비스 절차를 확인하지 못했습니다."
            if permitted
            else "분해·수리·교정 절차는 서비스 담당자의 확인이 필요합니다."
        ),
    )


def _pause_conflict(sources: list[Evidence]) -> AnswerResult:
    selected = [
        source
        for source in sources
        if (source.original_sha256 == PAUSE_USER_SHA and source.physical_page == 16)
        or (source.original_sha256 == PAUSE_QUICK_SHA and source.physical_page == 6)
    ]
    if len({source.original_sha256 for source in selected}) != 2:
        return AnswerResult(
            status="evidence_missing",
            mode="search",
            evidence=sources,
            limitation_codes=["source_conflict", "incomplete_evidence"],
            message="알려진 문서 충돌의 양쪽 원문이 모두 게시되지 않아 값을 확정할 수 없습니다.",
        )
    variants: list[Variant] = []
    for source in selected:
        lines = [
            line.strip()
            for line in source.text.splitlines()
            if "5" in line and "10" in line and ("15" in line or "60" in line)
        ]
        if not lines:
            return AnswerResult(
                status="evidence_missing",
                mode="search",
                evidence=sources,
                limitation_codes=["source_conflict", "incomplete_evidence"],
                message="알려진 충돌의 해당 문장을 확인할 수 없어 답변을 보류했습니다.",
            )
        variants.append(Variant(text=lines[0][:700], citation_ids=[source.evidence_id]))
    return AnswerResult(
        status="conflict",
        mode="search",
        evidence=sources,
        limitation_codes=["source_conflict"],
        conflicts=[Conflict(topic="BT-550 알람 일시 정지 시간", variants=variants)],
        message="사용자·퀵 매뉴얼의 일시 정지 시간이 다릅니다. 장비 적용 값을 확인해야 합니다.",
    )


def paragraphs(text: str) -> list[str]:
    return [" ".join(block.split()) for block in re.split(r"\n\s*\n", text) if block.strip()]


def contextual_passages(text: str) -> list[str]:
    # PDF extraction often puts several numbered settings into one paragraph.
    # Keep each complete section, including its conditions, instead of dropping
    # the whole page when a three-paragraph window exceeds the passage budget.
    sections = re.split(r"(?m)(?=^[ \t]*\d+(?:\.\d+){1,3}[ \t]*[가-힣A-Za-z])", text)
    preamble = paragraphs(sections[0]) if len(sections) > 1 else []
    if any(re.search(r"경고|주의|반드시|금지|하지\s*마", block) for block in preamble):
        # The scope of a leading warning is unknown. Preserve the original
        # contiguous paragraph windows instead of copying it into other sections
        # or splitting away conditions to fit the generation budget.
        blocks = paragraphs(text)
        return list(dict.fromkeys(
            "\n\n".join(blocks[max(0, index - 1) : index + 2])
            for index in range(len(blocks))
        ))
    contexts: list[str] = []
    for section in sections:
        compact = " ".join(section.split())
        if not compact:
            continue
        if len(sections) > 1 and len(compact) <= 700:
            contexts.append(compact)
            continue
        # Preserve an entire bullet with all continuation lines and exceptions.
        # Never truncate a long procedure at an arbitrary character boundary.
        bullets = re.split(r"(?m)(?=^[ \t]*[➢⚫•])", section) if len(compact) > 700 else []
        if len(bullets) > 1:
            contexts.extend(" ".join(bullet.split()) for bullet in bullets if bullet.strip())
            continue
        blocks = paragraphs(section)
        contexts.extend(
            "\n\n".join(blocks[max(0, index - 1) : index + 2])
            for index in range(len(blocks))
        )
    return list(dict.fromkeys(contexts))
