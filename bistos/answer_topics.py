"""Bounded decomposition of independent manual topics, not arbitrary prose."""

import re
from dataclasses import dataclass

from bistos.question_intent import (
    focused_question,
    installation_clearance_question,
    service_question,
)


@dataclass(frozen=True)
class Topic:
    label: str
    query: str


TOPICS = (
    ("작동 원리",
     r"(?:작동|동작|측정)\s*원리|(?:operating|operation|measurement)\s*principle", "작동 원리"),
    ("인쇄 속도", r"(?:인쇄|출력)\s*속도|(?:print|printing|paper)\s*speed", "인쇄 속도"),
    ("경보 음량", r"(?:경보|알람)\s*(?:음량|볼륨)|alarm\s*volume", "경보 음량 설정 방법"),
    ("화면 밝기", r"(?:화면|스크린)\s*밝기|(?:screen|display)\s*brightness", "화면 밝기 설정 방법"),
    ("배터리 사용 시간",
     r"배터리\s*(?:사용|동작|작동)\s*시간|battery\s*(?:runtime|operating\s*time|duration)",
     "배터리 사용 시간"),
    ("작동 온도", r"(?:작동|사용|동작|운전)\s*온도|operating\s*temperature", "작동 온도 범위"),
    ("보관 온도", r"(?:보관|저장)\s*온도|storage\s*temperature", "보관 온도 범위"),
    ("공기 온도", r"공기\s*온도|air\s*temperature", "공기 온도 설정 범위"),
    ("피부 온도", r"피부\s*온도|skin\s*temperature", "피부 온도 설정 범위"),
    ("터치 증상", r"터치|touch\s*(?:screen)?", "터치가 안돼. 확인할 항목은?"),
    ("경보 소리", r"(?:알람|경보)\s*(?:소리|음(?!량))|alarm\s*(?:sound|audio)",
     "경보 소리가 안 나와. 경보 음량 설정에서 확인할 항목은?"),
)


def connected_condition_question(question: str) -> bool:
    """Recognize one conditional chain, rather than independent topics.

    A question can ask for two conditions that belong to the same feature,
    such as the condition that displays an indicator and the condition that
    follows it.  Those questions should stay intact for retrieval/model
    answering even when the feature names are not in ``TOPICS``.
    """
    has_condition = re.search(
        r"조건|경우|때|\b(?:if|when|while)\b",
        question, re.IGNORECASE,
    )
    has_link = re.search(
        r"그\s*뒤|그\s*후|이후|그러면|따라서|\b(?:then|after|as\s+a\s+result)\b",
        question,
        re.IGNORECASE,
    )
    return bool(has_condition and has_link)


def explicit_compound(question: str) -> bool:
    if connected_condition_question(question):
        return False
    if shared_relation_question(question):
        return False
    # A component and its material are columns of one relation, not two
    # independent device topics. Do not reject these table questions upstream.
    if re.search(r"부품|외장|enclosure|component", question, re.IGNORECASE) and re.search(
        r"재질|소재|material|각각\s*다른\s*(?:접촉\s*)?부품", question, re.IGNORECASE,
    ):
        return False
    return bool(re.search(r"각각|각\s*항목|둘\s*다|구분해|respectively", question, re.IGNORECASE)
                and re.search(r"(?<!결)과\s|와\s|\s및\s|그리고|,|\band\b",
                              question, re.IGNORECASE))


def shared_relation_question(question: str) -> bool:
    """One inventory or responsibility relation is not an unrelated topic list."""
    if any(re.search(pattern, question, re.IGNORECASE) for _, pattern, _ in TOPICS):
        return False
    if re.search(r"가격|보증|원리|price|warranty|principle", question, re.IGNORECASE):
        return False
    inventory = re.search(
        r"구성품|출고|포장|박스|동봉|packing|package|accessories",
        question, re.IGNORECASE,
    )
    quantity = re.search(
        r"몇\s*개|개수|수량|quantity|count|how\s+many",
        question, re.IGNORECASE,
    )
    personnel = re.search(
        r"사용자|일반인|user|operator",
        question, re.IGNORECASE,
    )
    service_role = re.search(
        r"서비스\s*담당|기술자|service\s*(?:personnel|engineer)",
        question, re.IGNORECASE,
    )
    responsibility = re.search(
        r"범위|담당|누가|scope|responsib|who",
        question, re.IGNORECASE,
    )
    adapter = re.search(
        r"어댑터|아답터|adaptor|adapter",
        question, re.IGNORECASE,
    )
    power_pair = (re.search(r"(?<![a-z])AC(?![a-z])", question, re.IGNORECASE)
                  and re.search(r"(?<![a-z])DC(?![a-z])", question, re.IGNORECASE))
    rating = re.search(
        r"정격|입력|출력|rating|input|output",
        question, re.IGNORECASE,
    )
    field_limits = (
        re.search(r"이름|성명|first\s*name|last\s*name|surname", question, re.IGNORECASE)
        and re.search(r"글자|문자|characters?|letters?", question, re.IGNORECASE)
        and re.search(r"입력|칸|길이|한도|몇|fields?|length|limit|how\s+many",
                      question, re.IGNORECASE)
    )
    clearance = installation_clearance_question(question)
    return bool((inventory and quantity) or (personnel and service_role and responsibility)
                or (adapter and power_pair and rating) or field_limits or clearance)


def shared_setting_comparison(question: str) -> bool:
    """Keep same-setting comparisons intact for one-pass evidence retrieval.

    These are relational questions (for example, choosing between two work
    modes), not a request to answer unrelated manual topics independently.
    The guard is deliberately generic: it requires an explicit setting/mode,
    a comparison marker, and an ``each/both/distinguish`` request.
    """
    has_setting = re.search(r"모드|설정|mode|setting", question, re.IGNORECASE)
    has_comparison = re.search(
        r"각각|각\s*항목|둘\s*다|구분해|(?:과|와|및|그리고|vs\.?|versus|or|and)",
        question, re.IGNORECASE,
    )
    return bool(has_setting and has_comparison and re.search(
        r"무엇|어떤|골라|선택|차이|구분|which|choose|difference|respectively",
        question, re.IGNORECASE,
    ))


def ambiguous_battery_life(question: str) -> bool:
    if re.search(r"배터리", question) and re.search(r"얼마나.{0,8}(?:가|오래)|수명", question):
        return not re.search(
            r"충전|사용\s*시간|동작\s*시간|작동\s*시간|몇\s*(?:년|시간)|교체\s*주기", question,
        )
    return bool(re.search(r"battery\s+life\b", question, re.IGNORECASE)
                and not re.search(r"runtime|operating\s*time|lifespan|service\s*life|years?|hours?",
                                  question, re.IGNORECASE))


def dependent_topics(question: str) -> bool:
    """Do not turn a conditional relationship into unrelated specifications."""
    return bool(re.search(r"때|경우|조건|\b(?:when|while|if|with)\b", question, re.IGNORECASE))


def qualifiers_unclear(question: str) -> bool:
    matches = [list(re.finditer(pattern, question, re.IGNORECASE)) for _, pattern, _ in TOPICS]
    if any(len(items) > 1 for items in matches):
        return True
    starts = sorted(items[0].start() for items in matches if items)
    if len(starts) < 2:
        return False
    qualifier = r"\d|끄|음소거|mute|silent|최대|최소|maximum|minimum"
    first = question[starts[0]:starts[1]]
    later = question[starts[1]:]
    return bool(re.search(qualifier, later, re.IGNORECASE)
                and not re.search(qualifier, first, re.IGNORECASE))


def compound_topics(question: str) -> list[Topic]:
    text = focused_question(question)
    if service_question(text) or re.search(r"왜|이유|차이|\bwhy\b|difference", text, re.IGNORECASE):
        return []
    text = re.sub(r"(작동|사용|동작|운전)(과|와)\s*(보관|저장)\s*온도",
                  r"\1 온도\2 \3 온도", text)
    text = re.sub(r"공기(과|와)\s*피부\s*온도", r"공기 온도\1 피부 온도", text)
    found: list[tuple[int, Topic]] = []
    for label, pattern, default_query in TOPICS:
        query = default_query
        match = re.search(pattern, text, re.IGNORECASE)
        if match is None:
            continue
        if label in {"터치 증상", "경보 소리"} and not re.search(
            r"안\s*(?:돼|되|나)|먹통|반응|어긋|not\s+(?:work|respond)|no\s+sound", text,
            re.IGNORECASE,
        ):
            continue
        if label == "터치 증상" and re.search(r"위치.{0,10}어긋|misalign", text, re.IGNORECASE):
            query = "터치 위치가 어긋나. 확인할 항목은?"
        if label in {"공기 온도", "피부 온도"} and re.search(r"방법|how", text, re.IGNORECASE):
            query = label + " 설정 방법"
        found.append((match.start(), Topic(label, query)))
    prefix = re.split(r"각각|각 항목|둘 다|구분해|respectively", text, maxsplit=1)[0]
    fragments = re.split(r"(?:과|와)\s+|\s+(?:및|그리고|and)\s+|[,;]", prefix)
    if explicit_compound(question) and len(fragments) > len(found):
        if shared_setting_comparison(question):
            return [Topic("공통 설정 비교", text)]
        return []
    found.sort(key=lambda item: item[0])
    ordered: list[Topic] = []
    for index, (start, topic) in enumerate(found):
        end = found[index + 1][0] if index + 1 < len(found) else len(text)
        # Keep local actions/numbers/qualifiers, e.g. mute versus normal volume.
        fragment = text[0 if index == 0 else start:end].strip()
        ordered.append(Topic(topic.label, topic.query + "\n질문 조건: " + fragment))
    return ordered if len(ordered) >= 2 else []
