"""Conservative source-backed terminology repairs for Korean explanations."""

import re

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?\u3002\uff01\uff1f])\s+|\n+")
_CUFF_DEFLATED = re.compile(
    r"\bcuff\b.{0,100}\bdeflated\b|\bdeflated\b.{0,100}\bcuff\b",
    re.IGNORECASE,
)
_BATTERY = re.compile(r"battery|배터리", re.IGNORECASE)
_DEFLATED_CUFF = re.compile(r"(?P<complete>완전히\s*)?방전된\s*커프")
_DRAIN_CUFF = re.compile(
    r"커프를\s*(?P<complete>완전히\s*)?방전(?P<ending>시키고|시키세요|하여|하고|한)(?=\s|[.!?]|$)"
)
_MANUAL_ASSEMBLY = re.compile(r"수동으로\s*조립|수동\s*조립")


def _has_cuff_air_deflation_source(source: str) -> bool:
    """Require cuff and deflated to describe the same local source sentence."""
    for sentence in _SENTENCE_SPLIT.split(source):
        if _BATTERY.search(sentence):
            continue
        if _CUFF_DEFLATED.search(sentence):
            return True
    return False


def _normalize_cuff(text: str, source: str) -> str:
    if not _has_cuff_air_deflation_source(source):
        return text
    updated = _DEFLATED_CUFF.sub(
        lambda match: "공기를 " + ("완전히 " if match.group("complete") else "") + "뺀 커프",
        text,
    )

    def replace_action(match: re.Match[str]) -> str:
        ending = match.group("ending") or ""
        replacement = {
            "시키고": "빼고",
            "시키세요": "빼세요",
            "시키": "빼",
            "하여": "빼어",
            "하고": "빼고",
            "한": "뺀",
        }.get(ending, "뺀")
        complete = "완전히 " if match.group("complete") else ""
        return f"커프의 공기를 {complete}{replacement}"

    return _DRAIN_CUFF.sub(replace_action, updated)


def _normalize_manual_assembly(text: str, source: str) -> str:
    if not re.search(r"according\s+to\s+(?:this|the)\s+manual", source, re.IGNORECASE):
        return text
    if re.search(r"\bmanually\b", source, re.IGNORECASE):
        return text
    return _MANUAL_ASSEMBLY.sub("설명서에 따라 조립", text)


def normalize_explanation_terms(text: str, source: str) -> str:
    """Repair only translations whose source gives an unambiguous local cue.

    The function is deliberately idempotent and leaves text unchanged when the
    source does not establish the corresponding English term relationship.
    """
    normalized = _normalize_cuff(text, source)
    return _normalize_manual_assembly(normalized, source)
