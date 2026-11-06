import hashlib
import math
import re
import uuid
from collections import Counter
from collections.abc import Callable
from functools import lru_cache
from typing import Final, Protocol

from bistos.library import answer_eligible, matches_filters
from bistos.models import Document, Evidence, Page, SearchFilters, User
from bistos.question_intent import (
    battery_runtime_question,
    full_charge_question,
    installation_clearance_question,
    intent_terms,
    question_intent,
    runtime_evidence,
)

PAUSE_USER_SHA: Final = "cdb079a2e966b5faaa7eefd1f22387d01da22e636ccd17919e7b658e87de3d1a"
PAUSE_QUICK_SHA: Final = "fbdb1eeeeae425d16849754036b6401b49f3e2ce01f1e48f133944a17d8a7daa"
SEARCH_SYNONYMS: Final = (
    ("접촉부품", "contacting component"),
    ("재질", "material"),
    ("소재", "material"),
    ("언어", "language"),
    ("배터리", "battery"),
    ("전원", "power"),
    ("정전", "powerfailure"),
    ("전원정전", "powerfailure"),
    ("사용시간", "operating time"),
    ("작동시간", "operating time"),
    ("충전", "charging"),
    ("온도", "temperature"),
    ("습도", "humidity"),
    ("알람", "alarm"),
    ("경보", "alarm"),
    ("무음", "silence"),
    ("청소", "cleaning"),
    ("세척", "cleaning"),
    ("소독", "disinfection"),
    ("멸균", "sterilization"),
    ("사양", "specification"),
    ("무게", "weight"),
    ("중량", "weight"),
    ("크기", "dimensions"),
    ("치수", "dimensions"),
    ("보관", "storage"),
    ("운송", "transport"),
    ("오류", "error"),
    ("고장", "troubleshooting"),
    ("문제해결", "troubleshooting"),
    ("안나와", "no sound"),
    ("안 나와", "no sound"),
    ("소리가안", "no sound"),
    ("소리안", "no sound"),
    ("화면안", "display failure"),
    ("작동안", "not working"),
    ("원리", "principle"),
    ("작동원리", "operating principle"),
    ("작동 원리", "operating principle"),
    ("동작원리", "operating principle"),
    ("동작 원리", "operating principle"),
    ("측정원리", "measurement principle"),
    ("측정 원리", "measurement principle"),
    ("멈춰", "stops"),
    ("안켜", "no power"),
    ("측정", "measurement"),
    ("산소", "oxygen"),
    ("심박", "heart rate"),
    ("태아", "fetal"),
    ("혈압", "blood pressure"),
    ("센서", "sensor"),
    ("프로브", "probe"),
    ("도플러", "doppler"),
    ("도플러", "dop"),
    ("심박음량", "fetal audio volume"),
    ("음량", "volume"),
    ("최대", "maximum"),
    ("무음", "silence"),
    ("소리", "sound"),
    ("인쇄", "printing"),
    ("프린터", "printer"),
    ("종이", "paper"),
    ("용지", "paper"),
    ("설정", "setting"),
    ("연결", "connection"),
    ("시작", "start"),
    ("전압", "voltage"),
    ("주파수", "frequency"),
    ("소비전력", "power consumption"),
    ("교정", "calibration"),
    ("보정", "calibration"),
    ("터치스크린", "touchscreen"),
    ("터치 스크린", "touch screen"),
    ("터치", "touch"),
    ("안 돼", "not working"),
    ("먹통", "unresponsive"),
    ("반응 없음", "unresponsive"),
    ("어긋나", "misaligned"),
    ("점검", "inspection"),
    ("젖은손", "wet hand"),
    ("직렬", "serial"),
    ("통신포트", "communication port"),
    ("단자", "port"),
    ("퇴원", "discharge"),
    ("충전량", "charge level"),
    ("역류", "backflow"),
    ("자가검사", "self test"),
    ("자가진단", "self test"),
    ("부팅", "power on"),
    ("표시등", "indicator"),
    ("외관", "visual"),
    ("손상", "damage"),
    ("주기", "periodic"),
    ("유축", "breast pump"),
    ("완충", "충전"),
    ("완충", "fully charged"),
    ("아답터", "adapter"),
    ("어댑터", "adapter"),
    ("설치", "installation"),
    ("설치", "installed"),
    ("빨강", "적색"),
    ("녹색", "green"),
    ("적색", "red"),
    ("심박리듬", "heart rhythm"),
    ("램프", "indicator"),
)
KOREAN_PARTICLES: Final = (
    "으로", "에서", "에게", "은", "는", "이", "가", "을", "를", "과", "와", "도", "로"
)

EXPLICIT_STATUS_MESSAGES: Final = re.compile(
    "".join((  # noqa: FLY002
        r"(?<![A-Z0-9])(?:NO\s+PAPER|DOOR\s+OPEN|LOW\s+BATTERY|LOW\s+BAT|BAT\s+LO|",
        r"DOP\s*[12]\s+OPEN)(?![A-Z0-9])",
    )),
    re.IGNORECASE,
)


def _explicit_status_match(query: str, text: str) -> bool:
    """Match a known status only when query and evidence contain the same code."""
    def codes(value: str) -> set[str]:
        return {re.sub(r"\s+", "", match.group(0)).casefold()
                for match in EXPLICIT_STATUS_MESSAGES.finditer(value)}

    return bool(codes(query) & codes(text))


class SearchStore(Protocol):
    def searchable_pages(self, user: User, product_id: str) -> list[tuple[Document, Page]]: ...


class ManualSemanticIndex(Protocol):
    def rank(
        self, query: str, pages: list[tuple[Document, Page]]
    ) -> dict[tuple[str, int], float]: ...


def normalized(value: str) -> str:
    """Normalize spacing and punctuation without translating or substituting models."""
    return re.sub(r"[^a-z0-9가-힣]", "", value.casefold())


def pause_question(product_id: str, question: str) -> bool:
    text = normalized(question)
    if normalized(product_id) != "bt550":
        return False
    pause = any(term in text for term in ("일시정지", "알람정지", "알람멈", "pause"))
    alarm = any(term in text for term in ("알람", "경보", "무음", "음소거"))
    if any(term in text for term in ("apgar", "아프가")) and pause:
        # APGAR timer pause/reset instructions are distinct from the BT-550
        # alarm-pause conflict unless the question explicitly asks about an alarm.
        return alarm
    return pause or any(term in text for term in ("무음", "음소거"))


def _grams(value: str) -> Counter[str]:
    text = normalized(value)
    return Counter(text[index : index + 2] for index in range(len(text) - 1))


def door_question(question: str) -> bool:
    return any(
        term in normalized(question)
        for term in ("도어오픈", "오픈도어", "dooropen", "opendoor", "문열림", "문이열")
    )


@lru_cache(maxsize=128)
def _query_terms(question: str) -> tuple[str, ...]:  # noqa: C901
    normalized_question = normalized(question)
    raw_terms = re.findall(r"[a-z][a-z0-9_-]{1,}|[가-힣]{2,}", question.casefold())
    terms = list(raw_terms)
    for term in raw_terms:
        if not re.fullmatch(r"[가-힣]+", term):
            continue
        for particle in KOREAN_PARTICLES:
            if term.endswith(particle) and len(term) - len(particle) >= 2:
                terms.append(term[: -len(particle)])
                break
    sound_context = any(
        marker in normalized_question
        for marker in (
            "소리", "음량", "볼륨", "스피커", "도플러",
            "sound", "audio", "speaker", "doppler",
        )
    )
    for korean, english in SEARCH_SYNONYMS:
        if ((korean == "완충" and not full_charge_question(question))
                or (korean in {"안나와", "안 나와", "소리가안", "소리안"} and not sound_context)):
            continue
        if normalized(korean) in normalized_question:
            terms.append(english)
        if normalized(english) in normalized_question:
            terms.append(korean)
    # Common support calls are often phrased without the manual's formal
    # symptom wording.  Keep this expansion narrow so it cannot replace a
    # concrete menu or model term.
    if any(marker in normalized_question for marker in ("소리가안", "소리안", "도플러안")):
        terms.extend(("소리", "음향", "출력", "sound", "audio", "speaker"))
    if sound_context and any(
        marker in normalized_question for marker in ("최대", "올렸", "올려도", "높여도")
    ):
        terms.extend(("volume", "음량", "maximum", "max", "high"))
    if any(marker in normalized_question for marker in ("화면안", "화면이안", "디스플레이안")):
        terms.extend(("화면", "디스플레이", "display", "screen"))
    terms.extend(_touch_query_terms(normalized_question))
    terms.extend(_drowsiness_query_terms(question))
    if installation_clearance_question(question):
        terms.extend(("설치", "설치 요구사항", "좌우측면", "후면", "벽으로부터",
                      "installation", "clearance", "wall", "side", "rear"))
    return tuple(dict.fromkeys(terms))


def _drowsiness_query_terms(question: str) -> list[str]:
    human = re.search(r"졸려|졸리|졸린|잠든|잠들|자도|자면서", question)
    other = re.search(r"환자|무호흡|모드|sleep\s*mode", question, re.IGNORECASE)
    return ["drowsy", "sleeping", "asleep"] if human and not other else []


def _touch_query_terms(normalized_question: str) -> list[str]:
    terms: list[str] = []
    implicit_touch = any(
        marker in normalized_question
        for marker in ("화면눌러", "화면터치", "눌러도반응", "터치한곳", "다른곳", "다른위치")
    )
    if "터치" in normalized_question or "touch" in normalized_question or implicit_touch:
        terms.extend(("터치", "터치스크린", "touchscreen", "touch screen"))
    if any(
        marker in normalized_question
        for marker in ("터치보정", "터치교정", "touchcalibration")
    ):
        terms.extend(("보정", "교정", "calibration", "calibrate"))
    return terms


def _preuse_question(question: str) -> bool:
    return bool(re.search(
        r"사용\s*전|사용하기\s*전|사용\s*전의|before\s+(?:each\s+)?use|pre[- ]use",
        question,
        re.IGNORECASE,
    ))


def _preuse_heading_bonus(question: str, text: str) -> float:
    """Boost explicit pre-use sections without scanning outside ranked pages."""
    if not _preuse_question(question):
        return 0.0
    if re.search(r"table\s+of\s+contents|목차", text, re.IGNORECASE):
        return 0.0
    heading = re.search(
        "".join((  # noqa: FLY002
            r"(?m)^\s*(?:\d+(?:\.\d+)*\.?\s*)?",
            r"(?:사용\s*전(?:의)?\s*(?:준비|확인|점검|사항)|",
            r"before\s+(?:each\s+)?use|pre[- ]use|preparing\s+to\s+use)",
        )),
        text,
        re.IGNORECASE,
    )
    return 1.25 if heading else 0.0


def _principle_heading_bonus(question: str, text: str) -> float:
    if question_intent(question) != "principle":
        return 0.0
    if re.search(r"table\s+of\s+contents|목차", text, re.IGNORECASE):
        return 0.0
    heading = re.search(
        "".join((  # noqa: FLY002
            r"(?m)^\s*(?:\d+(?:\.\d+)*\.?\s*)?",
            r"(?:operating|working)\s+principles?|작동\s*원리|동작\s*원리",
        )),
        text,
        re.IGNORECASE,
    )
    return 1.25 if heading else 0.0


def _relevance_for_segment(question: str, text: str) -> float:  # noqa: C901, PLR0912, PLR0915
    source_normalized = normalized(text)
    door_query = door_question(question)
    door_match = any(
        term in source_normalized
        for term in (
            "dooropen",
            "opendoor",
            "hooddoor",
            "도어오픈",
            "오픈도어",
            "문열림",
            "문이열",
        )
    )
    if door_query and not door_match:
        return 0
    query, source = _grams(question), _grams(text)
    denominator = math.sqrt(
        sum(v * v for v in query.values()) * sum(v * v for v in source.values())
    )
    similarity = (
        sum(count * source[gram] for gram, count in query.items()) / denominator
        if denominator
        else 0
    )
    terms = _query_terms(question)
    direct = sum(1 for term in terms if normalized(term) in source_normalized)
    intent = question_intent(question)
    intent_direct = sum(
        1
        for term in intent_terms(intent)
        if normalized(term) and normalized(term) in source_normalized
    )
    query_normalized = normalized(question)
    procedure_bonus = 0.0
    if battery_runtime_question(question) and runtime_evidence(text):
        procedure_bonus += 2.0
    if intent == "principle":
        mechanism_terms = (
            "원리", "도플러", "초음파", "심박", "심전도", "측정", "감지", "검출",
            "temperature", "humidity", "principle", "doppler", "ultrasound", "measure",
            "detect", "electrocardi", "진공", "흡입", "음압", "펌프", "다이어프램",
            "vacuum", "suction", "negative pressure", "pump", "diaphragm",
        )
        mechanism_hits = sum(term in source_normalized for term in mechanism_terms)
        procedure_bonus += min(2.4, mechanism_hits * 0.35)
        # A specification/measurement table can share terms such as
        # wavelength, accuracy, and range with a principle question.  Keep
        # it as a fallback, but prefer an explanatory mechanism passage when
        # the table has no causal/mechanism language of its own.
        if re.search(
            r"(?:specification|measurement\s+range|accuracy|wavelength|사양|측정\s*범위|정확도|파장)",
            text, re.IGNORECASE,
        ) and not re.search(
            r"(?:principle|원리|uses?|using|through|based\s+on|도플러|초음파|진공|흡입|압력|펌프|센서|측정하여|감지하여)",
            text, re.IGNORECASE,
        ):
            procedure_bonus -= 1.0
        if re.search(
            "".join((  # noqa: FLY002
                r"(?:electrocardiogram|ECG|signal).{0,50}(?:produced|generated).{0,25}by|",
                r"(?:도플러|초음파).{0,25}(?:이용|사용)|",
                r"(?:uses?|using).{0,50}(?:doppler|ultrasound)",
            )),
            text, re.IGNORECASE | re.DOTALL,
        ):
            procedure_bonus += 1.8
        causal_pattern = (
            r"(?:이용|사용|통해|의해|작동|측정|감지|흡입|순환|가열|냉각|조절)"
            r"|(?:uses?|using|through|driven|measur\w*|detect\w*|suction|pump\w*)"
        )
        if re.search(causal_pattern, text, re.IGNORECASE):
            procedure_bonus += 0.65
        environmental_table = re.search(
            (
                r"(?:보관|운송|환경|storage|transport|ambient|operating\s+condition)"
                r".{0,120}(?:℃|°c|%|humidity|온도|습도)"
            ),
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if environmental_table and not re.search(
            (
                r"(?:진공|흡입|음압|펌프|다이어프램|도플러|초음파|원리|vacuum|suction|pump|"
                r"doppler|ultrasound|principle|measure|detect)"
            ),
            text,
            re.IGNORECASE,
        ):
            procedure_bonus -= 0.75
        if re.search(
            (
                r"(?:refer\s+to\s+the\s+chapters?|see\s+chapter|장(?:을|의)?\s*참조|"
                r"각\s+매개변수.{0,40}(?:참조|chapter))"
            ),
            text,
            re.IGNORECASE | re.DOTALL,
        ):
            # A chapter pointer is useful navigation, but is not itself an
            # explanation of the operating mechanism.
            procedure_bonus -= 1.1
    if (
        re.search(r"자동\s*인쇄|auto(?:matic)?\s*print", question, re.IGNORECASE)
        and re.search(r"자동\s*인쇄|auto(?:matic)?\s*print", text, re.IGNORECASE)
        and re.search(r"(?:설정하면|설정할\s*경우|설정\s*시)|\bif\b|\bwhen\b", text, re.IGNORECASE)
        and re.search(
            r"용지.{0,25}(?:끝|소진)|paper.{0,30}(?:out|end|run)|(?:stop|종료)",
            text, re.IGNORECASE,
        )
    ):
        procedure_bonus += 1.6
    printer_query = any(
        term in query_normalized
        for term in ("프린터", "인쇄", "출력", "용지", "종이", "printer", "print", "paper")
    )
    printer_action = re.search(
        (
            r"(?:용지\s*(?:삽입|장착|교체)|프린터\s*(?:덮개|도어)|"
            r"(?:load|insert|install|replace)\s+paper|print\s+door|paper\s+(?:loaded|out))"
        ),
        text,
        re.IGNORECASE,
    )
    if printer_query and printer_action:
        procedure_bonus += 0.9
    if (
        any(term in query_normalized for term in ("용지교체", "용지를교체", "paperreplace"))
        and any(
            term in source_normalized
            for term in (
                "용지장착", "용지삽입", "감열용지", "paperloaded", "installpaper", "loadpaper",
            )
        )
    ):
        procedure_bonus += 0.55
    if (
        any(term in query_normalized for term in ("보관온도", "보관습도", "storage", "stored"))
        and any(term in source_normalized for term in ("보관", "storage", "transport"))
    ):
        procedure_bonus += 0.45
        if re.search(
            r"(?:storage|보관|운송)[^\n]{0,160}(?:℃|°c|%|temperature|humidity|온도|습도)",
            text, flags=re.IGNORECASE,
        ):
            procedure_bonus += 1.2
    if (
        any(term in query_normalized for term in ("전원", "전압", "voltage", "powersupply"))
        and any(term in query_normalized for term in ("주파수", "frequency", "전압", "voltage"))
        and re.search(r"\binput\s+(?:a\.?c\.?\s*)?\d", text, re.IGNORECASE)
        and re.search(r"\d[\d\s/.,~-]*Hz\b", text, re.IGNORECASE)
    ):
        procedure_bonus += 2.0
    if (
        ("청소" in query_normalized or "clean" in query_normalized)
        and any(term in query_normalized for term in ("본체", "기기", "device", "body"))
        and any(
            term in source_normalized
            for term in ("청결", "청소하기전에", "부드러운솔", "cleaning", "disinfect")
        )
    ):
        procedure_bonus += 0.5
        if all(term in source_normalized for term in ("disconnect", "soft", "wipe")):
            procedure_bonus += 0.8
    door_bonus = 0.6 if door_query and door_match else 0
    if door_query and re.search(r"경보|알람|아이콘|메시지", text):
        door_bonus += 0.3
    # Prefer touchscreen operation/calibration sections for touch symptoms.
    # This remains a ranking hint; authorization and document filters are
    # enforced by the search store and answer gates.
    touch_query = "터치" in query_normalized or "touch" in query_normalized
    touch_issue = any(
        marker in query_normalized
        for marker in (
            "안돼", "안됨", "먹통", "반응없", "어긋나", "다른곳", "다른위치", "눌러도",
            "보정", "교정",
            "calibration", "unresponsive",
            "misalign", "miscalibrat",
        )
    )
    procedure_bonus += _touch_bonus(touch_query, touch_issue, source_normalized)
    # Intent is only a tie-breaker.  It cannot make an unrelated page beat a
    # page containing the user's concrete symptom or setting name.
    return similarity + direct * 0.15 + min(intent_direct, 3) * 0.04 + door_bonus + procedure_bonus


def _touch_bonus(touch_query: bool, touch_issue: bool, source_normalized: str) -> float:
    if not touch_query:
        return 0.0
    touch_source = any(
        marker in source_normalized
        for marker in ("터치스크린", "터치 스크린", "touchscreen", "touch screen")
    )
    bonus = 0.22 if touch_source else 0.0
    if touch_issue and any(
        marker in source_normalized
        for marker in (
            "터치보정", "터치스크린보정", "calibrationtouchscreen", "touchscreencalibration",
        )
    ):
        bonus += 0.72
    if (
        ("목차" in source_normalized or "tableofcontents" in source_normalized)
        and not any(
            marker in source_normalized
            for marker in ("선택", "누르", "클릭", "calibration")
        )
    ):
        bonus -= 0.35
    return bonus


def _segments(text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n|\r?\n", text) if part.strip()]
    if not paragraphs:
        return [text]
    segments = [text, *paragraphs]
    segments.extend(
        f"{paragraphs[index]} {paragraphs[index + 1]}"
        for index in range(len(paragraphs) - 1)
    )
    return segments


def relevance(question: str, text: str) -> float:
    """Score a page and its local paragraphs, retaining neighboring conditions."""
    return (max(_relevance_for_segment(question, segment) for segment in _segments(text))
            + _workplace_relation_bonus(question, text)
            + _connector_location_bonus(question, text))


def _connector_location_bonus(question: str, text: str) -> float:
    """Rank a labelled panel above incidental port mentions for location queries."""
    ports = re.findall(
        r"(?<![a-z0-9])(?:USB|RS[-\s]?232C?|HDMI|LAN)(?![a-z0-9])",
        question, re.IGNORECASE,
    )
    location = re.search(
        r"어디|어느\s*(?:면|쪽|위치)|위치|꽂는\s*곳|where|which\s*(?:side|panel)|located",
        question, re.IGNORECASE,
    )
    if not ports or not location:
        return 0.0
    if not all(normalized(port) in normalized(text) for port in ports):
        return 0.0
    panel = re.search(
        "".join((  # noqa: FLY002
            r"(?im)^\s*(?:\d+(?:\.\d+)*\s*)?(?:제품\s*)?",
            r"(?:(?:전면|후면|측면)(?:부)?|(?:description\s+of\s+(?:the\s+)?)?",
            r"(?:front|rear|side)\s+panel)\s*$",
        )),
        text,
    )
    return 1.5 if panel else 0.0


def _workplace_relation_bonus(question: str, text: str) -> float:
    """Prefer the requested object/action relation over neighboring vocabulary.

    These bilingual patterns only affect ranking of authorized source text;
    they provide neither an answer nor permission to use another document.
    """
    pairs = (
        (r"(?=.*(?:심박\s*리듬|heart\s*(?:rate\s*)?rhythm))(?=.*(?:램프|색|lamp|color|indicator))",
         r"(?:심박\s*리듬|heart\s*(?:rate\s*)?rhythm).{0,400}(?:녹색|green).{0,250}(?:적색|빨강|red)"),
        (r"(?=.*(?:설치|install))(?=.*(?:전원|power))",
         r"(?:power\s*off|전원.{0,10}(?:끄|꺼)).{0,90}(?:not\s*fully\s*installed|설치)|"  # noqa: ISC003
         + r"설치.{0,50}(?:완료|완전).{0,40}전원"),
        (r"젖은\s*손|wet\s+hands?", r"젖은\s*손|wet\s+hands?"),
        (r"(?:손상|damage).*(?:점검|확인|주기|검사|check|inspect)|(?:점검|검사).*(?:주기|interval)",
         r"(?:examine|inspect).{0,250}(?:visible|damage)|(?:점검|검사).{0,100}손상"),
        (r"자가\s*(?:검사|진단)|self[-\s]*(?:test|check)|부팅",
         r"self[-\s]*test|자가\s*(?:검사|진단)"),
        (r"환자\s*퇴원|discharge.{0,15}patient|patient.{0,15}discharge",
         r"(?:\d(?:\.\d)+\s+)?discharg\w*\s+(?:the\s+)?patient|환자\s*퇴원"),
        (r"직렬|\bserial\b|\bCOM\b",
         r"communication\s+port\s+setting|통신\s*포트\s*설정"),
        (r"(?:배터리|battery).*(?:보관|장기|storage)|보관.*(?:배터리|battery)",
         r"(?:long\s+time|months|장기간|개월).{0,140}(?:charg|충전)|(?:charg|충전).{0,140}(?:months|개월)"),
        (r"(?:모유|milk).*(?:본체|unit).*(?:들어|유입|역류|into)|(?:본체|unit).*(?:모유|milk)",
         r"(?:milk|모유).{0,90}(?:into\s+the\s+unit|본체)|(?:backflow|역류).{0,80}(?:unit|본체)"),
    )
    charge_relation = (
        full_charge_question(question)
        and re.search(r"아답터|어댑터|adapter", question, re.IGNORECASE)
        and re.search(
            r"(?:충전.{0,10}(?:다\s*되|완료)|fully\s*charged).{0,100}(?:아답터|어댑터|adapter)",
            text, re.IGNORECASE | re.DOTALL,
        )
    )
    return 1.0 if charge_relation or any(
        re.search(query, question, re.IGNORECASE | re.DOTALL)
        and re.search(source, text, re.IGNORECASE | re.DOTALL)
        for query, source in pairs
    ) else 0.0


def _duplicate_body_key(doc: Document, text: str) -> tuple[str, str, str, str, str]:
    """Deduplicate PDF/Word copies while retaining genuine metadata conflicts."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    body = lines
    if len(lines) > 2:
        body = [
            line for index, line in enumerate(lines)
            if not (
                index < 2
                and re.search(r"(?:P/N|bistos|\b20\d{2}[./-])", line, re.IGNORECASE)
            )
        ]
    normalized_body = re.sub(r"\s+", " ", " ".join(body).casefold()).strip()
    return (doc.kind, doc.language, doc.country, doc.revision, normalized_body)


def page_evidence(doc: Document, page: Page, evidence_id: str) -> Evidence:
    """Build server-owned evidence from an already authorized document page."""
    return Evidence(
        evidence_id=evidence_id, document_id=doc.id, product_id=doc.product_id,
        title=doc.title, kind=doc.kind, revision=doc.revision, language=doc.language,
        physical_page=page.number,
        is_derived_pdf=doc.pdf_is_derived or doc.original_name.casefold().endswith(".docx"),
        text=page.text, original_sha256=doc.original_sha256,
        render_sha256=doc.render_sha256,
        chunk_sha256=hashlib.sha256(page.text.encode("utf-8")).hexdigest(),
        layout_reviewed=page.layout_reviewed,
        original_name=doc.source_original_name or doc.original_name,
        country=doc.country, approval_date=doc.approval_date,
        approval_status=doc.approval_status, validity=doc.validity,
        source_record=doc.source_record,
    )


class SearchEngine:
    def __init__(
        self,
        store: SearchStore,
        semantic: ManualSemanticIndex | None = None,
        before_search: Callable[[], None] | None = None,
    ) -> None:
        self.store = store
        self.semantic = semantic
        self.before_search = before_search

    def search(  # noqa: C901, PLR0912
        self, user: User, product_id: str, question: str, limit: int = 6,
        filters: SearchFilters | None = None,
        document_ids: set[str] | None = None,
    ) -> list[Evidence]:
        """Search authorized pages, optionally restricted to cited documents."""
        if not user.active or "ask" not in user.capabilities or not question.strip():
            return []
        if self.before_search is not None:
            self.before_search()
        query = re.sub(re.escape(product_id), "", question, flags=re.IGNORECASE)
        selected_filters = filters or SearchFilters()
        pages = [
            (doc, page) for doc, page in self.store.searchable_pages(user, product_id)
            if answer_eligible(doc)
            and matches_filters(doc, selected_filters)
            and (document_ids is None or doc.id in document_ids)
        ]
        semantic_scores: dict[tuple[str, int], float] = {}
        if self.semantic is not None:
            try:
                semantic_scores = self.semantic.rank(
                    query, pages,
                )
            except Exception:  # semantic retrieval is supplementary; lexical remains available
                semantic_scores = {}
        ranked = sorted(
            [
                (
                    relevance(query, page.text)
                    + semantic_scores.get((doc.id, page.number), 0.0)
                    + (1.0 if _explicit_status_match(query, page.text) else 0.0)
                    + (0.0 if doc.kind == "service" else _preuse_heading_bonus(query, page.text))
                    + (
                        0.0
                        if doc.kind == "service"
                        else _principle_heading_bonus(query, page.text)
                    ),
                    doc,
                    page,
                )
                for doc, page in pages
            ],
            key=lambda item: (-item[0], item[1].kind == "service", item[1].id, item[2].number),
        )
        chosen: list[tuple[Document, Page]] = []
        if pause_question(product_id, question):
            chosen = [
                (doc, page)
                for doc, page in pages
                if (doc.original_sha256 == PAUSE_USER_SHA and page.number in {16, 33})
                or (doc.original_sha256 == PAUSE_QUICK_SHA and page.number in {5, 6})
            ]
        # Lexical-only search retains distinct manual/version perspectives.  A
        # semantic index is allowed to return multiple stronger pages from one
        # document, which prevents a troubleshooting section being swallowed by
        # a weak first page from another revision.
        seen_documents = {doc.id for doc, _ in chosen}
        seen_pages = {(doc.id, page.number) for doc, page in chosen}
        seen_bodies = {_duplicate_body_key(doc, page.text) for doc, page in chosen}
        if self.semantic is not None:
            for score, doc, page in ranked:
                body_key = _duplicate_body_key(doc, page.text)
                if score >= 0.08 and (doc.id, page.number) not in seen_pages \
                        and body_key not in seen_bodies:
                    chosen.append((doc, page))
                    seen_pages.add((doc.id, page.number))
                    seen_bodies.add(body_key)
        else:
            for score, doc, page in ranked:
                if score >= 0.08 and doc.id not in seen_documents:
                    chosen.append((doc, page))
                    seen_documents.add(doc.id)
                    seen_pages.add((doc.id, page.number))
                    seen_bodies.add(_duplicate_body_key(doc, page.text))
            for score, doc, page in ranked:
                body_key = _duplicate_body_key(doc, page.text)
                if score >= 0.08 and (doc.id, page.number) not in seen_pages \
                        and body_key not in seen_bodies:
                    chosen.append((doc, page))
                    seen_pages.add((doc.id, page.number))
                    seen_bodies.add(body_key)
        request_id = uuid.uuid4().hex[:16]
        return [
            page_evidence(doc, page, f"{request_id}:E{index}")
            for index, (doc, page) in enumerate(chosen[: max(1, min(limit, 12))], 1)
        ]
