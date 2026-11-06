"""Expose narrowly comparable optical-wavelength differences without blocking an answer."""

import re
from itertools import pairwise

from bistos.models import AnswerResult, Conflict, Evidence, Variant

_SPO2 = re.compile(r"Sp\s*O\s*[2₂]|산소\s*포화도", re.IGNORECASE)
_WAVELENGTH = re.compile(r"(?<![\d.])(660|905|950)\s*nm(?![A-Za-z])", re.IGNORECASE)
_MECHANISM = re.compile(r"원리|흡수|투과|principle|absorp|transmi", re.IGNORECASE)


def _pair(text: str, *, mechanism: bool) -> int | None:
    if not _SPO2.search(text):
        return None
    values = list(_WAVELENGTH.finditer(text))
    found: set[int] = set()
    for first, second in pairwise(values):
        numbers = {int(first.group(1)), int(second.group(1))}
        if 660 not in numbers or len(numbers) != 2 or second.start() - first.end() > 250:
            continue
        between = text[first.end() : second.start()]
        if re.search(r"~|\u2013|\u2014|\bto\b|부터|에서", between, re.IGNORECASE):
            continue
        context = text[max(0, first.start() - 300) : second.end() + 300]
        if mechanism and not _MECHANISM.search(context):
            continue
        found.add(next(number for number in numbers if number != 660))
    return next(iter(found)) if len(found) == 1 else None


def annotate_wavelength_conflicts(result: AnswerResult, sources: list[Evidence]) -> AnswerResult:
    """Flag only 660+905 versus 660+950 nm SpO2 mechanism statements.

    Different revisions are not assumed interchangeable or invalid. The answer
    retains its explanation while the differing measurement value is explicitly
    scoped to the cited source. Unrelated values and questions stay untouched.
    """
    by_id = {source.evidence_id: source for source in sources}
    claims = []
    conflicts = list(result.conflicts)
    changed = False
    for claim in result.claims:
        answer_pair = _pair(claim.text, mechanism=False)
        cited = [by_id[cid] for cid in claim.citation_ids if cid in by_id]
        products = {source.product_id for source in cited}
        if claim.presentation != "direct" or answer_pair is None or len(products) != 1:
            claims.append(claim)
            continue
        product = next(iter(products))
        variants: dict[int, Evidence] = {}
        for source in sorted(sources, key=lambda item: (item.document_id, item.physical_page)):
            if source.product_id != product:
                continue
            pair = _pair(source.text, mechanism=True)
            if pair is not None:
                variants.setdefault(pair, source)
        if set(variants) != {905, 950}:
            claims.append(claim)
            continue
        # Use one original source for each value, keeping the citation contract bounded.
        chosen = variants[answer_pair]
        other = variants[905 if answer_pair == 950 else 950]
        notice = (
            f"파장 표기가 문서마다 다릅니다. 다음 설명의 {answer_pair}nm는 "
            f"{chosen.title} ({chosen.revision})의 표기를 따릅니다. "
            f"다른 문서에는 {905 if answer_pair == 950 else 950}nm로 기재되어 "
            "파장 수치를 하나로 확정하지 않았습니다.\n"
        )
        ids = list(dict.fromkeys([*claim.citation_ids, chosen.evidence_id, other.evidence_id]))
        if len(ids) > 6 or len(notice + claim.text) > 2400:
            claims.append(claim)
            continue
        claims.append(claim.model_copy(update={"text": notice + claim.text, "citation_ids": ids}))
        conflicts.append(
            Conflict(
                topic="SpO2 근적외선 파장의 문서별 차이",
                variants=[
                    Variant(
                        text=f"{source.title} ({source.revision}): 660nm / {value}nm",
                        citation_ids=[source.evidence_id],
                    )
                    for value, source in sorted(variants.items())
                ],
            )
        )
        changed = True
    if not changed:
        return result
    return result.model_copy(
        update={
            "claims": claims,
            "conflicts": conflicts,
            "status": "partial_answer",
            "questions": [],
            "limitation_codes": list(dict.fromkeys([*result.limitation_codes, "source_conflict"])),
        }
    )
