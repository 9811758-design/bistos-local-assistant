"""Small, deterministic question intent hints used by local retrieval.

This module deliberately does not call a model.  Intent is only a ranking hint;
the evidence and safety gates remain authoritative.
"""

import re
from typing import Literal

QuestionIntent = Literal["spec", "usage", "troubleshooting", "service", "principle", "general"]


def full_charge_question(question: str) -> bool:
    """Do not confuse Korean battery completion with packaging cushioning."""
    text = re.sub(r"완충\s*(?:재|포장)|(?:충격|포장|운송).{0,8}완충", "", question)
    return bool(re.search(
        r"완충|충전.{0,10}(?:완료|끝|다\s*(?:되|됐|했))|fully\s*charg", text, re.IGNORECASE,
    ))

_SPEC = (
    "사양", "규격", "범위", "정확도", "용량", "무게", "크기", "전압", "주파수", "시간",
    "spec", "range", "accuracy", "voltage", "frequency", "weight", "dimension",
)
_USAGE = (
    "사용", "방법", "설정", "교체", "연결", "시작", "종료", "인쇄", "청소", "소독",
    "어떻게", "조작", "켜고", "켜는", "초점", "고정", "배치",
    "how", "setting", "replace", "connect", "clean", "turn on", "focus", "position", "secure",
)
_PRINCIPLE = (
    "원리", "작동원리", "작동 원리", "동작원리", "동작 원리", "측정원리", "측정 원리",
    "어떤 원리", "작동 방식", "동작 방식", "어떤 방식으로 작동", "어떤 방식으로 동작",
    "어떻게 작동", "어떻게 동작", "operation principle", "operating principle",
    "measurement principle", "how does", "how it works", "how the device works",
)
_TROUBLESHOOTING = (
    "켜지지 않", "표시되지 않", "작동하지 않", "동작하지 않", "안 켜",
    "오류", "고장", "문제", "안돼", "안됨", "안 돼", "않아", "안나", "안 나", "안켜", "멈춰",
    "먹통", "반응 없", "반응없", "어긋나", "눌러도", "다른 곳", "다른 위치", "터치한 곳",
    "경보", "알람", "소리가", "error", "fault", "trouble", "doesn't", "not working",
    "no sound", "alarm", "not responding", "doesn't respond", "misalign",
)
_SERVICE = (
    "수리", "교정", "보정", "분해", "납땜", "펌웨어", "서비스 매뉴얼", "repair", "calibration",
    "disassembl", "firmware", "service manual",
)


def focused_question(question: str) -> str:
    """Remove explicitly resolved/negated touch clauses, not genuine symptoms."""
    touch = r"터치(?:\s*스크린)?"
    normal = r"정상(?!\s*(?:은|이)?\s*(?:아니|않))(?=$|[\s,.!?]|인데|이지만|입니다|이고|임|이야)"
    clauses = (
        touch + r"(?:가|는|은|이)?\s*안\s*(?:되|돼)[^.!?\n]{0,15}?게\s*아니라",
        touch + r"\s*문제(?:가|는)?\s*아니라",
        touch + r"\s*위치(?:는|가)?\s*맞고\s*반응(?:도|은)?\s*" + normal,
        touch + r"(?:\s*(?:반응|동작|위치))?(?:가|는|은|이|도)?\s*" + normal,
        touch + r"\s*문제(?:는|가)?\s*해결(?:됐|되었)",
        r"touch\s*(?:screen)?\s+(?:is\s+)?working\s+(?:normally|properly|fine)",
        r"touch\s*(?:screen)?\s+(?:issue|problem)\s+(?:is\s+|has\s+been\s+)?resolved",
    )
    return re.sub(
        "(?:" + "|".join(clauses) + r")(?:인데|이지만|입니다|이고|지만|는데|어|다)?[,.!\s]*",
        " ", question, flags=re.IGNORECASE,
    ).strip()


def calibration_policy_question(question: str) -> bool:
    """Personnel/interval facts do not ask for the calibration procedure."""
    if not re.search(r"교정|\bcalibrat\w*", question, re.IGNORECASE):
        return False
    action = r"방법|절차|순서|어떻게|단계|버튼|메뉴|암호|수리|분해|납땜|펌웨어|"
    action += r"조절|조정|맞춰|열어|연결|분리|교체|쇼트|점퍼|단락|우회|(?:하는|할)\s*법|"
    action += r"눌러|누르|해\s*보려|실행|\bhow(?!\s+(?:often|frequently)\b)|"
    action += r"\b(?:how\s+(?:to|do|can|should|would)|steps?|procedure|buttons?|menu|password|"
    action += r"repair|disassembl\w*|"
    action += r"adjust\w*|connect\w*|disconnect\w*|replace\w*|open\w*|short|jumper|bypass)\b"
    if re.search(action, question, re.IGNORECASE):
        return False
    return bool(re.search(
        r"누가|자격|담당|주기|얼마나\s*자주|(?:사용자|일반인).{0,45}해도|"  # noqa: ISC003
        + r"\b(?:who|qualified|qualification|interval|how\s+often)\b|can\s+(?:the\s+)?user",
        question, re.IGNORECASE,
    ))


def service_question(question: str) -> bool:
    if calibration_policy_question(question):
        return False
    # Preserve attached action stems (기기분해, 혈압교정), excluding known homographs.
    # Asking who may repair is a qualification inquiry, not an instruction to
    # perform the repair. Remove only that phrase; other requested work remains.
    question = re.sub(
        "|".join((r"수리\s*(?:담당자|인력|기사|기술자)",  # noqa: FLY002
                  r"who\s+(?:is\s+qualified\s+to|can|may|should)\s+repair")),
        "담당자", question, flags=re.IGNORECASE,
    )
    patterns = (
        r"(?<!구)분해(?!능)|수리|교정|보정|펌웨어|납땜|회로",
        r"\b(?:calibrat\w*|firmware|disassembl\w*|repair\w*|solder\w*)\b",
        r"service\s*(?:procedure|manual)|서비스\s*(?:절차|매뉴얼)",
        r"(?:보드|PCB|전원부|히터|내부\s*부품|부품|터치\s*패널|LCD)\s*(?:을|를)?\s*교체",
        r"\breplace\w*\s+(?:the\s+)?(?:touch\s*panel|LCD|circuit\s*board)\b",
        r"쇼트|점퍼|(?:핀|단자|회로).{0,10}(?:단락|우회)",
        r"\b(?:short(?:ing)?|bridge)\s+(?:the\s+)?(?:pins?|connectors?|terminals?|circuit)\b",
        r"\bshort[-\s]+circuit|\b(?:jumper|bypass)\b",
    )
    return bool(re.search("|".join(patterns), question, re.IGNORECASE))


def service_overview_question(question: str) -> bool:
    """Factual fault classification/personnel scope, never an execution request."""
    return not service_question(question) and bool(re.search(
        "|".join((r"고장\s*분류|수리\s*(?:담당자|인력|기사|기술자)",  # noqa: FLY002
                  r"fault\s+classification|who\s+(?:is\s+qualified\s+to|can|may|should)\s+repair")),
        question, re.IGNORECASE,
    ))


def service_factual_question(question: str) -> bool:
    """Inventory counts and display timing are facts, not service work steps."""
    if service_question(question):
        return False
    if calibration_policy_question(question):
        return True
    inventory = re.search(
        r"구성품|출고|포장|박스|동봉|packing|package|accessories",
        question, re.IGNORECASE,
    )
    quantity = re.search(
        r"몇\s*개|개수|수량|quantity|count|how\s+many",
        question, re.IGNORECASE,
    )
    display = re.search(r"표시|메시지|display|message", question, re.IGNORECASE)
    timing = re.search(
        r"몇\s*초|걸리는\s*시간|how\s+(?:many\s+seconds|long)",
        question, re.IGNORECASE,
    )
    connector = re.search(
        r"단자|포트|USB|RS.?232|\b(?:connectors?|ports?)\b",
        question, re.IGNORECASE,
    )
    location = re.search(
        r"어느\s*면|위치|어디|where|location",
        question, re.IGNORECASE,
    )
    return bool((inventory and quantity) or (display and timing) or (connector and location))


def question_intent(question: str) -> QuestionIntent:  # noqa: PLR0911
    """Return a conservative intent hint, with service taking precedence."""
    text = focused_question(question).casefold()
    if service_question(text):
        return "service"
    # Alarm thresholds, timing and meaning are factual questions. The words
    # "alarm"/"경보" alone must not route them into symptom triage.
    alarm_failure = r"안\s*(?:울|나)|울리지|계속\s*울|오작동|고장|문제|"
    alarm_failure += r"not\s+(?:working|sounding)"
    if (re.search(r"경보|알람|alarm|baby\s*check", text, re.IGNORECASE)
            and re.search(r"언제|무엇|무슨|어떻게|몇|조건|의미|뜻|when|what|how|meaning",
                          text, re.IGNORECASE)
            and not re.search(alarm_failure, text, re.IGNORECASE)):
        return "general"
    silent_audio = re.search(
        "|".join((r"\b(?:audio|sound|speaker)\s+(?:is\s+|remains\s+)?silent\b",  # noqa: FLY002
                  r"\b(?:cannot|can't)\s+hear\b")), text,
    )
    if silent_audio or any(term in text for term in _TROUBLESHOOTING):
        return "troubleshooting"
    if any(term in text for term in _SPEC):
        return "spec"
    if any(term in text for term in _PRINCIPLE):
        return "principle"
    if any(term in text for term in _USAGE):
        return "usage"
    return "general"


def battery_runtime_question(question: str) -> bool:
    # Tests, lifespan and replacement need their full procedures/conditions.
    if re.search(
        r"시험|검사|테스트|점검|평가|수명|교체\s*주기|"  # noqa: ISC003
        + r"\b(?:test|check|inspect|evaluate)\w*\b|lifespan|service\s*life",
        question, re.IGNORECASE,
    ):
        return False
    battery = re.search(r"배터리|건전지|battery", question, re.IGNORECASE)
    charged = re.search(r"완충|꽉\s*채|충전.{0,8}(?:후|뒤)|fully\s*charg|after.{0,10}charg",
                        question, re.IGNORECASE)
    if not battery and (not charged or re.search(
        r"콘덴서|축전기|제세동|충전\s*회로|완충재|포장|capacitor|defibrillat|charging\s*circuit|packaging",
        question, re.IGNORECASE,
    )):
        return False
    explicit = re.search(
        r"(?:사용|동작|작동)\s*시간|연속\s*사용|runtime|operating\s*time|"  # noqa: ISC003
        + r"몇\s*(?:시간|분).{0,12}(?:쓰|쓸|사용|지속|버티)|"
        + r"(?:쓸|쓰는|사용할)\s*(?:수\s*있는\s*)?시간|"
        + r"how\s+many\s+hours.{0,30}(?:use|run|operat|last)",
        question, re.IGNORECASE,
    )
    lasting = re.search(r"얼마나.{0,15}(?:쓰|쓸|사용|지속|버티|가)|"  # noqa: ISC003
                       + r"how\s+long.{0,40}(?:last|run|operat|be\s+used)",
                       question, re.IGNORECASE)
    return bool(explicit or (charged and lasting))


def runtime_evidence(text: str) -> bool:
    parts = (
        r"(?:(?:동작|작동|사용|운전)\s*시간|operating\s*time|run\s*time)",
        r"[^\n]{0,65}\d[\d.\s~\-]*(?:hours?|hrs?|시간|분)",
    )
    inverse = r"\b(?:continuously|continuous)\s+\d+(?:\.\d+)?\s*(?:hours?|hrs?)\s+operat\w*"
    return bool(re.search("".join(parts), text, re.IGNORECASE)
                or re.search(inverse, text, re.IGNORECASE))


def installation_clearance_question(question: str) -> bool:
    """Recognize physical placement without confusing body walls or wallpaper."""
    if re.search(
        r"흉벽|복벽|심실벽|벽지|배경\s*화면|chest\s+wall|wallpaper", question, re.IGNORECASE,
    ):
        return False
    return bool(
        re.search(r"벽|wall", question, re.IGNORECASE)
        and re.search(
            r"설치|배치|놓|양옆|좌우|측면|후면|뒤|place|install|side|rear|back",
            question, re.IGNORECASE,
        )
        and re.search(
            r"간격|거리|공간|비워|띄|떨어|clearance|distance|space|away", question, re.IGNORECASE,
        )
    )


def intent_terms(intent: QuestionIntent) -> tuple[str, ...]:
    """Terms which provide a small ranking bonus for an intent."""
    return {
        "spec": _SPEC,
        "usage": _USAGE,
        "troubleshooting": _TROUBLESHOOTING,
        "service": _SERVICE,
        "principle": _PRINCIPLE,
        "general": (),
    }[intent]
