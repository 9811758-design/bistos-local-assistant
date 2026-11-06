"""Fail-closed, hash-bound reviewed source bundles."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from .models import AnswerResult, Claim, Evidence

TOPICS = frozenset(
    {
        "principle",
        "preuse",
        "ecg_connection",
        "ecg_display",
        "cuff_conditions",
        "flange_selection",
        "alarm_reference",
        "spo2_specs",
        "input_signal",
        "spo2_start",
        "transducer_test",
        "transducer_environment",
        "diagnosis_limit",
        "printer_setup",
        "battery_storage",
        "battery_performance",
        "battery_leakage",
        "resp_wave_gain",
        "washed_parts_storage",
        "phototherapy_manual_output",
        "eto_sterilization",
        "surface_cleaning",
        "clock_configuration",
        "enclosure_cleaning",
        "probe_preparation",
        "remote_nibp_control",
        "cms_connection_failure",
        "bed_reset",
        "single_user_use",
        "maternal_hr_contact",
        "cleaning_power_disconnect",
        "language_setting",
        "frequency_function",
        "temperature_unit_toggle",
        "electrode_connector_term",
        "pad_cover_requirement",
        "pad_wrapping_warning",
    }
)
_SHA = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


def _normalise(value: str) -> str:
    return " ".join(value.replace("\\r\\n", "\n").replace("\\n", "\n").split())


class ReviewedSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: StrictStr = Field(min_length=1, max_length=160)
    document_id: StrictStr = Field(min_length=1, max_length=160)
    render_sha256: StrictStr = Field(min_length=64, max_length=64)
    page: StrictInt = Field(gt=0, le=100000)
    page_sha256: StrictStr = Field(min_length=64, max_length=64)
    source_text: StrictStr = Field(min_length=1, max_length=20000)
    translation_ko: StrictStr = Field(min_length=1, max_length=6000)
    topic: StrictStr = Field(min_length=1, max_length=64)
    bundle: StrictStr = Field(min_length=1, max_length=160)
    reviewed: Literal[True]

    @field_validator("id", "document_id", "source_text", "translation_ko", "topic", "bundle")
    @classmethod
    def _text(cls, value: str) -> str:
        value = _normalise(value)
        if not value:
            raise ValueError("blank text")
        return value

    @field_validator("render_sha256", "page_sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        if not _SHA.fullmatch(value):
            raise ValueError("invalid SHA-256")
        return value.lower()

    @field_validator("topic")
    @classmethod
    def _topic(cls, value: str) -> str:
        if value not in TOPICS:
            raise ValueError("unknown topic")
        return value

    @field_validator("reviewed", mode="before")
    @classmethod
    def _reviewed(cls, value: object) -> Literal[True]:
        if value is not True or type(value) is not bool:
            raise ValueError("reviewed must be true")
        return True  # pyright: ignore[reportUnreachable]


def _load(root: Path) -> list[ReviewedSection]:  # noqa: PLR0911
    path = root / "config" / "reviewed-sections.json"
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            return []
        raw = cast("object", json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, UnicodeError):
        return []
    if isinstance(raw, dict):
        obj = cast("dict[object, object]", raw)
        raw = obj.get("records", obj.get("sections"))
    if not isinstance(raw, list):
        return []
    result: list[ReviewedSection] = []
    ids: set[str] = set()
    for item in cast("list[object]", raw):
        if not isinstance(item, dict):
            return []
        try:
            record = ReviewedSection.model_validate(item)
        except ValueError:
            return []
        if record.id in ids:
            return []
        ids.add(record.id)
        result.append(record)
    return result


def _topic_patterns() -> tuple[tuple[str, str], ...]:
    return (
        ("ecg_connection", r"(?:ecg|심전도).{0,100}(?:연결|요구사항|주의사항|connect|requirement)"),
        ("ecg_display", r"(?:ecg|심전도).{0,100}(?:표시|display|show)"),
        (
            "cuff_conditions",
            (
                r"(?:\bnibp\b|\bcuff\b|커프|비침습.*혈압).{0,100}"
                r"(?:준비|조건|시작|자세|prepare|condition|start|posture)"
            ),
        ),
        (
            "flange_selection",
            (
                r"(?:\bflange\b|\bfunnel\b|깔때기).{0,80}"
                r"(?:선택|크기|지름|select|siz)|유두.{0,40}(?:지름|측정)"
            ),
        ),
        ("alarm_reference", r"(?:알람|경보|alarm).{0,100}(?:어디|참조|where|reference)"),
        ("input_signal", r"입력\s*신호|input\s+signal"),
        ("spo2_specs", r"(?:sp\s*o?2|산소포화도).{0,100}(?:사양|범위|정확도|spec|accuracy|range)"),
        (
            "spo2_start",
            r"(?:sp\s*o?2|산소포화도).{0,100}(?:시작|순서|절차|start|procedure|sequence)",
        ),
        (
            "transducer_environment",
            (
                r"(?:transducer|트랜스듀서|변환기|probe|프로브).{0,80}"
                r"(?:환경|주변|주의|조심|precaution|environment)"
            ),
        ),
        (
            "transducer_test",
            r"(?:transducer|트랜스듀서|probe|프로브).{0,60}(?:시험|검사|test|두드|tap)",
        ),
        (
            "principle",
            (
                r"(?:작동|동작|측정|어떤|무슨)\s*원리|제품\s*원리|\bprinciple\b|how.{0,25}work|"
                r"(?:sp\s*o?2|산소포화도).{0,60}(?:계산|산출|calculat)"
            ),
        ),
        (
            "diagnosis_limit",
            (
                r"(?:diagnos(?:e|is)|진단).{0,80}(?:confirm|확정)|"
                r"(?:confirm|확정).{0,80}(?:diagnos(?:e|is)|진단)"
            ),
        ),
        (
            "printer_setup",
            (
                r"(?:용지|종이).{0,25}(?:나오|나와|배출).{0,40}"
                r"(?:인쇄|출력).{0,20}(?:안|않)|(?:인쇄|출력).{0,20}(?:백지|빈\s*종이)|"
                r"(?:blank\s*(?:paper|print|output)|(?:paper|printer).{0,50}"
                r"(?:comes?\s+out|feeds?).{0,40}print.{0,20}(?:not|no))"
            ),
        ),
    )


def _battery_topic(text: str) -> str:  # noqa: PLR0911 - fail-closed scope gates
    """Recognize only source-reviewable storage or performance-test requests."""
    if not re.search(r"배터리|건전지|battery|batteries", text, re.IGNORECASE):
        return ""
    if re.search(
        "".join((  # noqa: FLY002
        r"수리|교체|분해|repair|replace|disassembl|청소|세척|소독|clean|disinfect|",
        r"전압|용량|voltage|capacity|기록|삭제|record|delete|설정|setting|",
        r"몇\s*시간|몇\s*분|얼마나\s*(?:오래|가)|how\s+(?:long|many)|",
        r"런타임|runtime|작동\s*원리|principle|알람|경보|alarm|프린터|printer|",
        r"프로브|probe|커프|cuff|ECG|SpO2|센서|sensor|어댑터|아답터|adapter|adaptor|충전\s*완료|fully\s*charged",
        )),
        text, re.IGNORECASE,
    ):
        return ""
    leakage = re.search(r"누액|액\s*샘|액이\s*새|액체.{0,8}새|leak", text, re.IGNORECASE)
    if leakage:
        if re.search(r"충전|charg|성능|performance|보관|stor", text, re.IGNORECASE):
            return ""
        if re.search(
            r"사용|중지|접촉|주의|손|맨손|닦|묻|use|stop|contact|precaution|hand|wipe|touch",
            text, re.IGNORECASE,
        ):
            return "battery_leakage"
        return ""
    storage = re.search(
        "".join((  # noqa: FLY002
        r"장기|장기간|오래.{0,15}(?:안\s*쓰|사용하지)|보관|저장\s*장소|",
        r"stor(?:age|e)|long[- ](?:term|period)|unused|not\s+(?:be\s+)?used",
        )),
        text, re.IGNORECASE,
    )
    performance = re.search(r"성능|performance", text, re.IGNORECASE)
    testing = re.search(r"시험|테스트|검사|점검|test|check", text, re.IGNORECASE)
    if storage and not (performance or testing):
        return "battery_storage"
    if performance and testing and not storage:
        return "battery_performance"
    return ""



def _bounded_setting_or_storage_topic(text: str) -> str:
    for matches, topic in (
        (_pad_wrapping_warning_question, "pad_wrapping_warning"),
        (_pad_cover_requirement_question, "pad_cover_requirement"),
        (_electrode_connector_term_question, "electrode_connector_term"),
        (_language_setting_question, "language_setting"),
        (_frequency_function_question, "frequency_function"),
        (_temperature_unit_toggle_question, "temperature_unit_toggle"),
        (_maternal_hr_contact_question, "maternal_hr_contact"),
        (_cleaning_power_disconnect_question, "cleaning_power_disconnect"),
        (_bed_reset_question, "bed_reset"),
        (_single_user_question, "single_user_use"),
        (_cms_connection_failure_question, "cms_connection_failure"),
        (_remote_nibp_control_question, "remote_nibp_control"),
        (_surface_cleaning_question, "surface_cleaning"),
        (_clock_configuration_question, "clock_configuration"),
        (_enclosure_cleaning_question, "enclosure_cleaning"),
        (_probe_preparation_question, "probe_preparation"),
        (_eto_permission_question, "eto_sterilization"),
    ):
        if matches(text):
            return topic
    if (re.search(r"(?:manual|수동)\s*(?:mode|모드)", text, re.IGNORECASE)
            and re.search(r"출력|범위|단계|사양|output|range|level|spec", text, re.IGNORECASE)
            and not re.search(
                "".join((  # noqa: FLY002
                    r"baby|prewarm|아기|예열|온도|temperature|거리|distance|",
                    r"치료\s*시간|treatment\s*time|알람|alarm|타이머|timer|",
                    r"배터리|battery|청소|세척|clean|소독|disinfect|전체|all\s+spec",
                )),
                text, re.IGNORECASE,
            )):
        return "phototherapy_manual_output"
    if (re.search(r"resp|호흡", text, re.IGNORECASE)
            and re.search(r"wave\s*gain|gain|이득|파형.{0,12}높이", text, re.IGNORECASE)
            and not re.search(
                "".join((  # noqa: FLY002
                r"ecg|spo2|심전도|산소포화도|속도|speed|알람|경보|alarm|커프|cuff|",
                r"배터리|battery|수리|repair|교체|replace|분해|disassembl",
                )),
                text, re.IGNORECASE,
            )):
        return "resp_wave_gain"
    if (re.search(r"유축|breast\s*pump|깔때기|젖병|funnel|bottle", text, re.IGNORECASE)
            and re.search(r"부품|part|깔때기|젖병|funnel|bottle", text, re.IGNORECASE)
            and re.search(r"세척한|씻은|washed|cleaned|세척\s*후|after\s*(?:washing|cleaning)",
                          text, re.IGNORECASE)
            and re.search(r"젖|건조|보관|wet|damp|dry|stor", text, re.IGNORECASE)
            and not re.search(
                "".join((  # noqa: FLY002
                r"전체|전반|소독|멸균|삶|끓|세정제|세제|배터리|battery|냉장|냉동|refriger|freez|",
                r"모유를|우유|milk|내용물|채워|fill|",
                r"full|all\s+steps|disinfect|steriliz|boil|detergent|",
                r"(?:세척|씻|wash|clean).{0,8}(?:방법|순서|절차|method|procedure)|",
                r"how\s+(?:to|do\s+I|should\s+I)\s+(?:wash|clean)",
                )),
                text, re.IGNORECASE,
            )):
        return "washed_parts_storage"
    return ""


def _pad_wrapping_warning_question(text: str) -> bool:
    return bool(
        re.search(r"패드|커버|\bpad\b|cover", text, re.IGNORECASE)
        and re.search(r"담요|꽉|조여|조이|blanket|tight", text, re.IGNORECASE)
        and re.search(r"감싸|감아|덮|돼|될까|되나|wrap|can|allow", text, re.IGNORECASE)
        and not re.search(
            r"세척|소독|교체|빛\s*세기|무게|체중|온도\s*설정|몇\s*도|"  # noqa: ISC003
            + r"wash|clean|disinfect|replace|intensity|weight|temperature\s*set|\d",
            text, re.IGNORECASE,
        )
    )


def _pad_cover_requirement_question(text: str) -> bool:
    return bool(
        re.search(r"패드|\bpad\b", text, re.IGNORECASE)
        and re.search(r"일회용\s*커버|disposable\s*cover", text, re.IGNORECASE)
        and re.search(r"반드시|필수|씌워야|없이|must|required|without|necessary",
                      text, re.IGNORECASE)
        and not re.search(
            r"교체|시간|오염|더러|재사용|세척|소독|담요|조여|온도|위치|피부|"  # noqa: ISC003
            + r"replace|hour|soil|reuse|wash|clean|disinfect|blanket|tight|temperat|position|skin",
            text, re.IGNORECASE,
        )
    )


def _electrode_connector_term_question(text: str) -> bool:
    return bool(
        re.search(r"spring\s*clip|\bstud\b|스프링\s*클립|스터드", text, re.IGNORECASE)
        and re.search(r"뜻|의미|뭐|무엇|mean|what", text, re.IGNORECASE)
        and not re.search(
            r"세척|청소|소독|피부|위치|가격|구매|크기|재질|호환|불량|고장|"  # noqa: ISC003
            + r"clean|disinfect|skin|position|cost|buy|size|material|compatib|broken|fault",
            text, re.IGNORECASE,
        )
    )


def _language_setting_question(text: str) -> bool:
    return bool(
        re.search(r"언어|language|영어\s*화면|english\s*(?:screen|display)", text, re.IGNORECASE)
        and re.search(r"설정|전환|바꾸|변경|저장|경로|set|change|switch|save|menu",
                      text, re.IGNORECASE)
        and not re.search(
            r"침대|병상|포트|환자|백업|날짜|번역|매뉴얼|답변|사양|밝기|소리|"  # noqa: ISC003
            + r"bed|port|patient|backup|date|translat|manual|answer|spec|bright|sound",
            text, re.IGNORECASE,
        )
    )


def _frequency_function_question(text: str) -> bool:
    return bool(
        re.search(r"주파수|frequency", text, re.IGNORECASE)
        and re.search(r"유축|진공|vacuum|expression|pumping", text, re.IGNORECASE)
        and not re.search(
            r"저장|메모리|마사지|세척|전원|배터리|통증|압력\s*값|최대\s*압력|"  # noqa: ISC003
            + r"save|memory|massage|clean|power|battery|pain|mmhg|kpa|mhz|\bhz\b|cpm|분당",
            text, re.IGNORECASE,
        )
    )


def _temperature_unit_toggle_question(text: str) -> bool:
    return bool(
        re.search(r"섭씨|화씨|celsius|fahrenheit", text, re.IGNORECASE)
        and re.search(r"바꾸|전환|버튼|표시|누르|change|switch|button|display", text, re.IGNORECASE)
        and not re.search(
            r"계산|공식|\d|범위|정확도|알람|배터리|소리|체온\s*모드|물체|"  # noqa: ISC003
            + r"calculat|formula|range|accuracy|alarm|battery|sound|body\s*mode|object",
            text, re.IGNORECASE,
        )
    )


def _maternal_hr_contact_question(text: str) -> bool:
    return bool(
        re.search(r"심박|\bhr\b|heart\s*rate", text, re.IGNORECASE)
        and re.search(r"산모|엄지|maternal|thumb", text, re.IGNORECASE)
        and re.search(r"접촉|대야|대는|측정|contact|touch|measur", text, re.IGNORECASE)
        and not re.search(
            r"태아|도플러|bmi|체중|몸무게|나이|키|삭제|그래프|배터리|청소|소독|진단|정상\s*범위|"  # noqa: ISC003
            + r"fetal|doppler|weight|height|age|delete|graph|battery|clean|disinfect|"
            + r"diagnos|normal\s*range",
            text, re.IGNORECASE,
        )
    )


def _cleaning_power_disconnect_question(text: str) -> bool:
    return bool(
        re.search(r"청소|세척|clean", text, re.IGNORECASE)
        and re.search(r"전원|코드|플러그|power|cord|unplug", text, re.IGNORECASE)
        and not re.search(
            "".join((  # noqa: FLY002
                r"세제|소독|알코올|천|브러시|물로|물에|담가|습기|건조|순서|먼저|",
                r"청소\s*후|세척\s*후|다시\s*켜|재연결|재가동|배터리|충전|",
                r"detergent|disinfect|alcohol|cloth|brush|water|immers|damp|dry|order|sequence|",
                r"after\s*clean|reconnect|restart|battery|charg|압력|무게|범위|pressure|weight|range",
                r"|재료|화면|센서|프로브|material|screen|sensor|probe|전체|full|",
                r"(?:청소|세척)(?:하는|\s*)\s*(?:방법|절차)|cleaning\s*procedure",
            )), text, re.IGNORECASE,
        )
    )


def _bed_reset_question(text: str) -> bool:
    return bool(
        re.search(r"침대|침상|병상|\bbed\b", text, re.IGNORECASE)
        and re.search(r"reset|리셋|배정.{0,8}해제|cancel.{0,15}assignment", text, re.IGNORECASE)
        and not re.search(
            r"삭제|기록|공장|암호|환자\s*정보|포트|알람|경보|화면\s*배치|"  # noqa: ISC003
            + r"delete|history|factory|password|patient\s*(?:info|data)|port|alarm|layout|"
            + r"네트워크|연결|전원|인쇄|network|connect|power|print",
            text, re.IGNORECASE,
        )
        and not re.search(
            r"수리|교체|분해|기본값|정지|중단|저장|내보내|"  # noqa: ISC003
            + r"repair|replace|disassembl|default|stop|record|save|export",
            text, re.IGNORECASE,
        )
    )


def _single_user_question(text: str) -> bool:
    return bool(
        re.search(r"여러\s*사람|다른\s*사람|두\s*사람|한\s*사람|공유|공동\s*사용|"  # noqa: ISC003
                  + r"single\s*user|more\s*than\s*one|multiple\s*(?:people|users)|share", text,
                  re.IGNORECASE)
        and re.search(r"사용|쓰|써|use|share", text, re.IGNORECASE)
        and not re.search(
            r"세척|소독|멸균|청소|대여|중고|부품|깔때기|젖병|전압|무게|충전|사용\s*시간|"  # noqa: ISC003
            + r"clean|disinfect|steriliz|rental|second.hand|parts|funnel|bottle|"
            + r"voltage|weight|charg|duration|"
            + r"압력|모드|보관|pressure|mode|stor",
            text, re.IGNORECASE,
        )
    )


def _cms_connection_failure_question(text: str) -> bool:
    return bool(
        re.search(r"\bcms\b|중앙\s*(?:감시|모니터)|central\s*monitor", text, re.IGNORECASE)
        and re.search(
            r"연결.{0,15}(?:안\s*되|안\s*돼|실패|않|끊)|(?:not|cannot|can't|fail).{0,20}connect",
            text, re.IGNORECASE,
        )
        and not re.search(
            "".join((  # noqa: FLY002
                r"ip\s*주소|ip\s*address|\d+\.\d+|서브넷|게이트웨이|포트|방화벽|",
                r"subnet|gateway|port|firewall|설치|install|원인|이유|왜|cause|reason|why|",
                r"nibp|ecg|spo2|혈압|심전도|산소포화도|인쇄|프린터|print|",
                r"알람|경보|alarm|배터리|battery|무게|weight|사양|specification|",
                r"대기\s*시간|몇\s*(?:초|분).{0,5}기다|how\s*long|timeout",
            )), text, re.IGNORECASE,
        )
    )


def _remote_nibp_control_question(text: str) -> bool:
    return bool(
        re.search(r"nibp|비침습\s*혈압", text, re.IGNORECASE)
        and re.search(r"원격|중앙\s*감시|central|remote|\bcms\b|\bpc\b", text, re.IGNORECASE)
        and re.search(r"시작|정지|중지|멈|start|stop", text, re.IGNORECASE)
        and not re.search(
            "".join((  # noqa: FLY002
                r"알람|경보|간격|주기|압력|정확도|범위|커프|환자\s*(?:유형|모드)|",
                r"자동|연속|혈압\s*값|전원|설치|연결|네트워크|ip\s*주소|인쇄|",
                r"alarm|interval|period|pressure|accuracy|range|cuff|patient\s*(?:type|mode)|",
                r"automatic|continuous|power|install|connect|network|ip\s*address|print",
                r"|무게|크기|사양|전압|배터리|세척|청소|소독|보정|심전도|산소포화도|체온|호흡|",
                r"weight|dimension|specification|voltage|battery|clean|disinfect|calibr|ecg|spo2|temp|resp",
            )), text, re.IGNORECASE,
        )
    )


def _eto_permission_question(text: str) -> bool:
    named = re.search(
        r"(?<![a-z])eto(?![a-z])|ethylene\s+oxide|에틸렌\s*옥사이드", text, re.IGNORECASE,
    )
    permission = re.search(
        r"가능|되|돼|써|사용|허용|금지|can|may|allow|prohibit", text, re.IGNORECASE,
    )
    extra = re.search(
        r"절차|순서|시간|농도|온도|단계|전체|프로브|센서|커프|케이블|청소|세척|clean|wash|"  # noqa: ISC003
        + r"procedure|steps|concentration|temperature|how\s+to|probe|sensor|cuff|cable|"
        + r"배터리|전압|사양|battery|voltage|specification",
        text, re.IGNORECASE,
    )
    return bool(named and permission and not extra)


def _surface_cleaning_question(text: str) -> bool:
    return bool(
        re.search(r"본체|기기|장치|enclosure|device|housing", text, re.IGNORECASE)
        and re.search(r"화면|스크린|screen|display", text, re.IGNORECASE)
        and re.search(r"닦|청소|세척|clean|wipe", text, re.IGNORECASE)
        and not re.search(
            "".join((  # noqa: FLY002
                r"센서|프로브|커프|케이블|소독|멸균|침수|담그|시간|온도|농도|",
                r"배터리|충전|알람|인쇄|네트워크|음량|밝기|보관|운송|",
                r"sensor|probe|cuff|cable|disinfect|steriliz|immers|duration|temperature|concentration",
                r"|battery|charg|alarm|print|network|volume|brightness|storage|transport",
            )),
            text, re.IGNORECASE,
        )
    )


def _clock_configuration_question(text: str) -> bool:
    return bool(
        re.search(r"날짜|시계|\bdate\b|\bclock\b|time\s*setup", text, re.IGNORECASE)
        and re.search(r"설정|바꾸|변경|메뉴|형식|어디|set|change|menu|format|where",
                      text, re.IGNORECASE)
        and not re.search(
            "".join((  # noqa: FLY002
                r"알람|경보|간격|임계|측정|배터리|암호|비번|비밀번호|초기화|병원|장치명|침상|속도|주파수|",
                r"alarm|interval|threshold|measure|battery|password|reset|department|",
                r"device\s*name|bed|speed|frequency|firmware|펌웨어|업데이트|",
                r"밝기|음량|인쇄|네트워크|brightness|volume|print|network",
            )),
            text, re.IGNORECASE,
        )
    )


def _enclosure_cleaning_question(text: str) -> bool:
    return bool(
        re.search(r"본체|외함|케이스|enclosure|housing|case", text, re.IGNORECASE)
        and re.search(r"닦|세척|청소|액체|담가|담그|clean|wipe|liquid|immers", text, re.IGNORECASE)
        and not re.search(
            "".join((  # noqa: FLY002
                r"프로브|센서|화면|소독|멸균|농도|시간|온도|고장|수리|배터리|충전|브랜드|",
                r"probe|sensor|screen|display|disinfect|steriliz|concentration|duration|",
                r"temperature|repair|battery|charg|brand|알코올|표백|에탄올|락스|",
                r"케이블|커프|네트워크|인쇄|알람|acetone|alcohol|bleach|ethanol|cable|cuff|network|print|alarm",
            )), text, re.IGNORECASE,
        )
    )


def _probe_preparation_question(text: str) -> bool:
    return bool(
        re.search(r"프로브|probe", text, re.IGNORECASE)
        and re.search(r"겔|젤|\bgel\b", text, re.IGNORECASE)
        and re.search(r"위치|측정|대도|바르|position|place|apply|measur", text, re.IGNORECASE)
        and not re.search(
            "".join((  # noqa: FLY002
                r"각도|주파수|깊이|혈압|압력|세척|청소|소독|멸균|배터리|충전|전압|용량|보관|원리|",
                r"MHz|메가헤르츠|angle|frequency|depth|pressure|clean|disinfect|steriliz|battery|charg|",
                r"voltage|volume|storage|principle|\d\s*(?:ml|cc|g)\b|\bml\b|밀리리터|그램|그람|gram",
                r"|범위|정상\s*심박|BPM|range|normal\s*heart|네트워크|인쇄|경보|network|print|alarm",
            )), text, re.IGNORECASE,
        )
    )



def requested_topics(question: str) -> set[str]:  # noqa: C901, PLR0911 - fail-closed scope gates
    """Use reviewed sections only for narrow, recognized question scopes."""
    text = " ".join(question.split())
    if not text or re.search(
        r"가격|비용|price|cost|아니라|말고|아닌|not\s+(?:about|asking)|"  # noqa: ISC003
        + r"정상.{0,10}(?:인데|이고)",
        text,
        re.IGNORECASE,
    ):
        return set()
    if re.search(
        r"수리|교체|분해|repair|replace|disassembl|고장\s*원인|"  # noqa: ISC003
        + r"삭제|기록|내보내|기본값|공장\s*초기화|delete|record|save|export|reset",
        text, re.IGNORECASE,
    ) and not _bed_reset_question(text):
        return set()
    bounded_topic = _bounded_setting_or_storage_topic(text)
    if bounded_topic:
        return {bounded_topic}
    battery_topic = _battery_topic(text)
    if battery_topic:
        return {battery_topic}
    if re.search(
        r"(?:배터리|battery|충전|charg).{0,30}(?:시간|전압|방법|time|voltage|how)",
        text, re.IGNORECASE,
    ):
        return set()
    topics = {
        name for name, pattern in _topic_patterns() if re.search(pattern, text, re.IGNORECASE)
    }
    # A completed volume check opens the documented transducer test. A plain
    # no-sound complaint still takes the ordinary clarification/control path.
    if (
        re.search(r"도플러|doppler|\bdop\b", text, re.IGNORECASE)
        and re.search(r"최대|끝까지|maximum|max\b", text, re.IGNORECASE)
        and re.search(r"안\s*들|들리지|무음|silent|no\s+(?:audio|sound)", text, re.IGNORECASE)
    ):
        topics.add("transducer_test")
    # General pre-use checklists must not replace a question about one specific
    # component, setting, measurement or a separately requested operation.
    without_model = re.sub(r"\b[A-Z]+[- ]?\d+[A-Z0-9-]*\b", "", text, flags=re.IGNORECASE).strip()
    if re.fullmatch(
        r"(?:(?:제품|기기|장치)(?:의|를)?\s*)?"  # noqa: ISC003
        + r"(?:사용\s*전|사용하기\s*전)\s*(?:에\s*)?"
        + r"(?:확인|점검|준비)(?:해야\s*하는|할|해야\s*할)?\s*"
        + r"(?:사항|것|내용|항목|점검사항)?(?:은|을|이)?\s*"
        + r"(?:뭐야|무엇인가요|알려줘|알려주세요)?[?.! ]*|"
        + r"(?:what\s+(?:should\s+I|to)\s+check\s+before\s+(?:each\s+)?use|"
        + r"pre[- ]use\s+checklist)[?.! ]*",
        without_model,
        re.IGNORECASE,
    ):
        topics.add("preuse")
    if re.search(
        r"삭제|기록|저장|내보내|기본값|공장\s*초기화|delete|record|save|export|reset",
        text,
        re.IGNORECASE,
    ):
        return set()
    if "alarm_reference" in topics and (
        re.search(r"음량|볼륨|설정|해제|끄|volume|setting|silence|disable", text, re.IGNORECASE)
        or not re.search(
            r"원인|대책|알람\s*정보|경보\s*정보|cause|countermeasure|alarm\s*information",
            text,
            re.IGNORECASE,
        )
    ):
        return set()
    if topics.intersection({"ecg_connection", "ecg_display"}) and re.search(
        r"어디|위치|부착|붙|색|몇|placement|where|colou?r|how\s+many",
        text,
        re.IGNORECASE,
    ):
        return set()
    if "principle" in topics and re.search(
        r"배터리|충전|battery|charging|청소|clean|소독|disinfect|전압|voltage",
        text,
        re.IGNORECASE,
    ):
        return set()
    if "transducer_environment" in topics:
        topics.discard("preuse")
    return topics


def _eligible(record: ReviewedSection, evidence: Evidence) -> bool:
    if (
        record.document_id != evidence.document_id
        or record.page != evidence.physical_page
        or record.render_sha256 != evidence.render_sha256.lower()
        or evidence.kind == "service"
    ):
        return False
    actual = hashlib.sha256(evidence.text.encode("utf-8")).hexdigest()
    return (
        record.page_sha256 == actual
        and evidence.chunk_sha256 == actual
        and _normalise(record.source_text) in _normalise(evidence.text)
    )


def _subject_compatible(question: str, source_text: str, topic: str) -> bool:
    if topic == "spo2_specs" and re.search(r"neonat|신생아", source_text, re.IGNORECASE):
        for age in (r"adult|성인", r"pediatric|paediatric|child|소아|어린이"):
            if re.search(age, question, re.IGNORECASE) and not re.search(
                age, source_text, re.IGNORECASE,
            ):
                return False
    if topic != "principle":
        return True
    pairs = (
        (r"ecg|심전도", r"ecg|심전도"),
        (r"sp\s*o?2|산소포화도", r"sp\s*o?2|산소포화도"),
        (r"nibp|혈압|커프|cuff", r"nibp|혈압|커프|cuff"),
        (r"doppler|도플러|fhr|태아\s*심박", r"doppler|도플러|fhr|태아\s*심박"),
        (r"체온|temperature", r"체온|temperature"),
    )
    return all(
        not re.search(query_pattern, question, re.IGNORECASE)
        or re.search(source_pattern, source_text, re.IGNORECASE)
        for query_pattern, source_pattern in pairs
    )


def _bundles(records: list[ReviewedSection], topics: set[str]) -> list[list[ReviewedSection]]:
    grouped: dict[str, list[ReviewedSection]] = defaultdict(list)
    for record in records:
        grouped[record.bundle].append(record)
    return [
        group
        for group in grouped.values()
        if len({record.document_id for record in group}) == 1
        and topics.issubset({record.topic for record in group})
    ]


def candidate_pages(question: str, root: Path) -> set[tuple[str, int]]:
    topics = requested_topics(question)
    return (
        {
            (record.document_id, record.page)
            for group in _bundles(_load(root), topics)
            for record in group
        }
        if topics
        else set()
    )


def reviewed_section_answer(
    question: str, selected: AnswerResult, root: Path, *, language: str = "ko"
) -> AnswerResult | None:
    topics = requested_topics(question)
    if (
        not topics
        or language.casefold() not in {"ko", "en"}
        or selected.status not in {"search_results", "answered", "evidence_missing"}
    ):
        return None
    valid: list[tuple[list[ReviewedSection], dict[str, Evidence]]] = []
    for group in _bundles(_load(root), topics):
        matched: dict[str, Evidence] = {}
        for record in group:
            evidence = next(
                (
                    item
                    for item in selected.evidence
                    if _eligible(record, item)
                    and _subject_compatible(question, record.source_text, record.topic)
                ),
                None,
            )
            if evidence is None:
                break
            matched[record.id] = evidence
        else:
            valid.append((group, matched))
    if not valid:
        return None
    evidence_order = {item.evidence_id: index for index, item in enumerate(selected.evidence)}
    group, matched = min(
        valid,
        key=lambda item: min(
            evidence_order.get(source.evidence_id, 10**9) for source in item[1].values()
        ),
    )
    claims: list[Claim] = []
    for record in group:
        if record.topic not in topics:
            continue
        text = record.source_text if language.casefold() == "en" else record.translation_ko
        if len(text) > 700 or len(claims) >= 4:
            return None
        claims.append(
            Claim(
                id=f"C{len(claims) + 1}",
                text=text,
                citation_ids=[matched[record.id].evidence_id],
                presentation="direct",
            )
        )
    if not claims:
        return None
    limited = topics.intersection({"diagnosis_limit", "printer_setup"})
    return selected.model_copy(
        update={
            "status": "partial_answer" if "printer_setup" in topics else "answered",
            "mode": "search",
            "claims": claims,
            "questions": (
                [
                    "백지 출력의 원인은 아직 확인되지 않았습니다. "  # noqa: ISC003
                    + "인쇄 화면의 오류 표시와 사용 중인 용지 종류를 알려 주세요."
                ]
                if "printer_setup" in topics
                else []
            ),
            "message": (
                "측정 정보의 용도와 한계에 관한 매뉴얼 설명입니다. 진단을 확정하지 않습니다."
                if "diagnosis_limit" in topics
                else "용지 설정 확인 방법입니다. 백지 출력의 원인이 확인된 것은 아닙니다."
                if "printer_setup" in topics
                else "검수된 매뉴얼 원문에서 확인한 답변입니다."
            ),
            "limitation_codes": list(
                dict.fromkeys(
                    [
                        code
                        for code in selected.limitation_codes
                        if code not in {"no_matching_evidence", "explanation_unverified"}
                    ]
                    + (["reviewed_scope_limited"] if limited else [])
                    + (["incomplete_evidence"] if "printer_setup" in topics else [])
                )
            ),
        }
    )
