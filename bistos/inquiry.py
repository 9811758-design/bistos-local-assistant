"""Server-owned inquiry routing and bounded symptom clarification.

These prompts collect observations, never assert a diagnosis or invent a repair.
"""

import re
from difflib import SequenceMatcher
from typing import Literal

from bistos.models import AnswerContext, AnswerResult, Evidence
from bistos.question_intent import focused_question, question_intent

InquiryMode = Literal["auto", "product", "troubleshooting"]


def inquiry_mode(question: str, selected: InquiryMode = "auto",
                 context: AnswerContext | None = None) -> Literal["product", "troubleshooting"]:
    intent = question_intent(question)
    if intent in {"troubleshooting", "service"} or re.search(
        r"(?<![A-Za-z])(?:NO PAPER|DOOR OPEN|unresponsive|ERR[A-Za-z0-9]*)(?![A-Za-z])",
        question, re.IGNORECASE,
    ):
        return "troubleshooting"
    if intent in {"principle", "spec", "usage"}:
        return "product"
    if context is not None and context.inquiry_mode == "troubleshooting":
        return "troubleshooting"
    return "troubleshooting" if selected == "troubleshooting" else "product"


def resolved_report(question: str) -> bool:
    return bool(re.fullmatch(
        r"(?:해결\s*(?:됨|됐어|됐어요|되었습니다)|정상\s*작동해요|resolved|it\s+works\s+now)[.!\s]*",
        question.strip(), re.IGNORECASE,
    ))


def retry_report(question: str) -> bool:
    return bool(re.search(
        (r"해결\s*안|(?:그건|다|전부|모두)?\s*해봤(?:어|어요|습니다|음)?|"
         r"이미\s*(?:해|했|확인)|그래도|계속\s*안\s*(?:돼|됨|됩니다)|안\s*됐|"
         r"\bstill\b(?:\s+(?:no\s+\w+|not\s+\w+))?|"
         r"\balready\s+tried\b|\bno\s+improvement\b|"
         r"\bnot\s+resolved\b|\bnext\s+step\b"),
        question, re.IGNORECASE,
    ))


_RETRY_DETAIL_FILLERS = frozenset({
    "계속", "여전히", "아직", "그래도", "이미", "다시", "그건", "다", "전부", "모두",
    "해결", "안", "돼", "안돼", "않", "됐", "됨", "안됨", "안돼요", "안됩니다", "됩니다",
    "해봤어", "해봤어요",
    "해봤습니다", "해봤음", "still", "no", "not", "working", "again", "tried",
    "improvement", "resolved",
})

_DETAIL_SUBJECT_ALIASES: tuple[tuple[str, str], ...] = (
    (r"소리|sound|audio|음량|볼륨|volume", "audio"),
    (r"터치|touch(?:screen)?", "touch"),
    (r"프린터|printer|용지|paper", "printer"),
    (r"배터리|battery", "battery"),
    (r"화면|screen|display", "screen"),
    (r"전원|power", "power"),
    (r"측정값|reading|measurement", "measurement"),
)


def _detail_tokens(text: str) -> set[str]:
    normalized = text.casefold()
    for pattern, subject in _DETAIL_SUBJECT_ALIASES:
        normalized = re.sub(pattern, f" {subject} ", normalized, flags=re.IGNORECASE)
    return {
        token for token in re.findall(r"[0-9a-z가-힣]{2,}", normalized)
        if token not in _RETRY_DETAIL_FILLERS
    }


def has_new_troubleshooting_detail(question: str, context: AnswerContext) -> bool:
    """Detect a new symptom/detail without treating retry filler as new evidence."""
    previous = " ".join([
        context.initial_question,
        *(turn.question for turn in context.turns),
        *(claim for turn in context.turns for claim in turn.claims),
        *(item for turn in context.turns for item in turn.questions),
        *context.attempted_claims,
    ]).casefold()
    previous_tokens = _detail_tokens(previous)
    current_tokens = _detail_tokens(question)
    return bool(current_tokens - previous_tokens)


def symptom_choices(question: str) -> list[str]:
    text = focused_question(question).strip()
    if question_intent(text) != "troubleshooting":
        return []
    if (re.search(r"거나|또는|\bor\b", text, re.IGNORECASE)
            and re.search(r"켜지지|안\s*켜|not\s+power", text, re.IGNORECASE)
            and re.search(r"값|수치|reading|measurement", text, re.IGNORECASE)):
        return ["전원이 켜지지 않아요", "측정값이 표시되지 않음"]
    if re.search(r"프린터|인쇄|printer|printing|출력\s*(?:이|가|은|는)?\s*(?:안|않|못|되지)",
                 text, re.IGNORECASE) and not re.search(
        r"용지|종이|덮개|도어|흐리|빈\s*|paper|door|blank|faint|걸려|jam", text, re.IGNORECASE,
    ):
        return ["프린터 용지가 나오지 않아요", "용지는 나오지만 인쇄가 안 돼요",
                "프린터에 오류 문구가 표시돼요"]
    compact = re.sub(r"[\s.!?]", "", text)
    generic = "".join((  # noqa: FLY002
        r"(?:(?:제품|기기|장비)(?:가|이)?|BT-?\d+[A-Z]*)?",
        r"(?:작동(?:이)?|동작(?:이)?)?(?:안돼|안되요|안됩니다|안됨|먹통|고장)",
    ))
    if re.fullmatch(
        generic,
        compact, re.IGNORECASE,
    ) or re.fullmatch(r"(?:it\s+is\s+)?not\s+working[.!?]*", text, re.IGNORECASE):
        return ["전원이 켜지지 않아요", "전원은 켜지지만 화면에 이상이 있어요",
                "오류 문구가 표시돼요", "특정 기능만 작동하지 않아요"]
    return []


def symptom_detail_question(question: str) -> str:
    paper_pattern = r"(?:종이|용지).{0,12}(?:안\s*나|나오지\s*않|출력되지|배출되지)|"
    paper_pattern += r"paper.{0,20}(?:not\s+(?:feed|com)|doesn.t\s+(?:feed|come))"
    paper_not_feeding = re.search(paper_pattern, question, re.IGNORECASE)
    no_error_pattern = r"(?:오류|에러|코드|문구).{0,12}(?:없|안\s*떠|표시되지\s*않)|"
    no_error_pattern += r"no\s+error|no\s+(?:message|code)|"
    no_error_pattern += r"(?:error|code|message).{0,12}not\s+(?:shown|displayed)"
    no_error_displayed = re.search(no_error_pattern, question, re.IGNORECASE)
    if (paper_not_feeding and not no_error_displayed
            and not re.search(r"NO PAPER|DOOR OPEN", question, re.IGNORECASE)):
        prompt = "화면에 오류 문구나 코드가 표시되나요? 표시되면 그대로 입력해 주세요. "
        prompt += "표시가 없으면 '오류 표시 없음'과 함께 용지가 들어 있는지, "
        prompt += "덮개가 닫혔는지 알려주세요."
        return prompt
    if question.strip() in {
        "프린터에 오류 문구가 표시돼요", "오류 문구가 표시돼요",
    }:
        return "화면에 표시된 오류 문구나 코드를 그대로 입력해 주세요."
    if question.strip() == "특정 기능만 작동하지 않아요":
        return "작동하지 않는 기능과 화면에서 보이는 증상을 알려주세요."
    return ""


def clarification(sources: list[Evidence], options: list[str], *, repeated: bool = False,
                  exhausted: bool = False, detail_question: str = "") -> AnswerResult:
    question = detail_question or (
        "현재 화면의 오류 문구와 이미 확인한 내용을 알려주세요." if repeated
        else "어떤 증상인지 선택하거나 화면의 오류 문구를 입력해 주세요."
    )
    return AnswerResult(
        status="clarification_required", mode="search", evidence=sources,
        questions=[question], limitation_codes=["symptom_confirmation_required"],
        inquiry_mode="troubleshooting", followup_options=options,
        consultation_state="exhausted" if exhausted else "active",
        message=("현재 근거에서 추가 조치를 확인하지 못했습니다. 같은 조치는 반복하지 않겠습니다."
                 if exhausted else "증상을 확인해 관련 해결 방법을 찾겠습니다."),
    )


def remove_repeated_steps(result: AnswerResult, context: AnswerContext) -> AnswerResult:
    """Never present a near-identical prior instruction as a newly found next step."""
    previous = context.attempted_claims or [text for turn in context.turns for text in turn.claims]
    def normalize(text: str) -> str:
        return re.sub(r"[\W_]", "", text.casefold())

    def sentences(text: str) -> list[str]:
        return [part.strip() for part in re.split(
            r"(?:\n+|(?<=[.!?。！？]))\s*",  # noqa: RUF001
            text,
        ) if part.strip()]

    def repeats_prior(text: str, prior: str) -> bool:
        current = normalize(text)
        earlier = normalize(prior)
        if current == earlier or SequenceMatcher(None, current, earlier).ratio() >= 0.72:
            return True
        # A retry model often paraphrases and reorders the same procedure into
        # several sentences. Compare sentence-level coverage so that a longer
        # wrapper cannot bypass the exact-step guard.
        parts = sentences(text)
        if len(parts) < 2:
            return False
        prior_parts = sentences(prior)
        covered = sum(
            len(part) for part in parts
            if max(
                (SequenceMatcher(None, normalize(part), normalize(other)).ratio()
                 for other in prior_parts),
                default=0.0,
            ) >= 0.67
        )
        if covered / max(sum(map(len, parts)), 1) >= 0.60:
            return True
        def words(value: str) -> set[str]:
            return set(re.findall(r"[\w가-힣]+", value.casefold()))
        current_words = words(text)
        prior_words = words(prior)
        overlap = current_words & prior_words
        return (
            len(overlap) / max(len(current_words | prior_words), 1) >= 0.45
            and len(overlap) / max(len(prior_words), 1) >= 0.60
        )

    direct_claims = [claim for claim in result.claims if claim.presentation == "direct"]
    claims = [claim for claim in direct_claims if not any(
        repeats_prior(claim.text, prior)
        for prior in previous
    )]
    if claims:
        return result.model_copy(update={"claims": claims})
    # If every grounded candidate repeats a prior instruction, there is no
    # new safe step to present. Keep the evidence for a factual follow-up,
    # but mark the troubleshooting branch exhausted to stop retry loops.
    return clarification(result.evidence, [], repeated=True,
                         exhausted=bool(direct_claims))

