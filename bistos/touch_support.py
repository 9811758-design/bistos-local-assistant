"""Grounded navigation for touch symptoms, without prescribing service work."""

import re

from bistos.models import AnswerResult, Claim, Evidence
from bistos.product_identity import model_mismatch
from bistos.question_intent import focused_question

_TOUCH = r"터치|touch\s*(?:screen)?"
_TOUCH_INTERACTION = rf"(?:{_TOUCH}|화면[^.!?\n]{{0,12}}(?:눌|터치|반응))"
_SYMPTOM = (
    r"안\s*(?:돼|되|됨|먹)|먹통|반응|어긋|위치|다른\s*(?:곳|위치)|보정|교정|잠금|잠겨|눌러도|"
    r"정상적이지|(?:작동|동작|반응)하지\s*않|"
    r"calibrat|unresponsive|not\s+(?:work|respond)|doesn.t\s+(?:work|respond)|"
    r"misalign|inaccurate|locked|no\s+response"
)
_OTHER_SERVICE = r"ECG|NIBP|센서|혈압|분해|수리|교체|펌웨어|repair|replace|disassembl|firmware"


def touch_symptom(question: str) -> bool:
    focused = focused_question(question)
    # A remaining explicit location fault can qualify a normal response clause.
    if (focused != question and not re.search(_TOUCH, focused, re.IGNORECASE)
            and not re.search(r"위치.{0,12}어긋|misalign", focused, re.IGNORECASE)):
        return False
    return bool(
        re.search(_TOUCH_INTERACTION, question, re.IGNORECASE)
        and re.search(_SYMPTOM, question, re.IGNORECASE)
        and not re.search(_OTHER_SERVICE, question, re.IGNORECASE)
        and not re.search(r"음량|소리|volume|sound", question, re.IGNORECASE)
    )


def _sections(text: str) -> list[tuple[str, str]]:
    headings = list(re.finditer(r"(?m)^\s*(\d+(?:\.\d+){1,3})[ \t]+([^\n]{2,85})", text))
    return [
        (
            " ".join(match.group(0).split()),
            text[match.end():
                 headings[index + 1].start() if index + 1 < len(headings) else len(text)],
        )
        for index, match in enumerate(headings)
        if not re.search(r"\.{3}|…|\s\d+\s*$", match.group(2))
    ]


def calibration_references(question: str, sources: list[Evidence]) -> list[Claim]:
    """Report an actual section's existence, never its password or procedure."""
    if not touch_symptom(question):
        return []
    claims: list[Claim] = []
    seen: set[str] = set()
    for source in sources:
        if source.kind != "service" or source.document_id in seen:
            continue
        for heading, body in _sections(source.text):
            if not (
                re.search(_TOUCH, heading, re.IGNORECASE)
                and re.search(r"보정|교정|calibrat", heading, re.IGNORECASE)
                and len(body.strip()) >= 80
                and re.search(r"터치|touch", body, re.IGNORECASE)
                and not model_mismatch(source.product_id, heading + "\n" + body)
            ):
                continue
            claims.append(Claim(
                id=f"C{len(claims) + 1}",
                text=(
                    "터치 보정 항목이 있습니다. 현재 증상에 적용할 수 있는지는 "
                    "서비스 담당자가 확인해야 합니다. "
                    "보정으로 해결되는지는 확정할 수 없습니다."
                ),
                citation_ids=[source.evidence_id],
            ))
            seen.add(source.document_id)
            break
        if len(claims) == 2:
            break
    merged: dict[str, Claim] = {}
    for claim in claims:
        existing = merged.get(claim.text)
        if existing is None:
            merged[claim.text] = claim
            continue
        citation_ids = list(dict.fromkeys([*existing.citation_ids, *claim.citation_ids]))
        merged[claim.text] = existing.model_copy(update={"citation_ids": citation_ids})
    return [
        claim.model_copy(update={"id": f"C{index}"})
        for index, claim in enumerate(merged.values(), 1)
    ]


def touch_support(question: str, sources: list[Evidence]) -> AnswerResult | None:
    """Only state directly documented controls and ask which symptom is present."""
    if not touch_symptom(question):
        return None
    references = calibration_references(question, sources)
    controls: list[Claim] = []
    for source in sources:
        if source.kind not in {"user", "quick"}:
            continue
        for heading, body in _sections(source.text):
            if (
                "노브" in heading
                and re.search(r"회전\s*[:\uff1a].{0,40}커서", body)
                and re.search(r"누름\s*[:\uff1a].{0,40}메뉴\s*선택", body)
                and not model_mismatch(source.product_id, heading + body)
            ):
                controls.append(Claim(
                    id="C1",
                    text=(
                        "조절 노브를 돌리면 커서를 옮기고, 누르면 메뉴 선택 또는 명령을 "
                        "실행할 수 있습니다. 이 조작으로 터치 고장이 해결되는지는 "
                        "확인되지 않았습니다."
                    ),
                    citation_ids=[source.evidence_id],
                ))
                break
        if controls:
            break
    if not controls and not references:
        return None
    claims = [
        claim.model_copy(update={"id": f"C{index}", "presentation": "direct"})
        for index, claim in enumerate([*controls, *references], 1)
    ]
    specific = bool(re.search(
        r"위치.{0,12}어긋|다른\s*(?:곳|위치)|전혀.{0,12}반응|반응.{0,8}없|misalign|no\s+response",
        question, re.IGNORECASE,
    ))
    knob_reported = bool(re.search(
        r"(?:노브|물리\s*버튼|knob).{0,20}(?:정상|반응|작동|동작|work|respond)",
        question, re.IGNORECASE,
    ))
    questions: list[str] = []
    if not specific:
        questions.append("터치가 전혀 반응하지 않나요, 아니면 누른 곳과 다른 위치가 선택되나요?")
    if not knob_reported:
        questions.append("조절 노브와 물리 버튼은 반응하나요? 화면 잠금 표시도 알려 주세요.")
    confirmation_message = (
        "관련 근거를 찾았습니다. 알려주신 증상 구분을 반영했습니다. "
        "원인은 아직 확인되지 않았습니다. "
        "아래는 선택한 문서에서 확인되는 근거입니다."
    ) if specific else ""
    return AnswerResult(
        status="service_required" if references and (specific or not controls)
        else "clarification_required",
        mode="search", claims=claims, evidence=[
            source for source in sources
            if any(source.evidence_id in claim.citation_ids for claim in claims)
        ],
        limitation_codes=["symptom_confirmation_required"] + (
            ["service_review_required"] if references else []
        ),
        message=confirmation_message or (
            ("관련 근거를 찾았습니다. 터치가 전혀 반응하지 않는 경우와 누른 위치가 어긋나는 "
             "경우를 구분해야 합니다. 서비스 보정은 자동 실행 절차로 안내하지 않습니다.")
            if references else
            ("선택한 사용자 매뉴얼에서 확인되는 조작 방법입니다. "
             "이 근거만으로 터치 불량의 원인이나 해결 방법은 확정하지 않았습니다.")
        ),
        questions=questions,
    )
