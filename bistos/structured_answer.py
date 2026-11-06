"""Compose reviewed source relations without a second, lossy paraphrase.

These are document facts, not question/answer pairs. Every eligible unit retains
its exact document, page and source hashes. Document access/version gates run
before this module and current_result rechecks access before returning it.
"""

import re
from pathlib import Path

from bistos.errors import AppError
from bistos.models import AnswerResult, Claim
from bistos.search import normalized
from bistos.source_structure import SourceRecord, structure_for_passage


def _matches(question: str, record: SourceRecord) -> bool:
    if re.search(
        r"센서|분리|제거|수리|교체|만지|해도|안전|금지|주의|고장|원인|왜|"  # noqa: ISC003
        + r"sensor|disconnect|remove|repair|replace|touch|safe|prohibit|fault|why",
        question, re.IGNORECASE,
    ):
        return False
    if record["kind"] == "table":
        return bool(re.search(
            r"접촉\s*부품|재질|소재|enclosure|material|contacting\s+component", question,
            re.IGNORECASE,
        )) and not bool(re.search(
            r"방법|절차|순서|농도|충전|배터리|시간|전원|how\s+to|procedure|battery|charging",
            question, re.IGNORECASE,
        ))
    scopes = " ".join(row.get("scope", "") for row in record["relations"]).casefold()
    requested_modes = [mode for mode, pattern in (
        ("baby", r"baby\s*(?:모드|mode)|아기\s*모드"),
        ("manual", r"manual\s*(?:모드|mode)|수동\s*모드"),
    ) if re.search(pattern, question, re.IGNORECASE)]
    if len(requested_modes) == 1 and requested_modes[0] not in scopes:
        return False
    outcomes = " ".join(row.get("result", "") for row in record["relations"])
    labels = re.findall(r"[\u2018\"\u201c]([^\u2019\"\u201d]+)[\u2019\"\u201d]", outcomes)
    labels.extend(re.findall(r"\(([^)]+)\)", outcomes))
    label_matches = any(
        len(label) >= 4 and normalized(label) in normalized(question) for label in labels
    )
    if label_matches:
        return bool(re.search(
            r"언제|조건|경보|알람|표시|뜨|출력|울|유지|when|condition|alarm|output",
            question, re.IGNORECASE,
        )) and not bool(re.search(
            r"무음|음량|해제|리셋|끄는|설정\s*방법|울리지|안\s*울|"  # noqa: ISC003
            + r"silence|reset|disable|how\s+to|does\s+not|doesn.t|not\s+(?:sound|activate)",
            question, re.IGNORECASE,
        ))
    # A source with remembered output has a complete state transition family.
    # Always retain its initial state, action, memory precondition and exception.
    has_memory_transition = (
        "previous set status" in outcomes.casefold()
        and any("exception" in row for row in record["relations"])
    )
    return has_memory_transition and bool(re.search(
        r"(?:High|Low|OFF).*(?:켜|끄|전원|조절)|(?:켜|전원).*(?:High|Low|OFF)|"  # noqa: ISC003
        + r"광량|광원\s*모드|intensity|restart", question, re.IGNORECASE,
    )) and not bool(re.search(r"안\s*켜|켜지지|not\s+turn|doesn.t\s+turn", question, re.IGNORECASE))


def reviewed_relation_answer(
    question: str, selected: AnswerResult, root: Path,
) -> AnswerResult | None:
    """Return complete reviewed rows from one eligible document page only."""
    if selected.status not in {"answered", "evidence_missing", "search_results"}:
        return None
    # The evidence list is already permission-, publication- and version-gated.
    # Never load a document from the registry that was not retrieved here.
    for source in selected.evidence:
        if source.kind == "service":
            continue
        records = [record for record in structure_for_passage(
            root, [source], [source.evidence_id], source.text,
        ) if record["translated_relations"] and _matches(question, record)]
        if not records:
            continue
        claims: list[Claim] = []
        seen: set[str] = set()
        for record in records:
            text = "\n".join(record["translated_relations"])
            if text in seen:
                continue
            if len(text) > 700 or len(claims) >= 4:
                raise AppError("검수한 조건 전체가 답변 한도를 넘어 원문을 표시합니다.", 422)
            seen.add(text)
            claims.append(Claim(id=f"C{len(claims) + 1}", text=text,
                                citation_ids=[source.evidence_id], presentation="direct"))
        return selected.model_copy(update={
            "status": "answered", "claims": claims, "questions": [],
            "message": "매뉴얼에서 확인한 답변입니다.",
            "limitation_codes": [code for code in selected.limitation_codes
                                 if code not in {"no_matching_evidence", "explanation_unverified"}],
        })
    return None
