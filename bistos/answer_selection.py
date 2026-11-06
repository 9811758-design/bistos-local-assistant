import json
import re
from pathlib import Path

from pydantic import Field, ValidationError

from bistos.answer_gates import contextual_passages, paragraphs
from bistos.answer_validation import validate_answer
from bistos.errors import AppError
from bistos.models import AnswerResult, Evidence, Model
from bistos.ollama import INPUT_BYTES, Ollama
from bistos.question_intent import (
    battery_runtime_question,
    full_charge_question,
    question_intent,
    runtime_evidence,
    service_factual_question,
    service_overview_question,
)
from bistos.search import door_question, normalized, relevance
from bistos.source_structure import complete_structured_passage, structure_for_passage

SOURCE_PASSAGE_MAX = 2400

_PRINCIPLE_MECHANISM_RE = re.compile(
    "".join((  # noqa: FLY002
        r"convert|process|circulat|heat|measur|detect|vacuum|suction|pressure|pump|diaphragm|",
        r"doppler|ultrasound|heart\s*rate|sensor|temperature|humidity|",
        r"light|leds?|phototherap(?:y|ic)|bilirubin|wavelength|spectral|",
        r"ultraviolet|infrared|광원|광선|광요법|빌리루빈|파장|스펙트럼|",
        r"자외선|적외선|온도|습도|압력|펌프|센서|심박|측정|감지",
    )),
    re.IGNORECASE,
)


def _has_principle_mechanism(text: str) -> bool:
    return bool(_PRINCIPLE_MECHANISM_RE.search(text))


def _principle_core_passage(text: str) -> str:
    """Keep the complete optical/mechanical chain, excluding trailing metadata.

    Manuals often place age groups, calibration status, and defibrillation
    compatibility immediately after a principle paragraph. Those facts are
    valid elsewhere, but adding them to a principle-only answer can make the
    explanation model treat them as part of the mechanism. Cut only at a
    sentence boundary after a mechanism has already appeared; never cut a
    causal sentence or apply this to non-principle questions.
    """
    if not (_has_principle_mechanism(text)
            and re.search(r"Sp\s*O\s*2", text, re.IGNORECASE)
            and re.search(r"pulsat|맥동|박동", text, re.IGNORECASE)
            and re.search(r"absor|흡수|흡광", text, re.IGNORECASE)):
        return text
    sentences = re.split(r"(?<=[.!?\u3002\uff01\uff1f])\s+", text.strip())
    marker = re.compile(
        r"^(?:This\s+monitor\s+applies\s+to\s+measure|"  # noqa: ISC003
        + r"SpO2\s+function\s+of\s+this\s+monitor\s+has\s+been\s+calibrated|"
        + r"The\s+monitor\s+is\s+defibrillation\s+proof)",
        re.IGNORECASE,
    )
    kept: list[str] = []
    mechanism_seen = False
    for sentence in sentences:
        if _has_principle_mechanism(sentence):
            mechanism_seen = True
        if mechanism_seen and marker.search(sentence):
            break
        kept.append(sentence)
    result = " ".join(part.strip() for part in kept if part.strip())
    return result if len(result) >= 80 and _has_principle_mechanism(result) else text


def _principle_question(question: str) -> bool:
    return question_intent(question) == "principle" or bool(re.search(
        r"어떤\s*원리로\s*(?:작동|동작)|what\s+principle|how\s+does\s+.*work",
        question,
        re.IGNORECASE,
    ))


class Passage(Model):
    id: str
    evidence_id: str
    text: str


class Selection(Model):
    selected_ids: list[str] = Field(max_length=4)


def _service_qualification_context(chosen: list[Passage], sources: list[Evidence]) -> list[Passage]:
    """Keep same-page personnel restrictions attached to selected repair/parts text."""
    pages = {item.evidence_id: " ".join(item.text.split()) for item in sources}
    enriched: list[Passage] = []
    for passage in chosen:
        if not re.search(r"repair|replace.{0,35}(?:part|component)|수리|부품.{0,15}교체",
                         passage.text, re.IGNORECASE):
            enriched.append(passage)
            continue
        qualifications = [sentence for sentence in
                          re.split(r"(?<=[.!?])\s+", pages.get(passage.evidence_id, ""))
                          if re.search(r"only\s+qualified|자격.{0,80}(?:만|의해서만)",
                                       sentence, re.IGNORECASE)
                          and re.search(r"repair|replace|수리|교체", sentence, re.IGNORECASE)]
        additions = [sentence for sentence in qualifications
                     if normalized(sentence) not in normalized(passage.text)]
        complete = "\n\n".join([passage.text, *additions])
        if len(complete) > SOURCE_PASSAGE_MAX:
            raise AppError("서비스 자격 조건을 포함한 원문이 길어 설명을 보류했습니다.", 413)
        enriched.append(passage.model_copy(update={"text": complete}))
    return enriched


def _ordinary_printer_error_row(question: str, text: str) -> bool:
    # A complete printer error-table row can describe an ordinary user action.
    # Match the entire row: nearby repair/disassembly text must never ride along.
    ordinary_rows = {
        "NO PAPER": r"NO PAPER[. ]+(?:Paper empty|Paper is not loaded)[. ]+Insert the paper[. ]*",
        "DOOR OPEN": (
            r"DOOR OPEN[. ]+(?:Printer door open|The print door is opened)"
            r"[. ]+Close the door[. ]*"
        ),
    }
    compact = " ".join(text.split())
    return any(
        code.casefold() in question.casefold()
        and re.fullmatch(pattern, compact, re.IGNORECASE)
        for code, pattern in ordinary_rows.items()
    )


def safe_service_passage(question: str, text: str) -> bool:
    """Allow factual text and complete ordinary-action rows, never repair steps."""
    if _ordinary_printer_error_row(question, text):
        return True
    if service_overview_question(question):
        return _service_overview_passage(text)
    factual_question = service_factual_question(question)
    if question_intent(question) not in {"principle", "spec"} and not factual_question:
        return False
    hazardous = re.compile(
        (r"분해|해체|교정|보정|캘리브|calibrat|disassembl|replace\s+(?:the\s+)?"
         r"(?:board|part|module)|교체\s*(?:부품|보드|메인보드)|firmware|password|"
         r"암호|service\s+mode|고전압|high\s+voltage|"
         r"(?:open|remove|disconnect).{0,25}(?:housing|case|cover|board|internal)|"
         r"jumper|short.{0,15}(?:pin|terminal)|bypass|외함.{0,10}(?:열|분리)"),
        re.IGNORECASE,
    )
    if hazardous.search(text):
        return False
    factual = re.compile(
        (r"원리|작동|측정|감지|센서|사양|정격|범위|principle|measure|detect|sensor|"
         r"specification|rating|range|menu|메뉴|누르|press|확인|check|연결|connect|"
         r"설정|setting|packing\s*list|package|quantity|수량|구성품|"
         r"display|message|표시|메시지|connector|port|단자|포트"),
        re.IGNORECASE,
    )
    return bool(factual.search(text))


def _service_overview_passage(text: str) -> bool:
    """Admit personnel/classification facts, rejecting any executable work text."""
    qualified = re.search(
        "|".join((  # noqa: FLY002
            r"\b(?:qualified|professionals?|experienced\s+(?:engineers?|personnel))\b",
            r"\bmaintenance\s+experience\b|자격|전문\s*(?:인력|기사|기술자)|숙련",
        )), text, re.IGNORECASE,
    )
    prohibited = re.search(
        "|".join((  # noqa: FLY002
            r"\b(?:unqualified|untrained|non[-\s]?professionals?)\b|\bnot\s+qualified\b",
            r"비전문|무자격|자격.{0,8}(?:없|않)",
            r"\b(?:remov(?:e|es|ed|ing)|replac(?:e|es|ed|ing)|disassembl\w*|calibrat\w*)\b",
            r"\b(?:disconnect\w*|unscrew\w*|solder\w*|open\w*|bridg\w*|short\w*|jumper\w*)\b",
            r"\b(?:bypass\w*|connect\w*|insert\w*|install\w*|adjust\w*|press\w*|perform\w*)\b",
            r"\b(?:cut\w*|turn\w*|appl(?:y|ies|ied|ying)|flash\w*|powering|TP\d+)\b",
            r"password|firmware|service\s+mode|test\s+(?:point|pin)",
            r"분해|교정|보정|납땜|암호|펌웨어|교체|분리|해체|제거|단락|우회|연결",
            r"(?:패널|커버|하우징|케이스).{0,15}열|열어|열고|누르|삽입|조절|설치",
        )), text, re.IGNORECASE,
    )
    return bool(qualified and not prohibited)



def _device_principle_passages(question: str, text: str) -> list[str]:  # noqa: C901
    """Recover complete device-level explanatory sections for broad questions.

    A numbered overview may include optional sensors. Keep that whole section,
    including its option labels, instead of treating a sensor chapter as the
    complete device. Component questions continue through normal selection.
    """
    if not _principle_question(question) or not re.search(
        "".join((  # noqa: FLY002
            r"(?:제품|장치|기기)(?:의)?\s*(?:작동|동작)?\s*원리|",
            r"(?:device|product|system).{0,20}(?:work|principle)|",
            r"(?:operating|working)\s+principles?\s+of\s+(?:the\s+)?",
            r"(?:device|product|system)|\b(?:how\s+it\s+works?|measurement\s+principle)\b|",
            r"\b원리\b|\bprinciples?\b|측정\s*원리",
        )),
        question, re.IGNORECASE,
    ):
        return []
    if re.search(
        r"SpO2|ECG|NIBP|산소\s*포화|심전도|혈압|습도|온도|도플러|temperature|humidity|doppler|sensor|센서",
        question, re.IGNORECASE,
    ):
        return []
    heading = re.compile(r"(?m)^\s*(\d+(?:\.\d+)*)(?:\.)?\s+([^\n]+)")
    headings = list(heading.finditer(text))
    result: list[str] = []
    for index, match in enumerate(headings):
        title = match[2].strip()
        if len(match[1].split(".")) > 2 or not re.fullmatch(
            r"(?:Operating|Working)\s+Principles?|(?:Brief\s+)?(?:Device|Product)\s+Description|Intended\s+Use|작동\s*원리|동작\s*원리|제품\s*설명|기기\s*설명|사용\s*목적",
            title, re.IGNORECASE,
        ):
            continue
        depth = len(match[1].split("."))
        # A component chapter's nested 'Operating principle' is not an overall
        # device description, even when its heading has only two levels.
        parent = next((item for item in reversed(headings[:index])
                       if len(item[1].split(".")) < depth), None)
        if parent and re.search(
            r"SpO2|ECG|NIBP|oximet|pulse|산소\s*포화|심전도|혈압", parent[2], re.IGNORECASE,
        ):
            continue
        stop = next((item.start() for item in headings[index + 1:]
                     if len(item[1].split(".")) <= depth), len(text))
        section = text[match.start():stop].strip()
        if re.fullmatch(r"Intended\s+Use|사용\s*목적", title, re.IGNORECASE):
            # The opening descriptive paragraph precedes enumerated populations
            # and clinical indications. Its own qualifiers remain verbatim.
            if not re.search(
                r"convert|process|control|circulat|변환|처리|제어|조절", section, re.IGNORECASE,
            ):
                continue
            section = re.split(r"(?m)^\s*\d+\)\s+", section, maxsplit=1)[0].strip()
        if not 80 <= len(section) <= SOURCE_PASSAGE_MAX or _is_heading_only(section):
            continue
        if re.search(r"refer\s+to|see\s+(?:chapter|section)|참조", section, re.IGNORECASE):
            continue
        if not _has_principle_mechanism(section):
            continue
        result.append(section)
    return result


def _power_failure_table_passage(question: str, text: str) -> str | None:
    """Keep only the verified power-failure row from a known troubleshooting table.

    The BT-400 page places several independent symptom rows in one extracted
    passage.  Narrowing is deliberately gated by the exact row labels and by
    both page-local safety warnings; any additional symptom or diagnostic term
    leaves the normal broad selection path unchanged.
    """
    if not re.search(
        r"(?:켜지지|전원.*(?:안\s*켜|불능)|does\s+not\s+turn\s+on|(?:won't|wont)\s+turn\s+on|power\s+failure)",
        question,
        re.IGNORECASE,
    ):
        return None
    # A second symptom or an explicit diagnostic branch makes this a compound
    # question; retaining the complete source avoids hiding relevant rows.
    if re.search(
        r"(?:LED|발광|과열|온도|heated|overheat|display|화면|printer|프린터|sound|소리|alarm|경보|error|오류|code|코드|battery|배터리|NO\s*PAPER|DOOR\s*OPEN|LOW\s*BAT(?:TERY)?|에러\s*코드)",
        question,
        re.IGNORECASE,
    ):
        return None
    row = re.search(r"The\s+unit\s+does\s+not\s+turn\s+on\.", text, re.IGNORECASE)
    next_row = re.search(r"Some\s+LEDs\s+are\s+not\s+lit\.", text, re.IGNORECASE)
    if row is None or next_row is None or next_row.start() <= row.start():
        return None
    prefix = text[:row.start()]
    warning_blocks = [
        match.group(0).strip()
        for match in re.finditer(
            r"(?is)(?:^|\n)\s*warning!.*?(?=\n\s*warning!|\n\s*problem\s+possible|\Z)",
            prefix,
        )
    ]
    power_warning = [
        block for block in warning_blocks
        if re.search(r"disconnect\s+power\s+cord|전원\s*코드.*분리", block, re.IGNORECASE)
    ]
    service_warning = [
        block for block in warning_blocks
        if re.search(r"only\s+qualified\s+service|자격.*(?:서비스|수리)", block, re.IGNORECASE)
    ]
    # Narrowing without both original safeguards would turn a selection fix
    # into an unsafe service instruction.
    if not power_warning or not service_warning:
        return None
    header = re.search(
        r"(?is)(?:^|\n)\s*problem\s+possible\s+cause\s+action\s*",
        prefix,
    )
    table_start = header.start() if header else row.start()
    table = text[table_start:next_row.start()].strip()
    passage = "\n\n".join([*power_warning, *service_warning, table])
    return passage if len(passage) <= SOURCE_PASSAGE_MAX else None

_MEASUREMENT_SUBJECTS = {
    "ecg": r"(?<![A-Z])ECG(?![A-Z])|심전도",
    "spo2": r"(?<![A-Z])SpO2(?![A-Z0-9])|산소\s*포화도|\bpleth(?:ysmograph(?:ic|y)?)?\b|맥파",
    "nibp": r"(?<![A-Z])NIBP(?![A-Z])|비침습\s*혈압",
    "resp": r"\bRESP\b|respiration|호흡",
    "temp": r"\bTEMP\b|temperature|체온|섭씨|화씨|celsius|fahrenheit|°\s*[CF]|℃|℉",
}


def _measurement_subjects(text: str) -> set[str]:
    return {name for name, pattern in _MEASUREMENT_SUBJECTS.items()
            if re.search(pattern, text, re.IGNORECASE)}


def _parameter_setting_question(question: str) -> bool:
    question = re.sub(r"(?:분|초|시간)\s*단위", "", question)
    return bool(_measurement_subjects(question) and re.search(
        "".join((  # noqa: FLY002
            r"wave\s*(?:speed|gain)|파형\s*(?:속도|높이|진폭)|\bgain\b|",
            r"averag(?:e|ing)\s*time|평균\s*시간|\bunit\b|단위|섭씨|화씨|celsius|fahrenheit|°\s*[CF]|℃|℉",
        )),
        question, re.IGNORECASE,
    ))


def _parameter_setting_passages(question: str, text: str) -> list[str]:
    """Retain numbered parent context; a passing mention is not a subject.

    A page about ECG may mention SpO2 as an HR source. Matching that token
    alone lets its ECG speed values answer a pleth question. Determine scope
    from the heading hierarchy instead, and attach the verbatim parent intro
    (including menu entry instructions) to each complete child section.
    """
    requested = _measurement_subjects(question)
    headings = [item for item in re.finditer(
        r"(?m)^[ \t]*(\d+(?:\.\d+)*)\.?[ \t]+([^\n]{2,100})", text,
    ) if "." in item[1] or re.fullmatch(
        "".join((  # noqa: FLY002
            r"(?:SpO2|ECG|NIBP|RESP|TEMP|Respiration|Temperature|심전도|호흡|체온)",
            r"(?:\s+(?:setup|settings?|display))?\s*",
        )), item[2], re.IGNORECASE,
    )]
    result: list[str] = []
    for index, match in enumerate(headings):
        ancestors = [item for item in headings[:index + 1]
                     if match[1] == item[1] or match[1].startswith(item[1] + ".")]
        parent = next((item for item in reversed(ancestors)
                       if _measurement_subjects(item[2])), None)
        if parent is None or not (_measurement_subjects(parent[2]) & requested):
            continue
        # Only leaves are independent evidence units. Parent sections are
        # included as context, never as a page-wide bag of unrelated settings.
        if index + 1 < len(headings) and headings[index + 1][1].startswith(match[1] + "."):
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        block = text[match.start():end].strip()
        if parent is not match:
            context: list[str] = []
            for ancestor in ancestors[ancestors.index(parent):-1]:
                ancestor_index = headings.index(ancestor)
                context.append(text[
                    ancestor.start():headings[ancestor_index + 1].start()
                ].strip())
            block = "\n\n".join([*context, block])
        if (25 <= len(block) <= SOURCE_PASSAGE_MAX and not _is_heading_only(block)
                and _setting_action_matches(question, block)):
            result.append(block)
    return result


def parameter_requirement_passages(question: str, text: str) -> list[str] | None:
    """Expose the existing parameter scope for same-document evidence recovery."""
    if _parameter_setting_question(question):
        return _parameter_setting_passages(question, text)
    return None


def _setting_action_matches(question: str, block: str) -> bool:
    actions = (
        (r"wave\s*speed|파형\s*속도", r"wave\s*speed|파형\s*속도"),
        (r"\bgain\b|파형\s*(?:높이|진폭)", r"\bgain\b|이득"),
        (r"averag(?:e|ing)\s*time|평균\s*시간", r"averag(?:e|ing)\s*time|평균\s*시간"),
        (r"\bunit\b|단위|섭씨|화씨|celsius|fahrenheit|°\s*[CF]|℃|℉",
         "".join((  # noqa: FLY002
             r"(?:\bset(?:ting)?\b|설정).{0,40}(?:unit|단위)|(?:unit|단위).{0,40}(?:℃|℉|°C|°F)|",
             r"(?:press|hold|누르|길게).{0,180}(?:Celsius|섭씨).{0,40}(?:Fahrenheit|화씨)",
         ))),
    )
    requested = [source for query, source in actions if re.search(query, question, re.IGNORECASE)]
    return any(re.search(pattern, block, re.IGNORECASE | re.DOTALL) for pattern in requested)


def _factory_default_question(question: str) -> bool:
    return bool(re.search(r"default|공장|factory", question, re.IGNORECASE) and re.search(
        r"날짜|시간|시계|초기|복원|date|time|clock|reset|restore", question, re.IGNORECASE,
    ))


def _factory_default_passages(text: str) -> list[str]:
    """Keep a complete Default control item, including its reset exceptions."""
    boundaries = list(re.finditer(
        r"(?m)^[ \t]*(?:[✓➢⚫•]|\d+(?:\.\d+)+\s+)", text,
    ))
    result: list[str] = []
    for index, marker in enumerate(boundaries):
        end = boundaries[index + 1].start() if index + 1 < len(boundaries) else len(text)
        block = text[marker.start():end].strip()
        if (re.search(r"default", block[:100], re.IGNORECASE)
                and re.search(r"factory|공장|날짜|date|시계", block, re.IGNORECASE)
                and 25 <= len(block) <= SOURCE_PASSAGE_MAX):
            result.append(block)
    return result


def _cleaned_parts_question(question: str) -> bool:
    return bool(
        re.search(
            r"부품|깔때기|젖병|parts?|components?|funnel|bottle|tubing", question, re.IGNORECASE,
        )
        and re.search(
            r"세척|씻|젖어|물기|말리|건조|wash|clean|dry|wet|damp", question, re.IGNORECASE,
        )
        and re.search(r"보관|store|storage|말리|dry|건조", question, re.IGNORECASE)
    )


def _cleaned_parts_passages(text: str) -> list[str]:
    """Keep the complete parts-care procedure, not storage of its contents."""
    sections = _numbered_section_passages(text)
    candidates = sections or paragraphs(text)
    return [block for block in candidates
            if re.search(r"parts?|components?|부품|깔때기|젖병", block, re.IGNORECASE)
            and re.search(
                r"air\s*dry|dry\s+parts|wet\s+or\s+damp|자연\s*건조|물기|건조",
                block, re.IGNORECASE,
            )
            and not re.search(
                r"storage\s+of\s+(?:breast\s*milk|milk)|모유\s*보관", block, re.IGNORECASE,
            )
            and len(block) <= SOURCE_PASSAGE_MAX]


def _specific_operation_passages(question: str, text: str) -> list[str] | None:
    if _factory_default_question(question):
        return _factory_default_passages(text)
    if _parameter_setting_question(question):
        return _parameter_setting_passages(question, text)
    if _cleaned_parts_question(question):
        return _cleaned_parts_passages(text)
    return None


def _complete_bullet_items(text: str) -> list[str]:
    """Use source list boundaries, never individual sentence fragments.

    Numbered chapter headings terminate the last bullet. Numbered procedure
    steps are deliberately not independent items: their ordering and parent
    warnings must remain together in the ordinary section path.
    """
    boundary = re.compile(
        "".join((  # noqa: FLY002
            r"(?m)^[ \t]*(?:[✓➢⚫•]|\d+(?:\.\d+)+\.?[ \t]+[^\n]+|",
            r"\d+\.?[ \t]+(?:제품\s*사양|[Ss]pecifications?|[Mm]aintenance|",
            r"[Aa]ccessories|[Cc]leaning|[Ss]afety|[Tt]roubleshooting)\b[^\n]*)",
        )),
    )
    markers = list(boundary.finditer(text))
    result: list[str] = []
    for index, marker in enumerate(markers):
        if not re.match(r"\s*[✓➢⚫•]", marker[0]):
            continue
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        block = text[marker.start():end].strip()
        if 25 <= len(block) <= SOURCE_PASSAGE_MAX:
            result.append(block)
    return result


def _consumable_replacement_items(question: str, items: list[str]) -> list[str]:
    if not (re.search(r"소모품|액세서리|accessor|consumable", question, re.IGNORECASE)
            and re.search(r"기간|주기|교환|교체|period|replace|interval", question, re.IGNORECASE)):
        return []
    selected = [item for item in items if (
        re.search(r"consumable|소모품", item, re.IGNORECASE)
        and re.search(r"wear|damage|period|month|손상|마모|기간|개월", item, re.IGNORECASE)
    ) or re.search(r"original\s+accessories\s+only|정품.{0,10}(?:액세서리|부품)",
                   item, re.IGNORECASE)]
    # Never turn just the recommended lifetime into permission to keep using
    # damaged consumables. Require the same-page damage exception as a unit.
    if not any(re.search(r"damage|손상|마모", item, re.IGNORECASE) for item in selected):
        return []
    block = "\n\n".join(selected)
    return [block] if 25 <= len(block) <= SOURCE_PASSAGE_MAX else []


def _power_warning_before_method(block: str) -> str | None:
    method_start = re.search(r"(?im)^[ \t]*(?:Cleaning|Disinfection)\s*:", block)
    if method_start is None:
        return None
    power_steps = re.findall(
        r"(?:Turn|Switch)\s+off[^.!?]{0,100}?(?:unplug|disconnect)[^.!?]{0,100}?before\s+cleaning\.",
        block, re.IGNORECASE,
    )
    return "\n\n".join([block[:method_start.start()].strip(), *power_steps])


def _cleaning_power_warning(question: str, text: str) -> list[str]:  # noqa: C901
    power_handling = re.search(r"전원|코드|power|cord", question, re.IGNORECASE)
    cable_handling = (re.search(r"케이블|커넥터|cable|connector", question, re.IGNORECASE)
                      and re.search(r"연결|분리|뽑|빼|정리|disconnect|unplug|connection", question,
                                    re.IGNORECASE))
    if not (re.search(r"청소|세척|닦|clean|wipe", question, re.IGNORECASE)
            and (power_handling or cable_handling)):
        return []
    if re.search("".join((  # noqa: FLY002
        r"전체|전반|청소\s*방법|세척\s*방법|닦는\s*방법|full\s+procedure|",
        r"세제|소독제|용액|농도|재질|detergent|disinfectant|solution|concentration|material",
    )), question, re.IGNORECASE):
        return []
    # The explicit cleaning warning is a complete source unit. Preserve its
    # following cleaning caution, but do not import the next fault table or
    # other procedures merely because they share a PDF page.
    markers = list(re.finditer(
        "".join((  # noqa: FLY002
            r"(?m)^[ \t]*(?:WARNING\b|CAUTION\b|경고(?=\s|$)|주의(?=\s|$)|",
            r"\d+(?:\.\d+)*\.?[ \t]+[A-Za-z가-힣])",
        )), text, re.IGNORECASE,
    ))
    scoped: list[str] = []
    for index, marker in enumerate(markers):
        if not re.match(r"\s*(?:WARNING|경고)", marker[0], re.IGNORECASE):
            continue
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        block = text[marker.start():end].strip()
        if not (re.search(r"clean|청소|세척", block, re.IGNORECASE)
                and re.search(r"disconnect|switch\s+off|turn\s+off|분리|끄", block,
                              re.IGNORECASE)):
            continue
        power_only = _power_warning_before_method(block)
        explicit_power = re.search(
            r"(?m)^[ \t]*[•⚫][ \t]*Disconnect\s+electrical\s+power:"  # noqa: ISC003
            + r"[^.]{0,240}cleaning[^.]{0,80}[.]", block, re.IGNORECASE | re.DOTALL,
        )
        if (explicit_power and not re.search(r"젖|손|공급|승인|wet|hand|supplied|approved",
                                              question, re.IGNORECASE)):
            power_only = explicit_power[0]
        block = power_only or block
        if not power_only and index + 1 < len(markers) and re.match(
            r"\s*(?:CAUTION|주의)", markers[index + 1][0], re.IGNORECASE,
        ):
            caution_end = markers[index + 2].start() if index + 2 < len(markers) else len(text)
            caution = text[end:caution_end].strip()
            if re.search(r"clean|청소|세척", caution, re.IGNORECASE):
                block += "\n\n" + caution
        if 25 <= len(block) <= SOURCE_PASSAGE_MAX:
            scoped.append(block)
    if scoped:
        return scoped
    return [block for block in paragraphs(text)
            if re.match(r"Warning!?\s+Disconnect\s+electrical\s+power", block, re.IGNORECASE)
            and re.search(r"switch\s+off.{0,50}disconnect", block, re.IGNORECASE | re.DOTALL)
            and re.search(r"cleaning", block, re.IGNORECASE)
            and len(block) <= SOURCE_PASSAGE_MAX]


def _battery_leak_warning(question: str, items: list[str]) -> list[str]:
    """Retain a complete leak/exposure warning, including its conditional care."""
    if not (re.search(r"battery|batteries|배터리|건전지", question, re.IGNORECASE)
            and re.search(r"첫|먼저|우선|first|initial", question, re.IGNORECASE)
            and re.search(r"leak|smell|odor|누액|누설|새[거나고]|냄새", question,
                          re.IGNORECASE)):
        return []
    return [item for item in items
            if re.search(r"battery|batteries|배터리|건전지", item, re.IGNORECASE)
            and re.search(r"leak|foul\s+smell|누설|누액|냄새", item, re.IGNORECASE)
            and re.search(r"stop\s+using|중지|중단", item, re.IGNORECASE)]


def _calibration_policy_items(question: str, items: list[str]) -> list[str]:
    """Select the complete authorization/frequency statement, not a procedure."""
    if not (re.search(r"calibrat|교정|보정", question, re.IGNORECASE)
            and re.search(r"사용자|직접|누가|자격|who|user|allowed", question, re.IGNORECASE)):
        return []
    subjects = _measurement_subjects(question)
    return [item for item in items
            if re.search(r"users?\s+can\s*not\s+calibrat", item, re.IGNORECASE)
            and re.search(r"qualified.{0,60}personnel", item, re.IGNORECASE | re.DOTALL)
            and re.search(r"every|interval|주기", item, re.IGNORECASE)
            and (not subjects or subjects & _measurement_subjects(item))]


def _battery_replacement_policy(question: str, items: list[str]) -> list[str]:
    """Keep the whole authorization and replacement-specification bullet."""
    if re.search(r"주기|자주|수명|성능|전체|절차|순서|interval|often|life|performance|steps",
                 question, re.IGNORECASE):
        return []
    if not (re.search(r"배터리|battery", question, re.IGNORECASE)
            and re.search(r"교체|replace", question, re.IGNORECASE)
            and re.search(r"누가|직접|열어|자격|who|allowed|open", question, re.IGNORECASE)):
        return []
    return [item for item in items
            if re.search(r"do not open the battery compartment", item, re.IGNORECASE)
            and re.search(r"qualified.{0,90}authorized", item, re.IGNORECASE | re.DOTALL)
            and re.search(r"same\s+model", item, re.IGNORECASE)]


def _optional_profile_items(question: str, items: list[str]) -> list[str]:
    if not (re.search(r"사진|이름|생년월일|성별|photo|name|gender|birth", question, re.IGNORECASE)
            and re.search(r"입력|등록|input|enter", question, re.IGNORECASE)
            and re.search(r"않|안\s*하|필수|선택|optional|required|without", question,
                          re.IGNORECASE)):
        return []
    if re.search(r"블루투스|연결|기록|방법|조작|전체|bluetooth|connect|history|how|steps", question,
                 re.IGNORECASE):
        return []
    return [item for item in items
            if re.search(r"inputs?\s+are\s+options?", item, re.IGNORECASE)
            and re.search(r"no\s+problem\s+running", item, re.IGNORECASE)
            and re.search(r"photograph|name|gender|birth", item, re.IGNORECASE)]


def _brightness_range_items(question: str, items: list[str]) -> list[str]:
    """Prefer an eligible full control item that defines the brightness maximum."""
    if not (re.search(r"밝기|백라이트|backlight|brightness", question, re.IGNORECASE)
            and re.search(r"몇|최대|범위|range|max|highest|brightest", question, re.IGNORECASE)):
        return []
    if re.search(r"전체|절차|순서|방법|어떻게|알람|경보|how|steps|alarm|volume", question,
                 re.IGNORECASE):
        return []
    return [item for item in items
            if re.search(r"backlight|백라이트", item, re.IGNORECASE)
            and re.search(r"range|범위", item, re.IGNORECASE)
            and re.search(r"bright-?\s*est|가장\s*밝", item, re.IGNORECASE)]


def _disinfection_policy(question: str, text: str) -> list[str]:
    if re.search(r"전체|절차|순서|방법|어떻게|how|method|procedure|steps", question, re.IGNORECASE):
        return []
    if not (re.search(r"소독|멸균|disinfect|steriliz", question, re.IGNORECASE)
            and re.search(r"EtO|가스|gas|formaldehyde|포름알데히드", question, re.IGNORECASE)):
        return []
    permission_only = (
        re.search(r"해도|되냐|가능|허용|금지|돼|allowed|permit|prohibit|\bcan\b|\bmay\b", question,
                  re.IGNORECASE)
        and not re.search("".join((  # noqa: FLY002
            r"청소|세척|먼저|사전|언제|주기|약품|소독제|용액|알코올|알콜|",
            r"clean|wash|prior|before|when|frequency|chemical|solution|alcohol|ethanol",
        )), question, re.IGNORECASE)
    )
    headings = list(re.finditer(r"(?m)^[ \t]*\d+(?:\.\d+)*\.?[ \t]+[A-Z][^\n]*", text))
    result: list[str] = []
    for index, heading in enumerate(headings):
        if not re.search(r"\bDisinfection\b", heading.group(), re.IGNORECASE):
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        block = text[heading.start():end].strip()
        if (re.search(r"do not disinfect.{0,100}EtO", block, re.IGNORECASE | re.DOTALL)
                and len(block) <= SOURCE_PASSAGE_MAX):
            caution = re.search(r"(?im)^[ \t]*CAUTION\b", block)
            if permission_only and caution:
                block = block[caution.start():].strip()
            result.append(block)
    return result


def _single_port_passages(question: str, text: str) -> list[str]:
    attachment_opening = (
        re.search(r"안\s*쓰|사용하지\s*않|미사용|반대쪽|두\s*번째|unused|second", question,
                  re.IGNORECASE)
        and re.search("".join((  # noqa: FLY002
            r"튜브.{0,15}(?:꽂|끼우|연결).{0,10}구멍|",
            r"(?:tube|tubing).{0,30}(?:opening|hole)",
        )), question, re.IGNORECASE)
    )
    if not (re.search(r"single|싱글|한쪽", question, re.IGNORECASE)
            and (re.search(r"port|포트|연결구|마개|커버", question, re.IGNORECASE)
                 or attachment_opening)
            and not re.search(
                r"준비|전체|전반|절차|순서|누수|누액|새는|청소|세척|소독|prepare|steps|procedure|leak|clean|wash",
                question, re.IGNORECASE)):
        return []
    result: list[str] = []
    steps = list(re.finditer(r"(?m)^[ \t]*\(\d+\)[ \t]*", text))
    for index, step in enumerate(steps):
        end = steps[index + 1].start() if index + 1 < len(steps) else len(text)
        block = text[step.start():end].strip()
        if (re.search(r"single\s+pumping", block, re.IGNORECASE)
                and re.search(r"port\s+cover", block, re.IGNORECASE)
                and len(block) <= SOURCE_PASSAGE_MAX):
            # Preserve warnings about this port, not independent preparation
            # tasks such as washing breast-contact parts or double pumping.
            warning = list(re.finditer(
                r"(?im)^[ \t]*(?:WARNING|CAUTION|경고|주의)\b", text[:step.start()],
            ))
            if warning:
                start = warning[-1].start()
                first_step = next((item for item in steps if item.start() > start), step)
                context = text[start:first_step.start()].strip()
                local_warnings = [item for item in _complete_bullet_items(context)
                                  if re.search(r"port|single\s+pumping", item, re.IGNORECASE)]
                if local_warnings:
                    block += "\n\n" + "\n".join(local_warnings)
            if len(block) <= SOURCE_PASSAGE_MAX:
                result.append(block)
    return result


def _temperature_display_definition(question: str, text: str) -> list[str]:
    if not (re.search(r"TEMP|체온|온도", question, re.IGNORECASE)
            and re.search(r"display|디스플레이|표시|보여", question, re.IGNORECASE)):
        return []
    if re.search("".join((  # noqa: FLY002
        r"프로브|센서|교체|연결|방법|어떻게|바꾸|변경|설정|전체|절차|",
        r"probe|sensor|replace|connect|how|change|setup",
    )), question, re.IGNORECASE):
        return []
    start = re.search(r"(?im)^[ \t]*\d+(?:\.\d+)+[ \t]+(?:체온|TEMP)[ \t]*(?:디스플레이|display)",
                      text)
    if start is None:
        return []
    tail = text[start.start():]
    body_start = tail.find("\n") + 1
    boundary = re.search(r"(?im)^\s*(?:경고|주의|WARNING|CAUTION|\d+(?:\.\d+)+\s)",
                         tail[body_start:])
    block = tail[:boundary.start() + body_start].strip() if boundary else tail.strip()
    if (re.search(r"T1", block) and re.search(r"T2", block)
            and re.search(r"차이|difference", block, re.IGNORECASE)
            and len(block) <= SOURCE_PASSAGE_MAX):
        return [block]
    return []


def _dry_storage_steps(question: str, text: str) -> list[str]:
    if not (re.search(r"유축|부품|parts|pump", question, re.IGNORECASE)
            and re.search(r"보관|건조|젖(?:어|은|었)|축축|stor|dry|damp|wet",
                          question, re.IGNORECASE)):
        return []
    if re.search(r"전체|전반|절차|끓|소독|집게|꺼내|boil|saniti|tongs|remove|"  # noqa: ISC003
                 + r"세척\s*방법|청소\s*방법|어떻게\s*(?:세척|청소|닦)|how.{0,15}wash",
                 question, re.IGNORECASE):
        return []
    steps = list(re.finditer(r"(?m)^[ \t]*\(\d+\)[ \t]*", text))
    for step in steps:
        if not re.match(r"Place\s+parts\s+on\s+a\s+clean\s+surface", text[step.end():],
                        re.IGNORECASE):
            continue
        tail = text[step.start():]
        next_section = re.search(r"(?m)^[ \t]*\d+(?:\.\d+)+\.?[ \t]+", tail)
        if next_section:
            tail = tail[:next_section.start()]
        end = re.search(r"Do\s+not\s+store\s+wet\s+or\s+damp\s+parts\.", tail, re.IGNORECASE)
        if end is None:
            continue
        block = tail[:end.end()].strip()
        if (re.search(r"Allow\s+all\s+parts\s+to\s+air\s+dry", block, re.IGNORECASE)
                and re.search(r"Store\s+dry\s+parts\s+in\s+a\s+clean,?\s+cool\s+place", block,
                              re.IGNORECASE)
                and len(block) <= SOURCE_PASSAGE_MAX):
            warnings = [item for item in _complete_bullet_items(text)
                        if re.search(r"mold", item, re.IGNORECASE)
                        and re.search(r"do\s+not\s+use", item, re.IGNORECASE)]
            return ["\n\n".join([block, *warnings])]
    return []


def _paper_pack_rows(question: str, text: str) -> list[str]:
    if not (re.search(r"용지|종이|paper", question, re.IGNORECASE)
            and re.search(r"규격|크기|치수|끝\s*표시|size|dimension|end.{0,8}(?:mark|pack)",
                          question, re.IGNORECASE)):
        return []
    if re.search(
        "".join((  # noqa: FLY002
            r"전체|속도|감지|센서|장착|로딩|넣|끼우|교체|사용법|조작|방법|절차|",
            r"all|speed|detect|sensor|load|insert|replace|how|procedure",
        )),
        question, re.IGNORECASE,
    ):
        return []
    start = re.search(r"(?im)^[ \t]*Pack\s+Style\s*:", text)
    if start is None:
        return []
    tail = text[start.start():]
    end = re.search(r"(?im)^[ \t]*(?:Loading|Paper\s+Detectors|\d+\.\d+)\b", tail)
    if end is None:
        return []
    block = tail[:end.start()].strip()
    if (re.search(r"Pack\s+Size\s*:", block, re.IGNORECASE)
            and re.search(r"End-of-Pack\s*:", block, re.IGNORECASE)
            and len(block) <= SOURCE_PASSAGE_MAX):
        return [block]
    return []


def _light_output_rows(question: str, text: str) -> list[str]:
    if not (re.search(r"출력|강도|irradian|intensity|output", question, re.IGNORECASE)
            and re.search(r"low|high|낮은|높은", question, re.IGNORECASE)):
        return []
    if re.search(r"안정|수명|사양\s*전체|stability|lifetime|all\s+spec", question,
                 re.IGNORECASE):
        return []
    start = re.search(r"(?im)^[ \t]*(?:광\s*출력(?!\s*안정)|irradiance|light\s+output)\b", text)
    if start is None:
        return []
    tail = text[start.start():]
    boundary = re.search(
        "".join((  # noqa: FLY002
            r"(?im)^[ \t]*(?:광\s*출력\s*안정|유효\s*조사\s*면적|",
            r"stability|effective\s+area|LED\s*수명)",
        )),
        tail[1:],
    )
    if boundary is None:
        return []
    block = tail[:boundary.start() + 1].strip()
    if (re.search(r"Low\s*:", block, re.IGNORECASE)
            and re.search(r"High\s*:", block, re.IGNORECASE)
            and re.search(r"\d\s*(?:cm|㎝)", block, re.IGNORECASE)
            and len(block) <= SOURCE_PASSAGE_MAX):
        return [block]
    return []


def _record_capacity_section(question: str, text: str) -> list[str]:
    if not (re.search(r"기록|메모리|저장|record|memory|histor", question, re.IGNORECASE)
            and re.search(r"몇|얼마나|개수|용량|how\s+many|capacity|count", question,
                          re.IGNORECASE)):
        return []
    if re.search(r"삭제|전체|복원|delete|restore|all\s+steps", question, re.IGNORECASE):
        return []
    heading = re.search(r"(?im)^[ \t]*\d+(?:\.\d+)+\.?[ \t]+Memory\s+mode\b", text)
    if heading is None:
        return []
    tail = text[heading.start():]
    body_start = tail.find("\n") + 1
    end = re.search(r"(?m)^[ \t]*\d+(?:\.\d+)+\.?[ \t]+", tail[body_start:])
    block = tail[:body_start + end.start()].strip() if end else tail.strip()
    if (re.search(r"stored\s+in\s+memory", block, re.IGNORECASE)
            and re.search(r"No\.?\s*\d+.{0,30}No\.?\s*\d+", block, re.IGNORECASE)
            and len(block) <= SOURCE_PASSAGE_MAX):
        return [block]
    return []


def _manual_output_spec(question: str, text: str) -> list[str]:
    if not (re.search(r"manual|수동", question, re.IGNORECASE)
            and re.search(r"출력|범위|단계|output|range|level", question, re.IGNORECASE)):
        return []
    if re.search(r"전체|알람|경보|전환|방법|어떻게|baby|prewarm|alarm|how|switch", question,
                 re.IGNORECASE):
        return []
    start = re.search(r"(?im)^[ \t]*Operation\s+Mode\b", text)
    if start is None:
        return []
    tail = text[start.start():]
    manual = re.search(r"(?im)^[ \t]*-?Manual\b[^\n]*", tail)
    limits = re.search(r"(?is)Manual\s+Control\s+Range\s+\d+\s*[~\u2013-]\s*\d+\s*%[^\n]*", tail)
    first_mode = re.search(r"(?im)^[ \t]*-\s*[A-Za-z]", tail)
    if manual is None or limits is None or first_mode is None:
        return []
    if not re.search(r"\d.{0,20}(?:W|㎽|mW|µW).{0,10}(?:cm|㎠|㎝)", manual[0], re.IGNORECASE):
        return []
    # The table's shared tolerance belongs to the selected row. Keep its
    # original header, the complete row and its range; no other mode values.
    block = "\n".join([tail[:first_mode.start()].strip(), manual[0].strip(), limits[0].strip()])
    return [block] if len(block) <= SOURCE_PASSAGE_MAX else []


def _eye_protection_warning(question: str, text: str) -> list[str]:
    if not (re.search(r"눈|eye", question, re.IGNORECASE)
            and re.search(r"보호|안대|가리|protect|patch|shield", question, re.IGNORECASE)):
        return []
    if re.search("".join((  # noqa: FLY002
        r"거리|간격|온도|보온|히터|전체|절차|순서|설치|",
        r"distance|spacing|temperature|warming|heater|warmer|procedure|steps|install",
    )), question, re.IGNORECASE):
        return []
    result: list[str] = []
    for start in re.finditer(r"Eye\s+protection\s*:", text, re.IGNORECASE):
        tail = text[start.start():]
        boundary = re.search(
            r"\n[ \t]*\n|(?m:^[ \t]*(?:[•⚫]|\d+[.)][ \t]+|WARNING|CAUTION))", tail,
        )
        block = tail[:boundary.start()].strip() if boundary else tail.strip()
        if (re.search(r"Do\s+not\s+look\s+directly", block, re.IGNORECASE)
                and re.search(r"always\s+protect", block, re.IGNORECASE)
                and re.search(r"periodically", block, re.IGNORECASE)
                and re.search(r"infection", block, re.IGNORECASE)
                and re.search(r"patients\s+adjacent", block, re.IGNORECASE)
                and len(block) <= SOURCE_PASSAGE_MAX):
            result.append(block)
    return result


def _pump_finish_step(question: str, text: str) -> list[str]:
    if re.search(r"전체|전반|세척|청소|소독|보관|all\s+steps|clean|steriliz|stor", question,
                 re.IGNORECASE):
        return []
    if not (re.search(r"유축|pump", question, re.IGNORECASE)
            and re.search(r"끝|마무리|종료|finish|over", question, re.IGNORECASE)
            and re.search(r"깔때기|funnel|분리|떼|detach|remove", question, re.IGNORECASE)):
        return []
    boundaries = list(re.finditer(
        r"(?m)^[ \t]*(?:\(\d+\)|\d+(?:\.\d+)*\.?[ \t]+[A-Za-z가-힣]|WARNING|CAUTION)",
        text, re.IGNORECASE,
    ))
    result: list[str] = []
    for index, marker in enumerate(boundaries):
        if not re.match(r"\s*\(\d+\)", marker[0]):
            continue
        end = boundaries[index + 1].start() if index + 1 < len(boundaries) else len(text)
        block = text[marker.start():end].strip()
        if (re.search(r"session\s+is\s+over|pumping\s+is\s+finished", block, re.IGNORECASE)
                and re.search(r"turn\s+off", block, re.IGNORECASE)
                and re.search(r"finger\s+between", block, re.IGNORECASE)
                and len(block) <= SOURCE_PASSAGE_MAX):
            result.append(block)
    return result


def _function_capability_intro(question: str, text: str) -> list[str]:
    if not re.search(r"모드|mode|영향|같이|affect|vacuum|진공", question, re.IGNORECASE):
        return []
    if re.search(r"방법|조작|전체|전반|어떻게|누르|버튼|how|press|button|절차|순서", question,
                 re.IGNORECASE):
        return []
    headings = list(re.finditer(r"(?im)^[ \t]*\(\d+\)[ \t]+([A-Za-z ]+)\s+Function\b", text))
    result: list[str] = []
    for heading in headings:
        if normalized(heading[1]) not in normalized(question):
            continue
        tail = text[heading.start():]
        boundary = re.search(r"(?m)^[ \t]*(?:[①-⑳]|\(\d+\))", tail[1:])
        if boundary is None:
            continue
        block = tail[:boundary.start() + 1].strip()
        if 25 <= len(block) <= SOURCE_PASSAGE_MAX:
            result.append(block)
    return result


def _probe_care_warning(question: str, text: str) -> list[str]:
    if re.search(r"전체|전반|절차|순서|방법|method|all\s+steps|procedure|steps",
                 question, re.IGNORECASE):
        return []
    # "How should power be handled before cleaning?" asks for the warning,
    # whereas "when and how should I clean?" requests the full procedure.
    method_query = re.sub(
        r"(?:전원|코드|플러그).{0,12}어떻게|how.{0,25}(?:power|plug|cord)",
        "", question, flags=re.IGNORECASE,
    )
    if re.search(r"어떻게|\bhow\b", method_query, re.IGNORECASE):
        return []
    if not re.search(r"probe|프로브|센서", question, re.IGNORECASE):
        return []
    restriction = re.search(r"autoclave|가스|고압|steriliz", question, re.IGNORECASE)
    timing = (re.search(r"청소|세척|닦|clean", question, re.IGNORECASE)
              and re.search(r"언제|주기|전원|when|power|after", question, re.IGNORECASE))
    if not restriction and not timing:
        return []
    pure_timing_power = (
        re.search(r"주기|언제|빈도|frequency|when|how\s+often", question, re.IGNORECASE)
        and re.search(r"전원|power", question, re.IGNORECASE)
        and not re.search(
            r"어떻게|how|방법|약품|용액|소독|멸균|침수|보관|화면|chemical|solution|steriliz|disinfect|stor",
            question, re.IGNORECASE,
        )
    )
    markers = list(re.finditer(
        r"(?im)^[ \t]*(?:WARNING|CAUTION|경고|주의|\(\d+\)|\d+(?:\.\d+)*\.?[ \t]+[A-Za-z가-힣])",
        text,
    ))
    result: list[str] = []
    for index, marker in enumerate(markers):
        if not re.match(r"\s*(?:WARNING|CAUTION|경고|주의)", marker[0], re.IGNORECASE):
            continue
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        block = text[marker.start():end].strip()
        if (re.search(r"autoclave|고압\s*멸균", block, re.IGNORECASE)
                and re.search(r"probe|transducer|프로브|센서", block, re.IGNORECASE)
                and len(block) <= SOURCE_PASSAGE_MAX):
            if pure_timing_power:
                items = [item for item in _complete_bullet_items(block)
                         if re.search(r"clean.{0,30}probe", item, re.IGNORECASE | re.DOTALL)
                         and re.search(r"after|before|each|every", item, re.IGNORECASE)
                         and re.search(r"turn\s+off.{0,40}(?:when|while)\s+cleaning", item,
                                       re.IGNORECASE | re.DOTALL)]
                if items:
                    result.extend(items)
                    continue
            result.append(block)
    return result


def _probe_position_frequency_sections(question: str, text: str) -> list[str]:
    """Retain all explicit frequency/angle branches for an unspecified probe."""
    if not (re.search(r"프로브|probe|transducer", question, re.IGNORECASE)
            and re.search(r"겔|젤|gel|각도|위치|angle|position", question, re.IGNORECASE)):
        return []
    if re.search(r"MHz|메가헤르츠|주파수|세척|청소|소독|고장|배터리|교체|"  # noqa: ISC003
                 + r"clean|disinfect|fail|battery|replace", question, re.IGNORECASE):
        return []
    return [block for block in _numbered_section_passages(text)
            if re.search(r"How\s+to\s+use", block, re.IGNORECASE)
            and re.search(r"ultrasound\s+gel", block, re.IGNORECASE)
            and len(re.findall(r"If\s+use\s+[\d, .]+MHz\s+probe", block, re.IGNORECASE)) > 1
            and re.search(r"angle", block, re.IGNORECASE)]


def _main_body_cleaning_section(question: str, text: str) -> list[str]:
    """Keep the complete named component section, including its local cautions."""
    if not (re.search(r"본체|케이스|main\s*body|casing|housing", question, re.IGNORECASE)
            and re.search(r"세척|청소|닦|담가|담그|액체|clean|wipe|immers|liquid", question,
                          re.IGNORECASE)):
        return []
    if re.search(r"프로브|센서|화면|전체|모든|probe|sensor|screen|all|entire", question,
                 re.IGNORECASE):
        return []
    return [block for block in _numbered_section_passages(text)
            if re.search(r"(?m)^\d+(?:\.\d+)+\.?[ \t]+(?:본체|main\s*body|body)[ \t]*(?:\n|$)",
                        block, re.IGNORECASE)
            and re.search(r"세척|닦|clean|wipe", block, re.IGNORECASE)]


def _height_weight_unit_rows(question: str, text: str) -> list[str]:
    if not (re.search(r"(?<![A-Za-z])HT(?![A-Za-z])", question, re.IGNORECASE)
            and re.search(r"(?<![A-Za-z])WT(?![A-Za-z])", question, re.IGNORECASE)
            and re.search(r"뜻|단위|의미|unit|mean", question, re.IGNORECASE)):
        return []
    if re.search(r"DISP|버튼|진입|나가|들어|어디|방법|경로|계산|측정|전체|"  # noqa: ISC003
                 + r"button|enter|exit|where|how|calculate|measure|all",
                 question, re.IGNORECASE):
        return []
    for section in _numbered_section_passages(text):
        if not re.search(r"UNIT\s+Setting", section.splitlines()[0], re.IGNORECASE):
            continue
        markers = list(re.finditer(r"(?m)^[ \t]*[-•][ \t]*", section))
        rows: list[str] = []
        for index, marker in enumerate(markers):
            end = markers[index+1].start() if index+1 < len(markers) else len(section)
            row = section[marker.start():end].strip()
            if re.match(r"-\s*(?:HT|WT)\s*:", row, re.IGNORECASE):
                rows.append(row)
        if (len(rows) == 2 and all(len(row) <= SOURCE_PASSAGE_MAX for row in rows)
                and any(re.search(r"HT\s*:.*height", row, re.IGNORECASE) for row in rows)
                and any(re.search(r"WT\s*:.*weight", row, re.IGNORECASE) for row in rows)):
            return rows
    return []


def _air_tube_washing_policy(question: str, items: list[str]) -> list[str]:
    """Keep the complete air-tube exception and its replacement condition."""
    if not (re.search(r"에어\s*튜브|공기\s*튜브|air\s*tub", question, re.IGNORECASE)
            and re.search(r"씻|세척|청소|소독|clean|wash|sanitiz", question, re.IGNORECASE)):
        return []
    if re.search(r"젖병|깔때기|밸브|본체|전체|절차|순서|보관|건조|"  # noqa: ISC003
                 + r"bottle|funnel|valve|\bbody\b|main\s*unit|housing|"
                 + r"whole|procedure|steps|stor|dry",
                 question, re.IGNORECASE):
        return []
    return [item for item in items
            if re.search(r"Do\s+not\s+(?:wash|clean).{0,50}air\s*tub", item,
                         re.IGNORECASE | re.DOTALL)
            and re.search(r"Replace\s+damaged\s+or\s+dirty", item, re.IGNORECASE)]


def _oxygen_aerosol_environment_warning(question: str, items: list[str]) -> list[str]:
    """Keep an independent operating-environment prohibition, not its warning box."""
    if not (re.search(r"산소|스프레이|에어로졸|분무|oxygen|aerosol|spray", question,
                      re.IGNORECASE)
            and re.search(r"곳|방|환경|장소|사용|돌려|operate|use|room|area|environment",
                          question, re.IGNORECASE)):
        return []
    if re.search(r"포화|SpO2|농도|유량|경보|센서|통증|아프|유두|손가락|세척|청소|소독|"  # noqa: ISC003
                 + r"역류|깔때기|젖병|배터리|충전|어댑터|수리|서비스|점검|"
                 + r"온도|습도|보관|전체|절차|순서|"
                 + r"saturat|concentrat|flow|alarm|sensor|pain|nipple|finger|clean|wash|"
                 + r"sanitiz|disinfect|backflow|funnel|bottle|battery|charg|temperatur|"
                 + r"adapt[eo]r|repair|service|maintenance|humid|stor|whole|procedure|steps",
                 question, re.IGNORECASE):
        return []
    return [item for item in items
            if re.search(r"Do\s+not\s+operate\s+where", item, re.IGNORECASE)
            and re.search(r"aerosol|spray", item, re.IGNORECASE)
            and re.search(r"oxygen\s+is\s+being\s+administered", item, re.IGNORECASE)]


def _sanitizing_parts_steps(question: str, text: str) -> list[str]:
    if not (re.search(r"부품|구성품|젖병|parts?|bottle", question, re.IGNORECASE)
            and re.search(r"끓|집게|boil|tongs", question, re.IGNORECASE)):
        return []
    if re.search(r"모유.{0,15}(?:해동|데우|끓|냉동|보관|저장)|전자레인지|세제|식기세척|"  # noqa: ISC003
                 + r"milk.{0,30}(?:stor|freez)|microwave|thaw|detergent|dishwash",
                 question, re.IGNORECASE):
        return []
    starts = list(re.finditer(r"(?m)^[ \t]*\(1\)[ \t]*(?:Disassemble|모유)", text,
                             re.IGNORECASE))
    for start in starts:
        tail = text[start.start():]
        boundary = re.search(r"(?m)^[ \t]*\d+(?:\.\d+)*\.?[ \t]+[A-Za-z가-힣]", tail)
        if boundary:
            tail = tail[:boundary.start()]
        end = re.search(r"Do\s+not\s+store\s+wet\s+or\s+damp\s+parts[.]|"  # noqa: ISC003
                        + r"젖거나[^.]{0,40}보관하지\s*마십시오[.]",
                        tail, re.IGNORECASE)
        if end:
            block = tail[:end.end()].strip()
            if (re.search(r"tongs|집게", block, re.IGNORECASE)
                    and re.search(r"cool|식히", block, re.IGNORECASE)
                    and re.search(r"air\s*dry|건조", block, re.IGNORECASE)
                    and len(block) <= SOURCE_PASSAGE_MAX):
                return [block]
    return []


def _skin_sensor_attachment_section(question: str, text: str) -> list[str]:
    if not (re.search(r"skin\s*temp|피부\s*온도|S\d", question, re.IGNORECASE)
            and re.search(r"붙|부착|맨\s*피부|옷|attach|adhere|bare\s*skin|clothes", question,
                          re.IGNORECASE)):
        return []
    if re.search(r"세척|소독|교체|수리|경보|상한|하한|히터\s*출력|"  # noqa: ISC003
                 + r"alarm|threshold|clean|disinfect|repair|replace|heater\s*output",
                 question, re.IGNORECASE):
        return []
    start = re.search(r"(?im)^[ \t]*\d+(?:\.\d+)+[ \t]+Skin\s+temperature\s+sensor", text)
    if not start:
        return []
    tail = text[start.start():]
    end = re.search(r"(?m)^[ \t]*\d+(?:\.\d+)+[ \t]+", tail[start.end()-start.start():])
    block = tail[:end.start()+start.end()-start.start()].strip() if end else tail.strip()
    if (re.search(r"Attach.{0,20}S\d", block, re.IGNORECASE)
            and re.search(r"bare\s+skin", block, re.IGNORECASE)
            and len(block) <= SOURCE_PASSAGE_MAX):
        return [block]
    return []


def _patient_type_choices(question: str, items: list[str]) -> list[str]:
    if not (re.search(r"patient\s*type|환자\s*(?:종류|유형)", question, re.IGNORECASE)
            and re.search(r"항목|목록|종류|선택|choices?|options?|list", question, re.IGNORECASE)):
        return []
    if re.search(r"경보|압력|위험|아기|나이|어디|경로|전체|방법|"  # noqa: ISC003
                 + r"alarm|pressure|risk|age|where|how|all",
                 question, re.IGNORECASE):
        return []
    return [item for item in items if re.search(r"Select.{0,8}Patient\s*Type", item, re.IGNORECASE)
            and re.search(r"choose", item, re.IGNORECASE)]


def _automatic_interval_choices(question: str, text: str) -> list[str]:
    if not (re.search(r"NIBP|혈압", question, re.IGNORECASE)
            and re.search(r"자동|automatic|auto", question, re.IGNORECASE)
            and re.search(r"간격|주기|interval", question, re.IGNORECASE)):
        return []
    if re.search(r"오류|고장|압력|환자|커프|전체|절차|error|fail|pressure|patient|cuff|procedure",
                 question, re.IGNORECASE):
        return []
    remaining = re.sub(r"(?:분|초|시간)\s*단위", "", question)
    if re.search(r"단위|mmHg|kPa|units?", remaining, re.IGNORECASE):
        return []
    headings = list(re.finditer(r"(?m)^[ \t]*\d+(?:\.\d+)+[ \t]+[^\n]+", text))
    for index, heading in enumerate(headings):
        if not re.search(r"Setting\s+NIBP|NIBP\s*설정", heading[0], re.IGNORECASE):
            continue
        end = headings[index+1].start() if index+1 < len(headings) else len(text)
        choices = [item for item in _complete_bullet_items(text[heading.end():end])
                   if re.search(r"Select.{0,8}Interval", item, re.IGNORECASE)
                   and re.search(r"min|분", item, re.IGNORECASE)]
        if choices:
            return [heading[0].strip() + "\n" + item for item in choices]
    return []


def _occupied_hood_warning(question: str, items: list[str]) -> list[str]:
    if not (re.search(r"후드|hood", question, re.IGNORECASE)
            and re.search(r"아기|신생아|영아|infant|baby", question, re.IGNORECASE)
            and re.search(r"들어\s*올|올려|lift|raise", question, re.IGNORECASE)):
        return []
    if re.search(r"산소|습도|청소|세척|분해|전체\s*절차|oxygen|humidity|clean|disassembl",
                 question, re.IGNORECASE):
        return []
    return [item for item in items
            if re.search(r"Do\s+not\s+raise\s+the\s+hood|후드를\s*들어올리지", item,
                         re.IGNORECASE)
            and re.search(r"infant|신생아|영아", item, re.IGNORECASE)
            and re.search(r"access\s+panels?|access\s+doors?", item, re.IGNORECASE)]


def _adjacent_stack_warning(question: str, items: list[str]) -> list[str]:
    if not re.search(r"인접|적층|쌓|장비.{0,12}(?:옆|위)|adjacent|stack", question, re.IGNORECASE):
        return []
    if re.search(r"전원|어댑터|거리|세척|전체|산소|power|adapto?r|distance|clean|all|oxygen",
                 question, re.IGNORECASE):
        return []
    return [item for item in items
            if re.search(r"adjacent.{0,50}stack|인접.{0,50}적층", item, re.IGNORECASE | re.DOTALL)
            and re.search(r"necessary|verify|verification|observ|필요|확인", item, re.IGNORECASE)]


def _fully_charged_adapter_notice(question: str, text: str) -> list[str]:
    if not (full_charge_question(question)
            and re.search(r"아답터|어댑터|adapto?r", question, re.IGNORECASE)):
        return []
    if re.search(r"몇|얼마|시간|보관|수명|고장|전체|how\s*long|hours|storage|life|fail",
                 question, re.IGNORECASE):
        return []
    flat = " ".join(text.split())
    sentences = re.findall(r"충전이\s*다\s*되었으면[^.]{0,200}[.]", flat)
    return [sentence for sentence in sentences
            if re.search(r"(?:아답터|어댑터).{0,40}제거", sentence)]


def _unfinished_installation_notice(question: str, text: str) -> list[str]:
    if not (re.search(r"설치|install", question, re.IGNORECASE)
            and re.search(r"덜|안\s*끝|미완료|not.{0,15}(?:complete|finish)|unfinished", question,
                          re.IGNORECASE)
            and re.search(r"전원|power|turn\s*on", question, re.IGNORECASE)):
        return []
    if re.search(r"전체|절차|순서|위치|간격|all|procedure|steps|location|distance", question,
                 re.IGNORECASE):
        return []
    flat = " ".join(text.split())
    match = re.search(
        r"Power\s+off\s+when[^.]{0,100}not\s+fully\s+installed[.]"  # noqa: ISC003
        + r"(?:\s*Otherwise[^.]{0,100}[.])?", flat, re.IGNORECASE,
    )
    return [match[0]] if match else []


def _rhythm_indicator_definition(question: str, items: list[str]) -> list[str]:
    if not (re.search(r"심박\s*리듬|heart\s*rhythm", question, re.IGNORECASE)
            and re.search(r"색|빨강|red|green|colo[ur]+", question, re.IGNORECASE)):
        return []
    if re.search(r"소리|인쇄|수리|경보\s*설정|전체|sound|print|repair|alarm\s*set|all", question,
                 re.IGNORECASE):
        return []
    return [item for item in items
            if re.search(r"심박\s*리듬|heart\s*rhythm", item, re.IGNORECASE)
            and re.search(r"녹색|green", item, re.IGNORECASE)
            and re.search(r"적색|빨강|\bred\b", item, re.IGNORECASE)]


def _trace_offset_section(question: str, text: str) -> list[str]:
    if not (re.search(r"DOP\s*\d|도플러|doppler", question, re.IGNORECASE)
            and re.search(r"offset|옵셋|오프셋|trace\s*separation", question, re.IGNORECASE)):
        return []
    if re.search(
        r"알람|경보|인쇄\s*속도|자동\s*인쇄|환자\s*(?:이름|종류|유형)|날짜|언어|전체|"  # noqa: ISC003
        + r"alarm|printing\s*speed|auto\s*print|patient\s*(?:name|type)|"
        + r"date|language|all\s*settings",
        question, re.IGNORECASE,
    ):
        return []
    return [block for block in _numbered_section_passages(text)
            if re.search(r"DOP\s*\d.{0,25}(?:offset|옵셋|오프셋)|Doppler.{0,25}trace\s*separation",
                         block.splitlines()[0], re.IGNORECASE)
            and len(block) <= SOURCE_PASSAGE_MAX]


def _standby_setting_section(question: str, text: str) -> list[str]:
    if not re.search(r"standby|대기\s*(?:설정|모드|시간)", question, re.IGNORECASE):
        return []
    if re.search(
        r"밝기|볼륨|알람|경보|날짜|전체|고장|안\s*[되돼]|꺼지지|brightness|volume|alarm|"  # noqa: ISC003
        + r"date|all\s*settings|fail|not\s*work", question, re.IGNORECASE,
    ):
        return []
    headings = list(re.finditer(r"(?m)^[ \t]*\d+(?:\.\d+)+\.?[ \t]+[^\n]+", text))
    result: list[str] = []
    for index, heading in enumerate(headings):
        if not re.search(r"set\s+(?:the\s+)?standby|대기.{0,8}설정", heading[0], re.IGNORECASE):
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        block = text[heading.start():end].strip()
        if (re.search(r"select|선택", block, re.IGNORECASE)
                and re.search(r"screen|display|화면", block, re.IGNORECASE)
                and len(block) <= SOURCE_PASSAGE_MAX):
            result.append(block)
    return result


def _thick_wrapping_warning(question: str, items: list[str]) -> list[str]:
    if not (re.search(r"두꺼운|꽉|thick|tightly", question, re.IGNORECASE)
            and re.search(r"담요|감싸|조여|blanket|wrap", question, re.IGNORECASE)):
        return []
    if re.search(
        r"세척|소독|전원|시간|온도\s*설정|전체|순서|밝기|안대|눈|거리|위치|배치|"  # noqa: ISC003
        + r"clean|disinfect|power|eye|distance|position|"
        + r"duration|temperature\s*set|whole|procedure|brightness", question, re.IGNORECASE,
    ):
        return []
    return [item for item in items
            if re.search(r"두꺼운\s*담요|thick\s*blanket", item, re.IGNORECASE)
            and re.search(r"감싸|wrap", item, re.IGNORECASE)
            and re.search(r"환자|patient|infant", item, re.IGNORECASE)
            and re.search(r"안됩|금지|상승|열|do\s*not|must\s*not|heat|temperature",
                          item, re.IGNORECASE)]


def _visible_damage_use_warning(question: str, items: list[str]) -> list[str]:
    damage = re.search(
        r"균열|깨졌|파손|손상|금간|(?:케이스|외관|외함|본체|표면).{0,15}금|damage|crack|broken",
        question, re.IGNORECASE,
    )
    use = re.search(r"써도|사용|측정|켜|작동|\buse\b|operat|measure", question, re.IGNORECASE)
    if not (damage and use):
        return []
    if re.search(
        r"주기|정기|매주|매년|점검|검사|전체|관리|수리|교체|재사용|재개|가격|요금|"  # noqa: ISC003
        + r"배터리|케이블|프로브|어댑터|interval|periodic|weekly|annual|inspect|check|"
        + r"maintenan|repair|replace|resume|again|price|battery|cable|probe|adapto?r",
        question, re.IGNORECASE,
    ):
        return []
    # This independent sentence includes the complete damage condition; the
    # preceding recommended periodic inspection is a separate request scope.
    pattern = re.compile(
        r"\bDo\s+not\s+use\s+(?:the\s+)?(?:device|monitor|incubator|product|unit)\s+"  # noqa: ISC003
        + r"if\s+there\s+is\s+any\s+visible\s+sign\s+of\s+damage[^.!?]{0,100}[.!?]",
        re.IGNORECASE,
    )
    return [match.group().strip() for item in items
            for match in pattern.finditer(" ".join(item.split()))]


def _operator_sleep_question(question: str) -> bool:
    """Distinguish operator sleep during use from a device or patient state."""
    if re.search(
        r"절전|대기|슬립|수면\s*(?:모드|무호흡|검사)|무호흡|환자|아기|영아|"  # noqa: ISC003
        + r"sleep\s*(?:mode|apn(?:oe|e)a|study)|standby|patient|infant|baby|"
        + r"배터리|충전|소독|세척|분해|수리|전체|battery|charg|disinfect|clean|repair",
        question, re.IGNORECASE,
    ):
        return False
    sleepy = re.search(r"졸리|졸려|졸린|졸음|자도|자면서|잠들|잠든|수면|"  # noqa: ISC003
                       + r"\b(?:sleep|sleeping|asleep|drowsy)\b", question, re.IGNORECASE)
    use = re.search(r"유축|사용|켜\s*놓|작동|operate|pump|\b(?:use|using)\b",
                    question, re.IGNORECASE)
    return bool(sleepy and use)


def _operator_sleep_warning(question: str, items: list[str]) -> list[str]:
    if not _operator_sleep_question(question):
        return []
    return [item for item in items if _operator_sleep_prohibition(item)]


def _operator_sleep_prohibition(text: str) -> bool:
    if re.search(r"patient|infant|baby|sleep\s*mode|환자|아기|영아|수면\s*모드", text,
                 re.IGNORECASE):
        return False
    return bool(re.search(
        r"(?:do\s+not|never)\s+(?:use|operate)\b.{0,60}"  # noqa: ISC003
        + r"(?:while|when).{0,40}(?:sleeping|asleep|drowsy)|"
        + r"(?:졸리|졸음|잠든|수면).{0,45}사용하지",
        text, re.IGNORECASE | re.DOTALL,
    ))


def _teat_soother_warning(question: str, items: list[str]) -> list[str]:
    if not (re.search(r"젖꼭지|젖병|teats?|bottle\s*nipple", question, re.IGNORECASE)
            and re.search(r"공갈|pacifier|soother", question, re.IGNORECASE)):
        return []
    if re.search(r"세척|소독|검사|점검|교체|보관|방법|절차|전체|"  # noqa: ISC003
                 + r"clean|steriliz|inspect|replace|stor|procedure|all\s+steps",
                 question, re.IGNORECASE):
        return []
    return [item for item in items
            if re.search(r"(?:never|do\s+not)\s+use\s+feeding\s+teats?\s+as\s+a\s+soother",
                         item, re.IGNORECASE)]


def _milk_in_main_body_warning(question: str, items: list[str]) -> list[str]:
    if not (re.search(r"모유|breast\s*milk|milk", question, re.IGNORECASE)
            and re.search(r"역류|reverse|backflow", question, re.IGNORECASE)
            and re.search(r"본체|main\s*body", question, re.IGNORECASE)):
        return []
    if re.search(
        r"예방|방지|분해|수리|세척|청소|전체|절차|본체.{0,12}(?:안|않|없)|튜브에만|"  # noqa: ISC003
        + r"prevent|disassembl|repair|clean|procedure|not.{0,15}main\s*body|only.{0,15}tube",
        question, re.IGNORECASE,
    ):
        return []
    return [item for item in items
            if re.search(r"milk.{0,50}reverse.{0,30}main\s*body", item, re.IGNORECASE | re.DOTALL)
            and re.search(r"stop\s+using.{0,30}immediately", item, re.IGNORECASE | re.DOTALL)
            and re.search(r"(?:call|contact).{0,35}service\s*cent(?:er|re)",
                          item, re.IGNORECASE | re.DOTALL)]


def _oximeter_specification_rows(question: str, text: str) -> list[str]:  # noqa: PLR0911
    if not (re.search(r"SpO2|산소포화도|oxygen\s*saturation", question, re.IGNORECASE)
            and re.search(r"사양|정확도|측정\s*범위|specification|accuracy|measurement\s*range",
                          question, re.IGNORECASE)):
        return []
    if re.search(
        r"알람|경보|설정|배터리|전원|전압|온도|청소|소독|연결|사용법|원리|전체|"  # noqa: ISC003
        + r"alarm|setting|battery|power|voltage|temperature|clean|disinfect|connect|"
        + r"procedure|principle|all\s+spec",
        question, re.IGNORECASE,
    ):
        return []
    start = re.search(r"(?im)^[ \t]*(?:Masimo[ \t]+)?Oximeter[ \t]*$", text)
    if start is None:
        return []
    tail = text[start.start():]
    end = re.search(r"(?im)^[ \t]*Power[ \t]*$", tail)
    if end is None:
        return []
    block = tail[:end.start()].strip()
    # A referenced footnote may be outside this table/row; keep ordinary context.
    if re.search(r"[*†‡]|\[\d+\]", block):
        return []
    # Keep the complete measurement table, notes and interference warnings;
    # low-perfusion accuracy must retain both amplitude and transmission.
    if (re.search(r"\bRange\b", block, re.IGNORECASE)
            and re.search(r"\bAccuracy\b", block, re.IGNORECASE)
            and 25 <= len(block) <= SOURCE_PASSAGE_MAX):
        return [block]
    return []


def _battery_power_rows(question: str, text: str) -> list[str]:  # noqa: PLR0911
    if not (battery_runtime_question(question) or _battery_duration_question(question)):
        return []
    if re.search(
        r"시험|검사|테스트|점검|수명|교체|방법|절차|순서|전압|어댑터|온도|습도|전체|"  # noqa: ISC003
        + r"test|check|lifespan|replace|procedure|voltage|adapto?r|temperature|humidity|all\s+spec|"
        + r"USB|RS.?232|포트|위치|단자|알람|경보|음량|볼륨|밝기|최대|최소|무게|치수|크기|"
        + r"ports?|location|alarm|volume|brightness|maximum|minimum|"
        + r"weight|dimensions?|size|NIBP|SpO2|\d+\s*%",
        question, re.IGNORECASE,
    ):
        return []
    start = re.search(r"(?im)^[ \t]*(?:Power[ \t]*\n\s*)?Internal\b", text)
    if start is None:
        return _battery_pack_and_charger_rows(text)
    tail = text[start.start():]
    end = re.search(r"(?im)^[ \t]*External\b", tail)
    if end is None:
        return []
    block = tail[:end.start()].strip()
    # A referenced footnote may be outside this table/row; keep ordinary context.
    if re.search(r"[*†‡]|\[\d+\]", block):
        return []
    # A note outside the row may qualify its numbers; do not detach that note.
    after = tail[end.start():]
    if re.search(r"(?:note|주의|조건).{0,180}(?:battery|operating\s*time|runtime|배터리)|"  # noqa: ISC003
                 + r"(?:battery|operating\s*time|runtime|배터리).{0,80}(?:if|when|under|조건)",
                 after, re.IGNORECASE | re.DOTALL):
        return []
    if (re.search(r"battery|rechargeable|Li-ion|배터리", block, re.IGNORECASE)
            and _has_battery_duration(block) and len(block) <= SOURCE_PASSAGE_MAX):
        return [block]
    return []


def _battery_pack_and_charger_rows(text: str) -> list[str]:
    """Preserve adjacent complete pack/charger tables rather than individual values."""
    start = re.search(r"(?im)^[ \t]*Battery\s+pack[ \t]*$", text)
    if start is None:
        return []
    tail = text[start.start():]
    boundary = re.search(r"(?im)^[ \t]*(?:\d+(?:\.\d+)*\.?[ \t]+[A-Za-z가-힣]|"  # noqa: ISC003
                         + r"Environmental|Accessories|Main\s+body|Display)[ \t]*", tail)
    block = tail[:boundary.start()].strip() if boundary else tail.strip()
    if (not re.search(r"(?im)^[ \t]*Battery\s+charger[ \t]*$", block)
            or not runtime_evidence(block)
            or not re.search(r"Charging\s+time", block, re.IGNORECASE)
            or re.search(r"[*†‡]|\[\d+\]", block)
            or len(block) > SOURCE_PASSAGE_MAX):
        return []
    after = tail[len(block):]
    if re.search(r"(?:note|주의|조건).{0,180}(?:battery|operating\s*time|runtime|배터리)|"  # noqa: ISC003
                 + r"(?:battery|operating\s*time|runtime|배터리).{0,80}(?:if|when|under|조건)",
                 after, re.IGNORECASE | re.DOTALL):
        return []
    return [block]


def _battery_duration_question(question: str) -> bool:
    return bool(re.search(r"battery|배터리|건전지", question, re.IGNORECASE)
                and re.search(r"충전|완충|꽉\s*채|charg", question, re.IGNORECASE)
                and re.search(r"시간|얼마나|how\s+long|hour|duration", question, re.IGNORECASE))


def _has_battery_duration(text: str) -> bool:
    compact = " ".join(text.split())
    return bool(re.search(
        r"(?:charg|operating\s*time|runtime|충전|작동\s*시간|사용\s*시간).{0,80}\d+(?:\.\d+)?\s*(?:h(?:ou)?rs?|hours?|시간)",
        compact, re.IGNORECASE,
    ))


def _retained_settings_passages(question: str, text: str) -> list[str]:
    if re.search(r"전체|절차|순서|방법|어떻게|변경|how|method|procedure|steps|change", question,
                 re.IGNORECASE):
        return []
    if not (re.search(r"삭제|비워|delete|clear", question, re.IGNORECASE)
            and re.search(r"설정|신체|남|유지|settings?|remain|retain", question, re.IGNORECASE)):
        return []
    boundaries = list(re.finditer(
        r"(?m)^[ \t]*(?:※[^\n]*|\(\d+\)[ \t]+[^\n]*|\d+(?:\.\d+)+[ \t]+[^\n]*)", text,
    ))
    result: list[str] = []
    for index, marker in enumerate(boundaries):
        if not re.search(r"(?:DATA|데이터)\s*삭제", marker[0], re.IGNORECASE):
            continue
        end = boundaries[index + 1].start() if index + 1 < len(boundaries) else len(text)
        block = text[marker.start():end].strip()
        if re.search(r"지워지지|삭제되지|not\s+(?:be\s+)?(?:deleted|cleared)", block,
                     re.IGNORECASE) and len(block) <= SOURCE_PASSAGE_MAX:
            result.append(block)
    return result


def _headlight_no_power_row(question: str, text: str) -> list[str]:
    if not (re.search(r"LED|headlight|헤드라이트|조명", question, re.IGNORECASE)
            and re.search(
                r"안\s*켜|켜지지|안\s*들어|does\s*not\s*turn\s*on|won.t\s*turn\s*on",
                question, re.IGNORECASE,
            )):
        return []
    # Only this explicit three-column layout is supported. Keep the entire
    # possible-cause/action row, including the failed-charge condition. The
    # next problem is a row boundary, not an extra condition of this failure.
    header = re.search(r"Problems\s+Possible\s+cause\s+Corrective\s+action", text, re.IGNORECASE)
    if header is None:
        return []
    row = re.search(r"Headlight\s+does\s+not\s+turn\s+on\.", text[header.end():], re.IGNORECASE)
    if row is None:
        return []
    start = header.end() + row.start()
    following = re.search(r"(?m)^\s*Brightness\s+decreases\s+during", text[start:], re.IGNORECASE)
    if following is None:
        return []
    body = text[start:start + following.start()].strip()
    if not re.search(r"after\s+charging.{0,80}service\s+center", body, re.IGNORECASE | re.DOTALL):
        return []
    block = header[0] + "\n" + body
    return [block] if len(block) <= SOURCE_PASSAGE_MAX else []


def _display_care_passages(question: str, text: str) -> list[str]:
    if not (re.search(r"화면|디스플레이|액정|display|screen", question, re.IGNORECASE)
            and re.search(r"청소|세척|닦|천|clean|wipe|cloth", question, re.IGNORECASE)):
        return []
    if re.search(r"프로브|센서|probe|sensor", question, re.IGNORECASE):
        return []
    # Keep a complete monitor/display section and its numbered parent intro.
    # A probe procedure may say only "device" inside an individual step; its
    # original heading, not that broad noun, determines the cleaning target.
    screen_heading = re.compile(
        r"(?im)^\s*\d+(?:\.\d+)+\s+(?:본체|모니터|화면|monitor|display|screen)\b",
    )
    other_heading = re.compile(
        r"(?im)^\s*\d+(?:\.\d+)+\s+(?:프로브|센서|probe|sensor)\b",
    )
    return [block for block in _numbered_section_passages(text)
            if screen_heading.search(block) and not other_heading.search(block)
            and re.search(r"화면|디스플레이|액정|display|screen", block, re.IGNORECASE)
            and re.search(r"닦|천|clean|wipe|cloth", block, re.IGNORECASE)]


def _screen_cloth_items(question: str, text: str) -> list[str]:
    if not (re.search(r"화면|디스플레이|액정|screen|display", question, re.IGNORECASE)
            and re.search(r"천|cloth", question, re.IGNORECASE)):
        return []
    if re.search(r"본체|센서|프로브|전체|용액|소독|약품|body|sensor|probe|solution|disinfect",
                 question, re.IGNORECASE):
        return []
    return [item for item in _complete_bullet_items(text)
            if re.search(r"화면|디스플레이|액정|screen|display", item, re.IGNORECASE)
            and re.search(r"부드럽.{0,20}마른.{0,10}천|soft.{0,15}dry\s+cloth", item,
                          re.IGNORECASE | re.DOTALL)]


def _serial_setup_passages(question: str, text: str) -> list[str]:
    """Keep shared setup steps and the explicit serial branch of a procedure.

    Require both documented network/serial conditions and a complete shared
    setup path. An ambiguous IP-then-serial description is not rewritten.
    """
    if (not re.search(r"serial|시리얼|COM\s*(?:port|포트)", question, re.IGNORECASE)
            or re.search(
                r"네트워크|network|\bIP\b|비교|차이|둘\s*다|both|다른|other|체크박스",
                question, re.IGNORECASE,
            )):
        return []
    conditional = r"경우|연결\s*시|\bif\b"
    serial = r"serial|시리얼"
    network = r"network|네트워크"
    required_shared = (r"환경설정|configuration", r"통신|communication", r"베드|침대|bed",
                       r"저장|\bsave\b")
    for section in _numbered_section_passages(text):
        if re.search(r"WARNING|CAUTION|경고|주의", section, re.IGNORECASE):
            continue
        steps = list(re.finditer(r"(?m)^[ \t]*\d{1,2}(?![.\d])(?:[ \t]*\n|[ \t]+(?=\S))", section))
        if len(steps) < 4:
            continue
        blocks = [section[step.start():steps[index + 1].start()
                          if index + 1 < len(steps) else len(section)].strip()
                  for index, step in enumerate(steps)]
        serial_steps = [block for block in blocks
                        if re.search(serial, block, re.IGNORECASE)
                        and re.search(conditional, block, re.IGNORECASE)]
        network_steps = [block for block in blocks
                         if re.search(network, block, re.IGNORECASE)
                         and re.search(conditional, block, re.IGNORECASE)]
        if len(serial_steps) != 1 or len(network_steps) != 1:
            continue
        # A step that mentions both branches cannot be split safely here.
        if re.search(network, serial_steps[0], re.IGNORECASE):
            continue
        other_device = r"아닌\s*(?:장비|기기)|other\s+than[^.!?\n]{0,60}(?:monitor|device)"
        optional_steps = [block for block in blocks
                          if re.search(other_device, block, re.IGNORECASE)
                          and re.search(conditional, block, re.IGNORECASE)]
        if any(normalized(label) in normalized(question) for block in optional_steps
               for label in re.findall(r"\[([^\]]+)\]", block)):
            continue
        selected = [block for block in blocks if block not in network_steps
                    and block not in optional_steps]
        body = "\n\n".join([section[:steps[0].start()].strip(), *selected])
        if (all(re.search(pattern, body, re.IGNORECASE) for pattern in required_shared)
                and len(body) <= SOURCE_PASSAGE_MAX):
            return [body]
    return []


def _workplace_scoped_passages(question: str, text: str) -> list[str]:  # noqa: C901, PLR0911, PLR0912
    """Select complete, explicitly identified independent source items.

    Scope is based on the requested component/action or conditional subject,
    not a product name or a stored answer. This avoids treating every unrelated
    warning on the same PDF page as part of a factual answer. Broad procedures
    and comparisons keep the ordinary full-section path.
    """
    items = _complete_bullet_items(text)
    independent = (
        _fully_charged_adapter_notice(question, text)
        or _unfinished_installation_notice(question, text)
        or _rhythm_indicator_definition(question, items)
        or _probe_position_frequency_sections(question, text)
        or _main_body_cleaning_section(question, text)
        or _height_weight_unit_rows(question, text)
        or _air_tube_washing_policy(question, items)
        or _oxygen_aerosol_environment_warning(question, items)
        or _sanitizing_parts_steps(question, text)
        or _skin_sensor_attachment_section(question, text)
        or _patient_type_choices(question, items)
        or _automatic_interval_choices(question, text)
        or _occupied_hood_warning(question, items)
        or _adjacent_stack_warning(question, items)
        or _consumable_replacement_items(question, items)
        or _probe_care_warning(question, text)
        or _cleaning_power_warning(question, text)
        or _battery_leak_warning(question, items)
        or _calibration_policy_items(question, items)
        or _battery_replacement_policy(question, items)
        or _optional_profile_items(question, items)
        or _brightness_range_items(question, items)
        or _disinfection_policy(question, text)
        or _single_port_passages(question, text)
        or _temperature_display_definition(question, text)
        or _dry_storage_steps(question, text)
        or _trace_offset_section(question, text)
        or _standby_setting_section(question, text)
        or _thick_wrapping_warning(question, items)
        or _visible_damage_use_warning(question, items)
        or _operator_sleep_warning(question, items)
        or _teat_soother_warning(question, items)
        or _milk_in_main_body_warning(question, items)
        or _oximeter_specification_rows(question, text)
        or _battery_power_rows(question, text)
        or _paper_pack_rows(question, text)
        or _light_output_rows(question, text)
        or _record_capacity_section(question, text)
        or _manual_output_spec(question, text)
        or _eye_protection_warning(question, text)
        or _pump_finish_step(question, text)
        or _function_capability_intro(question, text)
        or _retained_settings_passages(question, text)
        or _headlight_no_power_row(question, text)
        or _screen_cloth_items(question, text)
        or _display_care_passages(question, text)
        or _serial_setup_passages(question, text)
    )
    if independent:
        return independent
    if re.search(r"배터리|건전지|battery|batteries", question, re.IGNORECASE) and re.search(
        r"보관|안\s*쓸|안\s*쓰|storage|store|unused", question, re.IGNORECASE,
    ):
        selected = [item for item in items
                    if re.search(r"배터리|건전지|battery|batteries", item, re.IGNORECASE)
                    and re.search(r"보관|storage|store", item, re.IGNORECASE)]
        if selected:
            return selected
    absent_pacer = re.search(
        "".join((  # noqa: FLY002
            r"(?:심박\s*조율기|pacemaker|pace\s*maker).{0,12}(?:없|없는)|",
            r"(?:without|no)\s+(?:a\s+)?pacemaker",
        )), question, re.IGNORECASE,
    )
    if absent_pacer and not re.search(r"있는\s*환자|with\s+(?:a\s+)?pacemaker|비교|compare",
                                      question, re.IGNORECASE):
        selected = [item for item in items if re.search(
            "".join((  # noqa: FLY002
                r"for\s+patients\s+without\s+(?:a\s+)?pacemaker|",
                r"심박\s*조율기.{0,8}없는\s*환자",
            )), item, re.IGNORECASE,
        )]
        if selected:
            return selected
    if re.search(r"bluetooth|블루투스", question, re.IGNORECASE) and re.search(
        r"연결|선택|순서|connect|select|sequence", question, re.IGNORECASE,
    ):
        hits = [index for index, item in enumerate(items)
                if re.search(r"bluetooth|블루투스", item, re.IGNORECASE)]
        if hits:
            start, end = hits[0], hits[-1] + 1
            if start and re.search(
                r"while\s+the\s+app|앱.{0,10}실행", items[start - 1], re.IGNORECASE,
            ):
                start -= 1
            while end < len(items) and re.match(
                r"[✓➢⚫•]\s*(?:once\s+connected|it\s+|연결되면)", items[end], re.IGNORECASE,
            ):
                end += 1
            block = "\n\n".join(items[start:end])
            if len(block) <= SOURCE_PASSAGE_MAX:
                return [block]
    if re.search(r"표시|메시지|코드|떠|뜨|message|display", question, re.IGNORECASE):
        labels = re.findall(
            r"""["“”'\u2018\u2019]([^"“”'\u2018\u2019\n]{3,40})["“”'\u2018\u2019]""", text,
        )
        requested = [label for label in labels if normalized(label) in normalized(question)]
        selected = [item for item in items if any(
            normalized(label) in normalized(item) for label in requested
        ) and re.search(r"message|appear|표시|메시지", item, re.IGNORECASE)]
        if selected:
            return selected
    if re.search(r"NIBP|혈압", question, re.IGNORECASE) and re.search(
        r"시작|중지|정지|start|stop", question, re.IGNORECASE,
    ):
        # Lettered menu entries are alternatives, not a cumulative procedure.
        entries = re.split(r"(?m)(?=^[ \t]*[a-z]\.[ \t]*(?:Choose|Select)\b)", text)
        selected = [entry.strip() for entry in entries
                    if re.match(r"\s*[a-z]\.[ \t]*(?:Choose|Select)\b", entry)
                    and re.search(r"Start\s+NIBP", entry, re.IGNORECASE)
                    and re.search(r"Stop\s+NIBP", entry, re.IGNORECASE)
                    and len(entry) <= SOURCE_PASSAGE_MAX]
        if selected:
            return selected
    return []


def question_passages(question: str, source: Evidence) -> list[str]:  # noqa: C901, PLR0911, PLR0912, PLR0915
    if _operator_sleep_question(question):
        return _operator_sleep_warning(question, _complete_bullet_items(source.text))
    overview = _device_principle_passages(question, source.text)
    if overview:
        return overview
    scoped = _specific_operation_passages(question, source.text)
    if scoped is not None:
        return scoped
    workplace = _workplace_scoped_passages(question, source.text)
    if workplace:
        return workplace
    text = normalized(source.text)
    if door_question(question) and "hooddoor" in text and "경보소리대신에아이콘" in text:
        return [
            block
            for block in paragraphs(source.text)
            if "경보소리대신에아이콘" in normalized(block)
        ]
    power_failure = _power_failure_table_passage(question, source.text)
    if power_failure:
        return [power_failure]
    error_rows = _error_code_passages(question, source.text)
    if error_rows:
        candidates = error_rows
    else:
        blocks = paragraphs(source.text)
        has_leading_warning = bool(
            blocks and re.search(r"경고|주의|반드시|금지|하지\s*마|WARNING|CAUTION", blocks[0])
        )
        candidates = [
            *contextual_passages(source.text),
            *( [] if has_leading_warning else blocks ),
            *_section_passages(source.text),
            *_independent_section_passages(source.text),
        ]
        # Numbered extraction is a repair for flattened procedure/pre-use
        # pages.  Keep the ordinary candidate order for specification and
        # principle questions: those often have a complete explanatory
        # paragraph that is more useful than a numbered fragment.
        if question_intent(question) == "usage" and re.search(
            (
                r"사용\s*전|사용하기\s*전|before\s+(?:each\s+)?use|pre[- ]use|"
                r"절차|순서|시작|연결|설치|점검|확인|procedure|sequence|start|"
                r"connect|install|check|prepare"
            ),
            question,
            re.IGNORECASE,
        ):
            candidates.extend(_numbered_section_passages(source.text))
    preuse = _preuse_section_passages(question, source.text)
    if preuse:
        # A broad before-use question needs the explicit inspection section,
        # not every neighboring section whose body happens to say "check".
        # Keep the section intact so its warnings and numbered prerequisites
        # remain available to the explanation model.
        return preuse
    if _principle_question(question):
        principle_pattern = "".join(  # noqa: FLY002
            (
                r"원리|측정|도플러|초음파|심박|심전도|온도|습도|감지|검출|출력|",
                r"진공|흡입|음압|공기압|압력|펌프|다이어프램|vacuum|suction|negative\s+pressure|pressure|pump|diaphragm|",
                r"principle|operation|measure|detect|doppler|ultrasound|heart\s*rate|",
                r"electrocardi|temperature|humidity|output",
            )
        )
        principle_candidates = [
            value for value in candidates if re.search(principle_pattern, value, re.IGNORECASE)
        ]
        substantive = [
            value
            for value in principle_candidates
            if not re.search(
                "".join(  # noqa: FLY002
                    (
                        r"참조|refer\s+to|see\s+(?:chapter|section)|장부터|",
                        r"malfunction|오류|파형\s*(?:이상|없음)|측정\s*절차|measurement\s+procedure|",
                        r"measurement\s+limits|patient\s+movement|arrhythmia|환경\s*요구",
                    )
                ),
                value,
                re.IGNORECASE,
            )
        ]
        if substantive:
            principle_candidates = substantive
        without_contents = [
            value for value in principle_candidates
            if not re.search(r"table\s+of\s+contents|목차", value, re.IGNORECASE)
        ]
        # A contents page is navigation metadata, never a fallback answer.
        # If the page has no substantive principle passage, leave the result
        # empty so the caller reports missing evidence instead of hallucinating
        # from section titles.
        principle_candidates = without_contents
        principle_candidates = list(
            dict.fromkeys(_principle_core_passage(value) for value in principle_candidates)
        )
        mechanism_only = [
            value for value in principle_candidates
            if not re.search(
                r"(?:메뉴|menu|터치|touch|버튼|button|설정|setting|operation\s+menu|start/?stop)",
                value,
                re.IGNORECASE,
            ) or re.search(
                r"(?:원리|principle|측정\s*원리|measur(?:e|ement)\s+principle)",
                value,
                re.IGNORECASE,
            )
        ]
        # Do not fall back to a menu-only paragraph for a principle question.
        # The caller can then ask for a narrower product question instead of
        # presenting an unrelated control instruction as the mechanism.
        # A heading alone is not a mechanism.  Keep ordinary factual windows,
        # but reject control/menu text that only repeats "Operating Principle"
        # or its Korean equivalent without describing a physical signal/path.
        principle_candidates = [
            value for value in mechanism_only
            if _has_principle_mechanism(value)
            or not re.search(
                r"(?:operating|working)\s+principles?|작동\s*원리|동작\s*원리",
                value,
                re.IGNORECASE,
            )
        ]
        if _is_broad_principle_question(question):
            principle_candidates = [
                value for value in principle_candidates
                if not _component_principle_heading(value)
            ]
        candidates = principle_candidates
    if re.search(r"태아|fetal|foetal", question, re.IGNORECASE):
        candidates = _fetal_condition_candidates(candidates, source.text)
    if battery_runtime_question(question):
        candidates = [value for value in candidates if runtime_evidence(value)]
    # Storage conditions cannot be replaced by operating conditions merely
    # because both contain temperature/humidity vocabulary.
    if re.search(r"보관|저장\s*온도|\bstorage\b|\bstored\b", question, re.IGNORECASE):
        candidates = [
            value for value in candidates if re.search(r"보관|storage|stored", value, re.IGNORECASE)
        ]
    if re.search(
        r"(?:작동|사용|동작|운전)\s*온도|operating\s*temperature",
        question,
        re.IGNORECASE,
    ):
        candidates = [
            value
            for value in candidates
            if re.search(
                r"(?:작동|사용|동작|운전|사용\s*환경).{0,25}온도|운전\s*환경|operating|operation\s*environment",
                value,
                re.IGNORECASE | re.DOTALL,
            )
        ]
    if re.search(
        r"(?:경보|알람)\s*(?:음량|볼륨|소리)|alarm\s+(?:volume|sound)", question, re.IGNORECASE
    ) and not re.search(r"끄|음소거|mute|silent|0\s*(?:으로|로|to)", question, re.IGNORECASE):
        mute_parts = (
            r"(?:alarmvolume|경보음량).{0,60}(?:0(?:으로|로)(?:설정|변경)|to0)",
            r"경보(?:소리|기능)?.{0,10}(?:꺼|끄|끔)|(?:turnoff|disable|mute).{0,20}alarm",
        )
        mute = "|".join(mute_parts)
        candidates = [
            value
            for value in candidates
            if not re.search(
                mute,
                re.sub(r'[\s“”"]', "", value),
                re.IGNORECASE,
            )
        ]
    result = (
        _status_table_passages(question, source.text)
        or _named_mode_passages(question, source.text) or candidates
    )
    if _principle_question(question):
        result = [
            value for value in result
            if not _is_heading_only(value)
            and not re.search(r"table\s+of\s+contents|목차", value, re.IGNORECASE)
        ]
    return result


def _status_table_passages(question: str, text: str) -> list[str]:
    """Keep row labels and repeated On/Off/Flashing cells in their complete table."""
    if question_intent(question) not in {"usage", "spec", "general"} or not re.search(
        r"표시|상태|indicator|status|display", question, re.IGNORECASE,
    ):
        return []
    header = re.search(r"(?:Fig\.?\s+)?Name\s+Status\s+Description", text, re.IGNORECASE)
    if header is None:
        return []
    next_section = re.search(r"(?m)^\s*\d+(?:\.\d+)+\s+\S", text[header.end():])
    end = header.end() + next_section.start() if next_section else len(text)
    start = header.start()
    if re.search(r"WARNING|CAUTION|경고|주의", text[:start], re.IGNORECASE):
        start = 0
    table = text[start:end].strip()
    return [table] if 25 <= len(table) <= SOURCE_PASSAGE_MAX else []


def _named_mode_passages(question: str, text: str) -> list[str]:
    """Prefer complete named modes, retaining parent context and global warnings."""
    heading = r"(?im)^\s*([A-Za-z][A-Za-z ]{0,40}(?:Mode|모드))\s*[\u2013\u2014-]"
    names = re.findall(heading, text)
    compact_question = re.sub(r"\s+", "", question.replace("모드", "mode")).casefold()
    requested = [name.strip() for name in names
                 if re.sub(r"\s+", "", name.replace("모드", "mode")).casefold()
                 in compact_question]
    if len(requested) != 1:
        return []
    return [
        value for value in _independent_section_passages(text)
        if re.search(r"(?im)^\s*" + re.escape(requested[0]) + r"\s*[\u2013\u2014-]", value)
        and len(re.findall(heading, value)) == 1
    ]


def _fetal_condition_candidates(candidates: list[str], text: str) -> list[str]:
    """Separate alternative conditional probe branches when fetal scope is explicit.

    Keep all conditions inside the fetal branch. This is deliberately limited
    to pages that explicitly distinguish multiple conditional probe branches;
    general preparation paragraphs and warning sections are not split.
    """
    blocks = re.split(r"(?m)(?=^[ \t]*[•⚫])", text)
    branches = [
        block for block in blocks
        if re.match(r"\s*[•⚫]\s*(?:If|When)\b", block, re.IGNORECASE)
        and re.search(r"\b(?:probe|transducer)\b", block, re.IGNORECASE)
    ]
    fetal = [block for block in branches if re.search(r"fetal|foetal|태아", block, re.IGNORECASE)]
    if not fetal:
        return candidates
    unrelated = [block for block in branches if block not in fetal]
    # A whole leading conditional clause must match, not just a shared probe word.
    prefixes = [normalized(re.split(r"\.(?:\s|$)", block, maxsplit=1)[0]) for block in unrelated]
    scoped = [
        value for value in candidates
        if not any(prefix in normalized(value) for prefix in prefixes)
    ]
    if not re.search(r"WARNING|CAUTION|경고|주의", text, re.IGNORECASE):
        scoped.extend(" ".join(block.split()) for block in fetal)
    return list(dict.fromkeys(scoped))


def _error_code_passages(question: str, text: str) -> list[str]:
    """Extract an error row or complete status item without importing neighbours."""
    # Acronyms such as CTG are product terminology, not error labels.
    if not re.search(
        "".join((  # noqa: FLY002
            r"오류|에러|경고|코드|떠|뜨|error|fault|warning|message|",
            r"(?<![A-Z])(?:NO PAPER|DOOR OPEN|LOW BATTERY|LOW BAT)(?![A-Z])",
        )),
        question,
        re.IGNORECASE,
    ):
        return []
    known = re.findall(r"NO\s+PAPER|DOOR\s+OPEN|LOW\s+BAT(?:TERY)?", question, re.IGNORECASE)
    codes = known or [
        match.group(0) for match in re.finditer(
            r"(?<![A-Z0-9])[A-Z][A-Z0-9]+(?:\s+[A-Z][A-Z0-9]+)*(?![A-Z0-9])", question
        )
    ]
    codes = [code for code in codes if len(code) >= 3 and any(char.isalpha() for char in code)]
    if not codes:
        return []
    # Status messages can occur inside a complete circled instruction. Preserve
    # its condition, and stop before another item, section, table, or independent
    # warning. A later table row must not inherit the preceding circled item.
    item_boundaries = list(re.finditer(
        "".join((  # noqa: FLY002
            r"(?m)^[ \t]*(?:[①-⑳]|\d+(?:\.\d+)+\.?[ \t]+[A-Za-z가-힣]|",
            r"(?:Error\s+Message|Problems?)\s+(?:Possible\s+)?Cause\s+",
            r"(?:Solution|Corrective\s+Action)|",
            r"(?:[⚫●•][ \t]*)?(?:WARNING|CAUTION|DANGER|경고|주의|",
            r"Alarm\s+Occasion\s+and\s+Status\s+Checking)\b)",
        )),
        text, re.IGNORECASE,
    ))
    found: list[str] = []
    for code in dict.fromkeys(codes):
        for match in re.finditer(re.escape(code), text, re.IGNORECASE):
            previous = [
                boundary for boundary in item_boundaries if boundary.start() <= match.start()
            ]
            following = [
                boundary for boundary in item_boundaries if boundary.start() > match.start()
            ]
            boundary_end = following[0].start() if following else len(text)
            if previous and re.match(r"[ \t]*[①-⑳]", previous[-1].group(0)):
                segment = " ".join(text[previous[-1].start():boundary_end].split())
                if segment and len(segment) <= SOURCE_PASSAGE_MAX:
                    found.append(segment)
                continue
            next_code = re.search(
                r"(?<![A-Z0-9])[A-Z][A-Z0-9]+(?:\s+[A-Z][A-Z0-9]+)*(?![A-Z0-9])",
                text[match.end():],
            )
            end = min(match.end() + next_code.start() if next_code else len(text), boundary_end)
            segment = " ".join(text[match.start():end].split())
            if segment and len(segment) <= SOURCE_PASSAGE_MAX:
                found.append(segment)
    return found


def _independent_section_passages(text: str) -> list[str]:
    """Add whole parenthesized sections and explicitly titled mode alternatives.

    Copy every page-level warning block to each candidate. Mode bodies are never
    cut at bullet boundaries, so timeout conditions and exceptions stay intact.
    """
    boundary = re.compile(
        "".join((  # noqa: FLY002
            r"(?m)^[ \t]*(?:\(\d+\)[ \t]+[^\n]+|",
            r"[A-Za-z][A-Za-z ]{0,40}(?:Mode|모드)[ \t]*[\u2013\u2014-][^\n]+|",
            r"(?:WARNING|CAUTION|경고|주의)\b[^\n]*)",
        )),
    )
    headings = list(boundary.finditer(text))
    if not headings:
        return []
    blocks = [
        text[
            match.start():headings[index + 1].start() if index + 1 < len(headings) else len(text)
        ].strip()
        for index, match in enumerate(headings)
    ]
    warning = re.compile(r"^(?:WARNING|CAUTION|경고|주의)\b", re.IGNORECASE)
    warnings = [block for block in blocks if warning.match(block)]
    result: list[str] = []
    parent_context = ""
    for block in blocks:
        if warning.match(block):
            continue
        if re.match(r"\(\d+\)", block):
            parent_context = block if "\n" in block else ""
            parts = [block]
        else:
            parts = [parent_context, block] if parent_context else [block]
        complete = "\n\n".join([*parts, *warnings])
        if len(complete) <= SOURCE_PASSAGE_MAX:
            result.append(complete)
    return result


def _section_passages(text: str) -> list[str]:
    """Retain explicit, page-local sections and their complete warning blocks.

    No numeric table cells are reordered or interpreted. Long unsplittable
    sections remain unavailable for generation rather than being truncated.
    """
    heading = "".join(  # noqa: FLY002
        (
            r"(?im)(?=^[ \t]*(?:Storage\b|Power\s*\(AC\)|Operation environment\b|",
            r"WIPE\s*$|DISINFECTION\s*$|WARNING\s*$|CAUTION\s*$))",
        )
    )
    blocks = re.split(heading, text)
    warnings: list[str] = []
    for block in blocks:
        if not re.match(r"\s*(?:WARNING|CAUTION)\b", block, re.IGNORECASE):
            continue
        warning = re.split(r"(?m)(?=^[ \t]*\d+(?:\.\d+)+\s+[A-Za-z])", block)[0].strip()
        if not re.fullmatch(r"WARNING|CAUTION", warning, re.IGNORECASE):
            warnings.append(warning)
    result: list[str] = []
    for raw_block in blocks:
        block = raw_block.strip()
        if not block:
            continue
        # A storage paragraph may be followed immediately by operating advice,
        # without a blank line or another numbered section.
        if re.match(r"Storage\b", block, re.IGNORECASE):
            block = re.split(
                r"(?im)(?=^\s*(?:The operating environment|\d+(?:\.\d+)+\s+))",
                block,
            )[0].strip()
        if not re.match(r"(?:Storage\b|Power\s*\(AC\)|WIPE\b)", block, re.IGNORECASE):
            continue
        complete = "\n\n".join([block, *warnings]) if warnings else block
        if len(complete) <= SOURCE_PASSAGE_MAX:
            result.append(complete)
    return result


def _numbered_section_passages(text: str) -> list[str]:  # noqa: C901
    """Extract complete numbered/manual sections from flattened PDF pages.

    Some PDF pages contain several headings without blank lines, so the normal
    paragraph splitter produces one large page window.  Section boundaries are
    a safer context unit when the question asks for a procedure or a
    pre-use check.  The function keeps the source verbatim and never truncates
    an overlong section.
    """
    heading = re.compile(
        "".join((  # noqa: FLY002
            r"(?im)^\s*(?:(\d+(?:\.\d+){0,3})\.?\s+([^\n]{2,100})|",
            r"(Section\s*\d+(?:\.\d+)?(?:\s*\.\s*)?[^\n]{2,100}))\s*$",
        ))
    )
    matches = list(heading.finditer(text))
    if not matches:
        return []
    result: list[str] = []
    step_start = re.compile(
        "".join((  # noqa: FLY002
            r"(?:turn|check|verify|connect|select|place|press|open|close|remove|install|",
            r"insert|measure|start|stop|ensure|adjust|choose|hold|set|누르|확인|연결|",
            r"선택|장착|열|닫|분리|설치|측정|시작|중지|조정|잡|설정)",
        )),
        re.IGNORECASE,
    )
    descriptive_title = re.compile(
        "".join((  # noqa: FLY002
            r"(?:safety|information|description|overview|operation|maintenance|",
            r"intended\s+use|introduction|troubleshooting|procedure|requirements?|",
            r"checking|product|device|configuration|specification|environment|",
            r"before\s+use|pre[- ]use|",
            r"warning|caution|cleaning|disinfection|measurement|NIBP|ECG|SpO2|",
            r"사용\s*목적|안전|정보|설명|개요|작동|유지|점검|절차|요구사항|제품|장치|",
            r"환경|사양|측정|경고|주의|세척|소독|사용\s*전|사용하기\s*전)",
        )),
        re.IGNORECASE,
    )
    kept_matches: list[re.Match[str]] = []
    for match in matches:
        number = match.group(1) or ""
        title = (match.group(2) or match.group(3) or "").strip()
        # A single numeric prefix is also used for procedure steps.  Keep
        # top-level sections with descriptive titles, but never promote an
        # imperative step to a new evidence boundary.  Multi-level sections
        # and explicit Section headings remain unambiguous.
        explicit_before_use = re.search(
            r"before\s+use|pre[- ]use|사용\s*전|사용하기\s*전",
            title,
            re.IGNORECASE,
        )
        if number and "." not in number and (
            (step_start.match(title) and not explicit_before_use)
            or not descriptive_title.match(title)
        ):
            continue
        kept_matches.append(match)
    for index, match in enumerate(kept_matches):
        end = kept_matches[index + 1].start() if index + 1 < len(kept_matches) else len(text)
        block = text[match.start():end].strip()
        number = match.group(1) or ""
        if number and "." in number:
            parts = number.split(".")
            parent_number = ".".join(parts[:-1])
            parent = next(
                (
                    prior for prior in reversed(kept_matches[:index])
                    if (prior.group(1) or "") == parent_number
                ),
                None,
            )
            if parent is not None:
                prefix = text[parent.start():match.start()].strip()
                first_body = prefix.find("\n")
                nested = re.search(
                    r"(?m)^\s*\d+\.\d+(?:\.\d+)*\s+",
                    prefix[first_body + 1:] if first_body >= 0 else "",
                )
                if nested:
                    prefix = prefix[:first_body + 1 + nested.start()].strip()
                if prefix:
                    if len(prefix) + len(block) + 2 > SOURCE_PASSAGE_MAX:
                        # Do not expose a child section without its required
                        # parent warning/context when the complete source
                        # cannot fit in the model input passage.
                        continue
                    block = f"{prefix}\n\n{block}"
        if 80 <= len(block) <= SOURCE_PASSAGE_MAX and not _is_heading_only(block):
            result.append(block)
    return list(dict.fromkeys(result))


def _preuse_section_passages(question: str, text: str) -> list[str]:  # noqa: C901, PLR0912
    """Return at most two complete, explicitly titled before-use sections."""
    if not re.search(
        "".join((  # noqa: FLY002
            r"사용\s*전|사용하기\s*전|사용\s*시|before\s+(?:each\s+)?use|",
            (r"pre[- ]use|before\s+administer|before\s+(?:using|administering)\s+"
             r"(?:the\s+)?(?:device|unit|product|patient)"),
        )),
        question,
        re.IGNORECASE,
    ):
        return []
    sections = _numbered_section_passages(text)
    heading = re.compile(
        "".join((  # noqa: FLY002
            r"(?im)^\s*(?:(?:\d+(?:\.\d+){0,3})\.?\s+([^\n]{2,100})|",
            r"Section\s*\d+(?:\.\d+)?(?:\s*\.\s*)?([^\n]{2,100}))\s*$",
        )),
    )
    explicit = re.compile(
        "".join((  # noqa: FLY002
            r"사용\s*전|사용하기\s*전|사용\s*시\s*요구|before\s+(?:each\s+)?us(?:e|ing)|",
            (r"pre[- ]use|before\s+administer|before\s+(?:using|administering)\s+"
             r"(?:the\s+)?(?:device|unit|product|patient)|checking|inspection|점검|확인"),
        )),
        re.IGNORECASE,
    )
    preuse_phrase = re.compile(
        "".join((  # noqa: FLY002
            r"사용\s*전|사용하기\s*전|사용\s*시\s*요구|before\s+(?:each\s+)?us(?:e|ing)|",
            (r"pre[- ]use|before\s+administer|before\s+(?:using|administering)\s+"
             r"(?:the\s+)?(?:device|unit|product|patient)"),
        )),
        re.IGNORECASE,
    )
    inspection_context = re.compile(
        "".join((  # noqa: FLY002
            r"damage|wire|accessor|function|output|power|environment|battery|alarm|",
            r"mechanical|inspect|verify|measure|check|손상|와이어|액세서리|기능|출력|",
            r"전원|환경|배터리|경보|검사|점검|확인|측정",
        )),
        re.IGNORECASE,
    )
    if not sections:
        fallback = [
            block for block in paragraphs(text)
            if preuse_phrase.search(block) and 80 <= len(block) <= SOURCE_PASSAGE_MAX
        ]
        if fallback:
            return fallback[:2]
    named_preuse_exists = any(
        any(
            preuse_phrase.search((match[1] or match[2]).strip())
            for match in heading.finditer(section)
        )
        for section in sections
    )
    matches: list[tuple[int, int, str]] = []
    for index, section in enumerate(sections):
        if re.search(r"table\s+of\s+contents|목차", section, re.IGNORECASE):
            continue
        titles = [
            (match[1] or match[2]).strip()
            for match in heading.finditer(section)
            if (match[1] or match[2]).strip()
        ]
        matching_titles = [title for title in titles if explicit.search(title)]
        title_preuse = any(preuse_phrase.search(title) for title in titles)
        body_preuse = bool(preuse_phrase.search(section))
        if named_preuse_exists and not title_preuse and re.search(
            r"product\s+configuration|description\s+of\s+(?:each\s+)?part|제품\s*구성|각\s*부품\s*설명",
            section,
            re.IGNORECASE,
        ):
            continue
        if not (title_preuse or (body_preuse and inspection_context.search(section))):
            continue
        # A generic "read the manual" notice or icon is navigation, not a
        # pre-use inspection answer.  Keep it only when the same section also
        # names an inspection/check or a concrete condition to verify.
        manual_notice = re.search(
            r"(?:read|refer\s+to)\s+(?:the\s+)?(?:operation\s+)?manual|"  # noqa: ISC003
            + r"매뉴얼(?:을|를)?\s*(?:읽|참조)|설명서(?:를|을)?\s*(?:읽|참조)",
            section, re.IGNORECASE,
        )
        concrete_check = re.search(
            r"damage|wire|accessor|function|output|power|environment|battery|alarm|"  # noqa: ISC003
            + r"inspect|verify|measure|check|손상|와이어|액세서리|기능|출력|전원|환경|배터리|"
            + r"경보|검사|점검|확인|측정",
            section, re.IGNORECASE,
        )
        if manual_notice and not concrete_check:
            continue
        if not matching_titles and not body_preuse:
            continue
        nested_check = re.search(
            r"(?im)^\s*\d+\.\d+(?:\.\d+)*\s+(?:before\s+(?:each\s+)?use|"  # noqa: ISC003
            + r"checking|inspection|점검|확인)[^\n]*",
            section,
        )
        section_text = section
        if nested_check and nested_check.start() > 0:
            section_text = section[nested_check.start():].strip()
        # Prefer a heading whose title explicitly names checking/inspection,
        # then preserve source order as the deterministic tie-breaker.
        score = max(
            2 if re.search(r"checking|inspection|점검|확인", title, re.IGNORECASE) else 1
            for title in matching_titles
        ) if matching_titles else 0
        if title_preuse:
            score += 3
        if body_preuse:
            score += 2
        if inspection_context.search(section_text):
            score += 1
        matches.append((score, index, section_text))
    if not matches:
        fallback = [
            block for block in paragraphs(text)
            if preuse_phrase.search(block) and 80 <= len(block) <= SOURCE_PASSAGE_MAX
        ]
        if fallback:
            return fallback[:2]
    # When a parent chapter and a matching nested checking section are both
    # present, keep the narrower section so unrelated maintenance controls do
    # not enter the answer context.
    if any(re.match(r"\s*\d+\.\d+\s+", value[2]) for value in matches):
        matches = [value for value in matches if not re.match(r"\s*\d+\s+", value[2])]
    matches.sort(key=lambda item: (-item[0], item[1]))
    return [value for _, _, value in matches[:2]]


def _is_preuse_question(question: str) -> bool:
    return bool(re.search(
        "".join((  # noqa: FLY002
            r"사용\s*전|사용하기\s*전|사용\s*시\s*요구|before\s+(?:each\s+)?use|",
            r"pre[- ]use|before\s+administer|before\s+[^?\n]{0,40}patient",
        )),
        question,
        re.IGNORECASE,
    ))


def _is_broad_principle_question(question: str) -> bool:
    if not _principle_question(question):
        return False
    return not bool(re.search(
        "".join((  # noqa: FLY002
            r"SpO2|ECG|NIBP|산소\s*포화|심전도|혈압|습도|온도|도플러|temperature|",
            r"humidity|doppler|sensor|센서",
        )),
        question,
        re.IGNORECASE,
    ))


def _component_principle_heading(text: str) -> bool:
    """Identify a component chapter without rejecting an overall overview."""
    prefix = " ".join(text.split())[:260]
    if re.search(
        "".join((  # noqa: FLY002
            r"^(?:\d+(?:\.\d+)*\s+)?(?:SpO2|ECG|NIBP|Pulse\s+oximetry|",
            r"산소\s*포화|심전도|혈압|맥박|온도|습도)\b",
        )),
        prefix,
        re.IGNORECASE,
    ):
        return True
    return bool(
        re.match(r"^\d+(?:\.\d+)*\s+(?:overview|개요)\b", prefix, re.IGNORECASE)
        and re.search(
            r"SpO2|oxygen\s+saturation|산소\s*포화|ECG|NIBP|심전도|혈압",
            prefix,
            re.IGNORECASE,
        )
    )


def _unique_passages(passages: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Drop repeated windows while keeping each page/evidence boundary."""
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for evidence_id, text in passages:
        key = (evidence_id, " ".join(text.split()))
        if key in seen:
            continue
        if re.search(r"(?:^|\s)(?:경고|주의|WARNING|CAUTION)\s*$", text):
            continue
        seen.add(key)
        unique.append((evidence_id, text))
    return unique


def _is_heading_only(text: str) -> bool:
    compact = " ".join(text.split())
    # Printed contents entries have page numbers, not a factual numeric claim.
    if re.fullmatch(
        r".+(?:\.{3,}|\u2026{2,}|\u00b7{3,}|-{3,}|\u2013{3,}|\u2014{3,})\s*\d+",
        compact,
    ):
        return True
    match = re.match(r"^\d+(?:\.\d+)*\.?\s+(.+)$", compact)
    body = match.group(1) if match else ""
    action = r"\d|누르|열어|여세요|넣|장착|연결|분리|확인하|하세|하십시오|합니다|된다|됩니다"
    commands = "".join(  # noqa: FLY002
        (
            r"|\b(?:press|open|insert|install|connect|disconnect|check|turn|click|",
            r"remove|must|do)\b",
        )
    )
    if re.search(
        action + commands,
        body,
        flags=re.IGNORECASE,
    ):
        return False
    return len(compact) <= 90 and bool(match) and not any(mark in body for mark in (".", "!", "?"))


def _deduplicate_windows(
    candidates: list[tuple[float, str, str]],
) -> list[tuple[float, str, str]]:
    """Merge nearly identical contained windows from one evidence page only.

    Keep the longer original verbatim, so extra conditions/warnings survive.
    Distinct documents, revisions, pages and differing numeric claims never merge.
    Short focused subsections remain alongside their substantially wider context.
    """
    kept: list[tuple[float, str, str]] = []
    for score, evidence_id, text in sorted(candidates, key=lambda item: -len(item[2])):
        compact = " ".join(text.split())
        for index, (other_score, other_id, other_text) in enumerate(kept):
            other_compact = " ".join(other_text.split())
            if (evidence_id == other_id and compact in other_compact
                    and len(compact) / len(other_compact) >= 0.85):
                kept[index] = (max(score, other_score), other_id, other_text)
                break
        else:
            kept.append((score, evidence_id, text))
    return kept


def _selection_relevance(  # noqa: C901, PLR0912
    question: str, text: str, ranking_question: str | None,
) -> float:
    """Score original-language evidence before using translated retrieval terms.

    Retrieval translation is useful for finding English manuals, but it can make
    a Korean page header or generic usage paragraph look more relevant than a
    Korean troubleshooting instruction.  Keep the best lexical score and add a
    small, intent-specific preference for an explicit symptom check.
    """
    score = relevance(question, text)
    if ranking_question:
        score = max(score, relevance(ranking_question, text))
    if question_intent(question) == "usage":
        score += _control_heading_bonus(question, text)
        if re.search(
            "".join((  # noqa: FLY002
                r"사용\s*전|사용하기\s*전|사용\s*시|before\s+(?:each\s+)?use|",
                r"pre[- ]use|before\s+administer",
            )),
            question,
            re.IGNORECASE,
        ) and re.search(
            "".join((  # noqa: FLY002
                r"사용\s*전|사용하기\s*전|before\s+(?:each\s+)?use|",
                r"pre[- ]use|before\s+administer|checking|점검",
            )),
            text,
            re.IGNORECASE,
        ):
            score += 1.0
    if service_overview_question(question) and safe_service_passage(question, text):
        score += 0.6
    if _principle_question(question):
        if re.search(
            r"^\s*(?:\d+(?:\.\d+)*\.?\s*)?(?:operating|working|measurement)\s+principles?|"  # noqa: ISC003
            + r"^\s*(?:\d+(?:\.\d+)*\.?\s*)?(?:작동|동작|측정)\s*원리|"
            + r"\bhow\s+it\s+works?\b",
            text, re.IGNORECASE | re.MULTILINE,
        ):
            score += 1.0
        if re.search(
            r"(?:specification|measurement\s+range|accuracy|wavelength|사양|측정\s*범위|정확도|파장)",
            text, re.IGNORECASE,
        ) and not _has_principle_mechanism(text):
            score -= 0.8
    if (
        re.search(
            r"어디에서\s*(?:확인|찾)|where\s+(?:can|to)\s+(?:i\s+)?(?:find|check)",
            question,
            re.IGNORECASE,
        )
        and re.search(
            r"(?:alarm\s+information|알람\s*정보|경보\s*정보|chapter|장)",
            text,
            re.IGNORECASE,
        )
    ):
        score += 0.75
    if question_intent(question) == "usage" and re.search(
        r"시작|순서|절차|설치|연결|start|sequence|procedure|setup|connect",
        question, re.IGNORECASE,
    ) and re.search(
        "".join((  # noqa: FLY002
            r"(?im)^\s*(?:\d+(?:\.\d+)*\s+)?(?:측정\s*절차|사용\s*절차|",
            r"measurement\s+procedure|monitoring\s+(?:procedure|sequence)|setup\s+procedure)",
        )),
        text,
    ):
        score += 0.75
    # An explicit independent heading is a better context-budget unit than a
    # wider page window containing the same procedure plus adjacent controls.
    if re.match(r"\s*\(\d+\)[ \t]+", text) and score >= 0.08:
        score += 0.35
    symptom_pattern = "".join(  # noqa: FLY002
        (
            r"소리가\s*(?:들리지|나지|안\s*들리|안\s*나)|",
            r"(?:no|not)\s+sound|sound\s+(?:is\s+)?(?:not|cannot)|",
            r"소리.*(?:확인|시험|점검|서비스)",
        )
    )
    if (
        question_intent(question) == "troubleshooting"
        and re.search(r"소리|sound|audio", question, re.IGNORECASE)
        and re.search(symptom_pattern, text, re.IGNORECASE)
    ):
        score += 0.35
    if question_intent(question) == "troubleshooting" and re.search(
        r"프린터|인쇄|출력|용지|printer|print(?:ing)?|paper|door|error|오류|문제해결|troubleshoot",
        question,
        re.IGNORECASE,
    ):
        if re.search(
            r"프린터|인쇄|출력|용지|printer|print(?:ing)?|paper|door|error|오류|문제해결|troubleshoot|확인|check|insert|close|open",
            text,
            re.IGNORECASE,
        ):
            score += 0.28
        if re.search(r"분해|disassembl|나사|screw|remove\s+the\s+printer", text, re.IGNORECASE):
            score -= 0.25
    # Keep a requested measurement action ahead of a generic principle.  NIBP
    # pages often put the oscillometric overview next to the cuff/line checks;
    # a cuff question needs the latter, while the overview remains valid for a
    # principle question.
    if question_intent(question) == "troubleshooting" and re.search(
        r"NIBP|비침습\s*혈압|혈압", question, re.IGNORECASE,
    ) and re.search(r"커프|cuff", question, re.IGNORECASE):
        if re.search(
            r"커프|cuff|연장\s*튜브|extension\s+tube|leak|누설|감압|deflat|pressure\s+sensor",
            text, re.IGNORECASE,
        ):
            score += 0.9
        elif re.search(r"oscillometric|오실로메트릭|14\.1\s+overview|개요", text,
                       re.IGNORECASE):
            score -= 0.35
    # A DOP/no-audio complaint should first expose the documented volume or
    # transducer check.  Alarm-only rows are adjacent in the same table and
    # otherwise compete for the small selector context window.
    if question_intent(question) == "troubleshooting" and re.search(
        r"DOP|도플러|소리|음량|볼륨|sound|audio|volume", question, re.IGNORECASE,
    ):
        if re.search(
            (
                r"DOP\s*[12]|Doppler|심박\s*음량|transducer\s+test|변환기.*(?:시험|점검)|"
                r"tap.{0,45}(?:sound|소리)|두드.{0,20}(?:소리|음)"
            ),
            text, re.IGNORECASE,
        ):
            score += 0.55
        if (
            re.search(r"alarm\s+(?:on|off|sound)|경보\s*(?:음향|소리)", text, re.IGNORECASE)
            and not re.search(
                r"DOP\s*[12]|Doppler|심박\s*음량|transducer|변환기", text, re.IGNORECASE,
            )
        ):
            score -= 0.3
    # Principle questions need a mechanism paragraph, not the safety/intended
    # use paragraph that happens to contain the word "vacuum".  This is a
    # ranking hint only; source and model-scope gates still decide eligibility.
    if _principle_question(question):
        if re.search(
            (
                r"진공\s*(?:펌프|압력)|vacuum\s+(?:pump|pressure)|suction|negative\s+pressure|"
                r"다이어프램|diaphragm"
            ), text, re.IGNORECASE,
        ):
            score += 0.65
        if (
            re.search(r"safety\s+information|intended\s+use|안전\s*정보|사용\s*목적", text,
                      re.IGNORECASE)
            and not re.search(
                r"operating\s+principle|작동\s*원리|원리", text, re.IGNORECASE,
            )
        ):
            score -= 0.2
    return score


def _control_heading_bonus(question: str, text: str) -> float:
    """Rank an explicitly requested control section ahead of a matching fault table."""
    actions = (
        (r"켜|turn\s+on|power\s+on", r"turn\s+on|power\s+on|전원\s*(?:켜|on)"),
        (r"초점|focus", r"focus\w*|초점"),
        (r"충전|charg", r"charg\w*|충전"),
        (r"사용\s*전|사용\s*시|before\s+use|pre[- ]use",
         r"사용\s*시\s*요구사항|사용\s*전|requirements?\s+for\s+use|before\s+use|pre[- ]use"),
    )
    return 0.9 if any(
        re.search(requested, question, re.IGNORECASE)
        and re.search(r"(?im)^\s*\d+(?:\.\d+)*\.?\s+(?:" + heading + ")", text)
        for requested, heading in actions
    ) else 0.0


def _component_scoped_candidates(  # noqa: C901, PLR0912
    question: str, candidates: list[tuple[float, str, str]]
) -> list[tuple[float, str, str]]:
    """Keep troubleshooting evidence on the component named by the question.

    Retrieval can return a printer page next to generic power/probe setup
    paragraphs. If a component-specific candidate exists, unrelated generic
    preparation text must not compete for the selector's small context window.
    Questions without a recognizable component retain the normal broad search.
    """
    # Named measurements are the subject even in a factual/connection question.
    # Do not substitute general room, power, or storage requirements for a
    # missing ECG/SpO2/NIBP instruction.
    measurements = (
        r"(?<![A-Z])ECG(?![A-Z])|심전도",
        _MEASUREMENT_SUBJECTS["spo2"],
        r"(?<![A-Z])NIBP(?![A-Z])|비침습\s*혈압",
    )
    requested = [pattern for pattern in measurements if re.search(pattern, question, re.IGNORECASE)]
    if requested:
        cuff_measurement = (
            bool(re.search(r"(?<![A-Z])NIBP(?![A-Z])|비침습\s*혈압", question, re.IGNORECASE))
            and bool(re.search(r"커프|cuff", question, re.IGNORECASE))
        )
        candidates = [
            item for item in candidates
            if any(re.search(pattern, item[2], re.IGNORECASE) for pattern in requested)
            or (
                cuff_measurement
                and re.search(
                    r"커프|cuff|extension\s+tube|연장\s*튜브", item[2], re.IGNORECASE,
                )
            )
        ]
        if not re.search(r"교정|보정|calibrat|수리|repair|서비스|service", question, re.IGNORECASE):
            candidates = [
                item for item in candidates
                if not re.search(
                    r"교정|보정|calibrat|password|서비스\s*모드", item[2], re.IGNORECASE,
                )
            ]
        # A cuff-and-measurement question needs the preparation/procedure
        # section.  The NIBP overview also mentions a cuff, but does not give
        # the checks the operator must perform before starting measurement.
        if (
            re.search(r"커프|cuff", question, re.IGNORECASE)
            and re.search(
                r"측정|시작|확인|연결|measure|start|check|connect",
                question,
                re.IGNORECASE,
            )
        ):
            procedure = [
                item for item in candidates
                if re.search(
                    r"measurement\s+procedure|prepare\s+the\s+measurement|측정\s*절차|측정\s*준비|extension\s+tube|연장\s*튜브",
                    item[2],
                    re.IGNORECASE,
                )
            ]
            if procedure:
                candidates = procedure
    if question_intent(question) != "troubleshooting":
        return candidates
    scopes = (
        r"프린터|인쇄|출력|용지|종이|printer|print(?:ing)?|paper|door|인쇄\s*도어",
        r"터치|화면|touch|screen|display",
        r"소리|음량|볼륨|도플러|스피커|sound|audio|volume|speaker|doppler",
        r"충전|배터리|전원\s*공급|charging|battery|power\s+supply",
        r"전원|켜지|부팅|power|startup|boot",
    )
    scope = next(
        (pattern for pattern in scopes if re.search(pattern, question, re.IGNORECASE)), None
    )
    if scope is None:
        return candidates
    scoped = [item for item in candidates if re.search(scope, item[2], re.IGNORECASE)]
    if re.search(r"소리|음량|볼륨|sound|audio|volume", question, re.IGNORECASE) and re.search(
        r"DOP|도플러|transducer|변환기", question, re.IGNORECASE,
    ):
        # Keep alarm rows only when they also contain the requested DOP/probe
        # subject.  The table may share one page, but an alarm-only row cannot
        # answer a DOP sound complaint.
        focused = [item for item in scoped if re.search(
            r"DOP\s*[12]|도플러|transducer|변환기|심박\s*음량", item[2], re.IGNORECASE,
        )]
        if focused:
            scoped = focused
    if re.search(r"소리가|소리|sound|audio", question, re.IGNORECASE) and re.search(
        r"최대|끝까지|maximum|max|음량.{0,15}(?:올|높)|volume.{0,15}(?:up|high|max)",
        question, re.IGNORECASE,
    ):
        # Once the user says volume is already high, prefer the documented
        # transducer test.  This keeps the ordered procedure in the input and
        # avoids repeated model paraphrases of the volume button table.
        tested = [item for item in scoped if re.search(
            r"transducer\s+test|변환기.*(?:시험|점검)|tap.{0,45}(?:sound|소리)|두드.{0,20}(?:소리|음)",
            item[2], re.IGNORECASE,
        )]
        if tested:
            scoped = tested
    if re.search(r"DOP|도플러", question, re.IGNORECASE) and re.search(
        r"소리|음량|볼륨|sound|audio", question, re.IGNORECASE,
    ) and not re.search(
        r"경보|alarm|printer|프린터|인쇄|용지|paper|UC|트렌드|trend|전원|power",
        question,
        re.IGNORECASE,
    ):
        narrowed: list[tuple[float, str, str]] = []
        seen: set[str] = set()
        for score, evidence_id, text in scoped:
            focused = _korean_dop_volume_passage(question, text)
            if focused is None:
                continue
            key = " ".join(focused.split())
            if key not in seen:
                seen.add(key)
                narrowed.append((score, evidence_id, focused))
        if narrowed:
            scoped = narrowed
    return scoped or candidates


def _korean_dop_volume_passage(question: str, text: str) -> str | None:
    """Extract the two Korean DOP volume rows for an unambiguous sound query.

    This intentionally requires the exact Korean row labels. English tables and
    alarm or multi-feature questions keep the normal broad candidates.
    """
    if not re.search(r"DOP|도플러", question, re.IGNORECASE) or not re.search(
        r"소리|음량|볼륨|sound|audio", question, re.IGNORECASE,
    ) or re.search(
        r"경보|alarm|printer|프린터|인쇄|용지|paper|UC|트렌드|trend|전원|power",
        question,
        re.IGNORECASE,
    ):
        return None
    text = " ".join(text.split())
    row_pattern = r"DOP{channel}\s+볼륨\s+증가/감소\s+버튼\s+모니터링\s+모드에서\s+"
    row_pattern += r"DOP{channel}의\s+볼륨을\s+크게/작게\s+합니다\."
    row1 = re.search(row_pattern.format(channel=1), text)
    row2 = re.search(row_pattern.format(channel=2), text)
    if row1 is None or row2 is None:
        return None
    preserved: list[str] = []
    click = re.search(
        r"(?s)전면[ \t]*패널에는.*?버튼이[ \t]*활성화됩니다\.", text,
    )
    sharp = re.search(
        r"(?s)전면[ \t]*패널[ \t]*스위치를.*?사용하지[ \t]*마십시오\.", text,
    )
    if click:
        preserved.append(" ".join(click.group(0).split()))
    if sharp:
        preserved.append(" ".join(sharp.group(0).split()))
    preserved.extend((" ".join(row1.group(0).split()), " ".join(row2.group(0).split())))
    return "\n\n".join(preserved)


def _memory_capability_passage(question: str, text: str) -> str | None:
    """Select the complete documented memory-capability sentence only.

    Capability questions ask what can be saved/restored; procedure questions
    retain their normal button and timing passages.
    """
    if not re.search(r"메모리|memory|저장|save|restore|복원", question, re.IGNORECASE):
        return None
    if not re.search(r"어떤|무엇|무슨|할\s*수|가능|what|which|can", question, re.IGNORECASE):
        return None
    if re.search(
        r"방법|어떻게|시작|중지|실행|누르|button|how|start|stop|run|procedure",
        question,
        re.IGNORECASE,
    ):
        return None
    compact = " ".join(text.split())
    capability_pattern = "".join((  # noqa: FLY002
        r"Using memory function, comfortable and optimum massage/express mode, ",
        r"level of speed/vacuum strength, massage/express time can be saved and ",
        r"restored anytime\.",
    ))
    restore_pattern = "".join((  # noqa: FLY002
        r"You can save and restore the convenient and optimum massage/express mode, ",
        r"speed/vacuum level and operating time\.",
    ))
    patterns = (
        capability_pattern,
        restore_pattern,
    )
    for pattern in patterns:
        match = re.search(pattern, compact, re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def _sequence_context(  # noqa: C901
    chosen: list[Passage], sources: list[Evidence],
) -> list[Passage]:
    """Carry numbered procedure context across adjacent pages of the same document.

    Added spans are verbatim and keep their own page citation. No sections from
    another revision, unprovided page or service manual can enter through here.
    """
    by_id = {source.evidence_id: source for source in sources}
    step = re.compile(r"(?:➢\s*)?\bStep\s+(\d+)\s*[:.]", re.IGNORECASE)
    result = list(chosen)
    for passage in chosen:
        source = by_id[passage.evidence_id]
        steps = list(step.finditer(passage.text))
        if source.kind == "service" or not steps:
            continue
        for neighbour in sources:
            if (neighbour.document_id != source.document_id or neighbour.kind == "service"
                    or abs(neighbour.physical_page - source.physical_page) != 1):
                continue
            adjacent = list(step.finditer(neighbour.text))
            if not adjacent:
                continue
            text = ""
            if (neighbour.physical_page < source.physical_page
                    and int(adjacent[-1][1]) == int(steps[0][1]) - 1):
                first = len(adjacent) - 1
                while first > 0 and int(adjacent[first - 1][1]) == int(adjacent[first][1]) - 1:
                    first -= 1
                start = adjacent[first].start()
                text = neighbour.text[start:].strip()
            elif (neighbour.physical_page > source.physical_page
                    and int(adjacent[0][1]) == int(steps[-1][1]) + 1):
                prefix = neighbour.text[:adjacent[0].start()].strip()
                if re.search(r"(?m)^\s*[-•⚫]\s*[A-Za-z]", prefix):
                    text = prefix
            if not 25 <= len(text) <= SOURCE_PASSAGE_MAX or len(result) >= 8:
                continue
            if any(item.evidence_id == neighbour.evidence_id
                   and normalized(text) in normalized(item.text) for item in result):
                continue
            result.append(Passage(
                id=f"context{len(result)}", evidence_id=neighbour.evidence_id, text=text,
            ))
    return result


def _reviewed_condition_passages(question: str, source: Evidence, root: Path) -> list[str]:
    """Use complete reviewed events for a named alarm question.

    Never narrow a request to operate/disconnect/repair equipment this way.
    Access and version gates have already chosen the eligible evidence pages.
    """
    if source.kind == "service" or re.search(
        r"센서|분리|제거|수리|교체|만지|해도|안전|금지|주의|"  # noqa: ISC003
        + r"sensor|disconnect|remove|repair|replace|touch|safe|prohibit", question, re.IGNORECASE,
    ):
        return []
    if not re.search(r"언제|조건|경보|알람|표시|뜨|출력|when|condition|alarm|output", question,
                     re.IGNORECASE):
        return []
    matches: list[str] = []
    for record in structure_for_passage(root, [source], [source.evidence_id], source.text):
        if record["kind"] != "conditions":
            continue
        outcomes = " ".join(row.get("result", "") for row in record["relations"])
        # Quoted display labels are source data, never a product-specific rule.
        labels = re.findall(r"[\u2018\"\u201c]([^\u2019\"\u201d]+)[\u2019\"\u201d]", outcomes)
        if any(len(label) >= 4 and normalized(label) in normalized(question) for label in labels):
            matches.append(record["source_text"])
    return matches


def select_answer(  # noqa: C901, PLR0912, PLR0915
    question: str,
    sources: list[Evidence],
    ollama: Ollama,
    root: Path,
    *,
    ranking_question: str | None = None,
    conversation_context: str = "",
) -> AnswerResult:
    system = (root / "prompts" / "select.system.ko.txt").read_text(encoding="utf-8")
    schema_path = root / "schemas" / "selection.schema.json"
    schema_bytes = len(
        json.dumps(json.loads(schema_path.read_text(encoding="utf-8")), ensure_ascii=False).encode(
            "utf-8"
        )
    )
    raw_candidates: list[tuple[str, str]] = []
    preuse_candidates: list[tuple[str, str, str]] = []
    preuse_question = _is_preuse_question(question)
    reviewed_conditions: list[tuple[str, str]] = []
    reviewed_tables: list[tuple[str, str]] = []
    workplace_candidates: list[tuple[str, str]] = []
    for source in sources:
        care_warnings = (_probe_care_warning(question, source.text)
                         + _screen_cloth_items(question, source.text))
        workplace_candidates.extend(
            (source.evidence_id, text if text in care_warnings
             else complete_structured_passage(root, source, text))
            for text in _workplace_scoped_passages(question, source.text)
            if source.kind != "service" or safe_service_passage(question, text)
        )
        if (source.kind != "service"
                and re.search(r"접촉\s*부품|재질|소재|enclosure|material|contacting\s+component",
                              question, re.IGNORECASE)
                and not re.search(
                    r"방법|절차|순서|농도|how\s+to|procedure", question, re.IGNORECASE,
                )):
            reviewed_tables.extend(
                (source.evidence_id, record["source_text"])
                for record in structure_for_passage(
                    root, [source], [source.evidence_id], source.text,
                ) if record["kind"] == "table"
            )
        reviewed_conditions.extend(
            (source.evidence_id, text)
            for text in _reviewed_condition_passages(question, source, root)
        )
        for raw_text in question_passages(question, source):
            text = complete_structured_passage(root, source, raw_text)
            if source.kind == "service" and not safe_service_passage(question, text):
                continue
            if 25 <= len(text) <= SOURCE_PASSAGE_MAX and not _is_heading_only(text):
                raw_candidates.append((source.evidence_id, text))
        if preuse_question:
            preuse_candidates.extend(
                (source.evidence_id, source.kind, text)
                for text in _preuse_section_passages(question, source.text)
                if 25 <= len(text) <= SOURCE_PASSAGE_MAX
                and not _is_heading_only(text)
                and (source.kind != "service" or safe_service_passage(question, text))
            )
    if reviewed_tables:
        raw_candidates = reviewed_tables
    elif preuse_candidates:
        # Prefer complete user-manual inspection sections globally. Quick
        # guides remain a fallback when no user manual has an explicit section;
        # service material remains the final fallback for authorized searches.
        user_preuse = [item for item in preuse_candidates if item[1] == "user"]
        non_service_preuse = [item for item in preuse_candidates if item[1] != "service"]
        selected_preuse = user_preuse or non_service_preuse or preuse_candidates
        raw_candidates = [(evidence_id, text) for evidence_id, _, text in selected_preuse]
    elif reviewed_conditions:
        raw_candidates = reviewed_conditions
    elif workplace_candidates:
        raw_candidates = [item for item in workplace_candidates
                          if 25 <= len(item[1]) <= SOURCE_PASSAGE_MAX]
    ordinary_rows = [item for item in raw_candidates
                     if _ordinary_printer_error_row(question, item[1])]
    if ordinary_rows:
        raw_candidates = ordinary_rows
    if service_overview_question(question) and re.search(
        r"고장\s*분류|fault\s+classification", question, re.IGNORECASE,
    ):
        # Classification is not an invitation to substitute an ordinary sensor
        # troubleshooting table when the requested service overview is absent.
        raw_candidates = [
            item for item in raw_candidates
            if safe_service_passage(question, item[1]) and re.search(
                r"fault\s+classification|component[-\s]+level\s+maintenance|고장.{0,5}분류",
                item[1], re.IGNORECASE,
            )
        ]
    device_overviews = {
        (source.evidence_id, text)
        for source in sources if source.kind != "service"
        for text in _device_principle_passages(question, source.text)
    }
    preferred_overviews = [item for item in raw_candidates if item in device_overviews]
    if preferred_overviews:
        raw_candidates = preferred_overviews
    if _principle_question(question) and not preferred_overviews:
        user_ids = {source.evidence_id for source in sources if source.kind != "service"}
        mechanism = re.compile(
            "".join((  # noqa: FLY002
                r"(?:도플러|초음파|진공|압력|전기적|적외선|센서).{0,60}(?:이용|측정|감지|발생|방사)|",
                r"(?:uses?|using|principle|measur|detect|produced|operates?|utilizes?|applies).{0,240}",
                r"(?:doppler|ultrasound|vacuum|pressure|electrical|infrared|sensor)",
            )),
            re.IGNORECASE | re.DOTALL,
        )
        user_mechanisms = [item for item in raw_candidates
                           if item[0] in user_ids and mechanism.search(item[1])]
        if user_mechanisms:
            raw_candidates = user_mechanisms
    if _is_broad_principle_question(question):
        # A broad device-principle question must not fall through to an
        # SpO2/ECG/NIBP component chapter when no device overview exists.
        # Keep such chapters for explicitly scoped component questions.
        device_level = [item for item in raw_candidates
                         if not _component_principle_heading(item[1])]
        raw_candidates = device_level
    if _operator_sleep_question(question):
        # A generic introduction/absence of contraindications cannot answer
        # whether an operator may sleep during use.
        raw_candidates = [item for item in raw_candidates if _operator_sleep_prohibition(item[1])]
    if _battery_duration_question(question):
        # A safety/aging paragraph cannot answer a requested duration. Keep
        # the numeric specification as a complete source passage, or abstain.
        raw_candidates = [item for item in raw_candidates if _has_battery_duration(item[1])]
    candidates = [
        (_selection_relevance(question, text, ranking_question)
         + (1.0 if (evidence_id, text) in device_overviews else 0.0), evidence_id, text)
        for evidence_id, text in _unique_passages(raw_candidates)
    ]
    candidates = _component_scoped_candidates(question, candidates)
    capability_candidates: list[tuple[float, str, str]] = []
    for score, evidence_id, text in candidates:
        capability = _memory_capability_passage(question, text)
        if capability:
            capability_candidates.append((score, evidence_id, capability))
    if capability_candidates:
        candidates = capability_candidates
    if re.search(
        r"(?:저장|메모리).{0,25}(?:순서|시퀀스)|(?:saved|stored|memory).{0,30}sequence",
        question, re.IGNORECASE,
    ):
        memory_candidates = [
            item for item in candidates if re.search(
                r"(?:saved|stored|memory).{0,35}(?:sequence|operation)|sequence.{0,35}(?:saved|stored)|(?:저장|메모리).{0,25}(?:순서|시퀀스)",
                item[2], re.IGNORECASE,
            )
        ]
        if memory_candidates:
            candidates = memory_candidates
        # Prefer the complete instructions naming both controls over an example
        # whose icon-only button labels disappeared during PDF extraction.
        if re.search(r"시작|중지|start|stop|run", question, re.IGNORECASE):
            named_controls = [
                item for item in candidates
                if re.search(r"(?:play|start)\s+button", item[2], re.IGNORECASE)
                and re.search(r"stop\s+button", item[2], re.IGNORECASE)
            ]
            if named_controls:
                candidates = named_controls
    ranked = sorted(
        [candidate for candidate in _deduplicate_windows(candidates) if candidate[0] >= 0.08],
        # Preserve source order for equal relevance. Near-duplicate windows
        # have already retained their complete conditions above.
        key=lambda item: -item[0],
    )
    if preuse_question:
        # The passages are complete sections; cap their count before the LLM
        # input is assembled so required warnings/steps are never truncated.
        ranked = ranked[:2]
    if _principle_question(question) and not preferred_overviews:
        mechanism = re.compile(
            "".join(  # noqa: FLY002
                (
                    r"원리|principle|electrocardiogram|\becg\b|sp[o0]2|도플러|doppler|",
                    r"초음파|ultrasound|heart\s*rate|심박|temperature|humidity|온도|습도|",
                    r"진공|흡입|음압|공기압|압력|펌프|다이어프램|vacuum|suction|negative\s+pressure|pressure|pump|diaphragm",
                )
            ),
            re.IGNORECASE,
        )
        operational = re.compile(
            "".join(  # noqa: FLY002
                (
                    r"환경\s*(?:요구|조건)|environment(?:al)?\s+(?:requirements?|conditions?)|절차|procedure|patient\s+posture|",
                    r"환자\s*자세|start/stop|시작/?정지|malfunction|오류",
                )
            ),
            re.IGNORECASE,
        )
        principle_ranked = [
            item for item in ranked if mechanism.search(item[2]) and not operational.search(item[2])
        ]
        if principle_ranked:
            ranked = principle_ranked
    if not ranked:
        return AnswerResult(
            status="evidence_missing",
            mode="search",
            evidence=sources,
            limitation_codes=["no_matching_evidence"],
            message="질문에 직접 답할 수 있는 완전한 원문 문단을 찾지 못했습니다.",
        )
    dialogue = ({"dialogue_context_untrusted": conversation_context}
                if conversation_context else {})
    passages: list[Passage] = []
    for _, evidence_id, text in ranked[:30]:
        candidate = Passage(id=f"P{len(passages) + 1}", evidence_id=evidence_id, text=text)
        trial = [*passages, candidate]
        encoded = json.dumps(
            {
                "question": question,
                **dialogue,
                "passages": [item.model_dump(exclude={"evidence_id"}) for item in trial],
            },
            ensure_ascii=False,
        )
        if len(system.encode("utf-8")) + len(encoded.encode("utf-8")) + schema_bytes <= INPUT_BYTES:
            passages = trial
    if not passages:
        raise AppError("완전한 원문 문단을 AI 입력에 담을 수 없어 검색 결과를 표시합니다.", 413)
    user_json = json.dumps(
        {
            "question": question,
            **dialogue,
            "passages": [item.model_dump(exclude={"evidence_id"}) for item in passages],
        },
        ensure_ascii=False,
    )
    raw = ollama.generate(system, user_json, schema_path)
    try:
        selected = Selection.model_validate_json(raw)
    except ValidationError as error:
        raise AppError("AI 원문 선택 형식이 올바르지 않아 검색 결과를 표시합니다.", 422) from error
    by_id = {passage.id: passage for passage in passages}
    if len(set(selected.selected_ids)) != len(selected.selected_ids) or any(
        identifier not in by_id for identifier in selected.selected_ids
    ):
        raise AppError("AI가 제공되지 않은 원문 ID를 골라 답변을 채택하지 않았습니다.", 422)
    if not selected.selected_ids:
        return AnswerResult(
            status="evidence_missing",
            mode="search",
            evidence=sources,
            limitation_codes=["no_matching_evidence"],
            message="AI가 직접 답할 수 있는 문단을 고르지 못했습니다. 관련 원문을 확인해 주세요.",
        )
    chosen = [by_id[identifier] for identifier in selected.selected_ids]
    if (reviewed_conditions and chosen
            and (chosen[0].evidence_id, chosen[0].text) in reviewed_conditions):
        # Do not silently omit another mode of the same named alarm. Complete
        # the selected document's event set; never merge different versions.
        first_evidence = chosen[0].evidence_id
        complete_events = [passage for passage in passages
                           if passage.evidence_id == first_evidence
                           and (passage.evidence_id, passage.text) in reviewed_conditions]
        if 1 <= len(complete_events) <= 4:
            chosen = complete_events
    if question_intent(question) == "usage":
        chosen = _sequence_context(chosen, sources)
    chosen = _service_qualification_context(chosen, sources)
    result = validate_answer(
        json.dumps(
            {
                "status": "answered",
                "claims": [
                    {"id": f"C{index}", "text": passage.text, "citation_ids": [passage.evidence_id]}
                    for index, passage in enumerate(chosen, 1)
                ],
                "conflicts": [],
                "questions": [],
                "limitation_codes": [],
            },
            ensure_ascii=False,
        ),
        sources,
        root,
    )
    return result.model_copy(
        update={
            "message": "AI가 관련 원문을 골랐습니다. 아래 내용은 매뉴얼 문단 그대로입니다.",
        }
    )

