"""Deterministic checks for reviewed condition-event claims.

Reviewed condition-event records and narrowly explicit maintenance intervals
are checked independently. Checks reject contradictions; none accepts an answer.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import cast

from .errors import AppError

_MINUTES = r"(?P<minutes>\d{1,4})"
_ALERT = re.compile(r"(?:alarm|alert|warning|message|경보|알람|경고|메시지|표시)", re.IGNORECASE)
_MODES: dict[str, tuple[str, ...]] = {
    "Baby Mode": ("baby mode", "babymode", "baby 모드", "baby모드", "아기 모드", "아기모드"),
    "Manual Mode": (
        "manual mode", "manualmode", "manual 모드", "manual모드", "수동 모드", "수동모드"
    ),
}


def _clean(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _mode(scope: str) -> str:
    lowered = scope.casefold()
    for name, aliases in _MODES.items():
        if any(alias.casefold() in lowered for alias in aliases):
            return name
    return ""


def _event(row: Mapping[str, object]) -> tuple[str, str, int, str] | None:
    scope = _clean(row.get("scope"))
    mode = _mode(scope)
    result = _clean(row.get("result"))
    elapsed = _clean(row.get("elapsed"))
    when = _clean(row.get("when"))
    if not mode or not _ALERT.search(result):
        return None
    for timing in (elapsed, when):
        after = re.search(rf"\bafter\s+{_MINUTES}\s*minutes?\b", timing, re.IGNORECASE)
        if after:
            return mode, "after", int(after.group("minutes")), timing
        more = re.search(rf"\bmore\s+than\s+{_MINUTES}\s*minutes?\b", timing, re.IGNORECASE)
        if more:
            return mode, "more_than", int(more.group("minutes")), timing
        after_ko = re.search(rf"{_MINUTES}\s*분\s*후", timing)
        if after_ko:
            return mode, "after", int(after_ko.group("minutes")), timing
        more_ko = re.search(rf"{_MINUTES}\s*분\s*(?:초과|보다\s*오래)", timing)
        if more_ko:
            return mode, "more_than", int(more_ko.group("minutes")), timing
    return None


def _sentences(text: str) -> Iterable[str]:
    return (
        part.strip()
        for part in re.split(r"(?<=[.!?。！？])\s+|[\r\n]+", text)  # noqa: RUF001
        if part.strip()
    )


def _claim_mode(sentence: str) -> str:
    found = [
        name
        for name, aliases in _MODES.items()
        if any(alias.casefold() in sentence.casefold() for alias in aliases)
    ]
    return found[0] if len(found) == 1 else ""


def _matches_condition(sentence: str, comparator: str, minutes: int) -> bool:
    if not re.search(rf"\b{minutes}\s*(?:minutes?|min)\b|{minutes}\s*분", sentence, re.IGNORECASE):
        return False
    if comparator == "after":
        return bool(
            re.search(rf"\bafter\s+{minutes}\s*minutes?\b", sentence, re.IGNORECASE)
            or re.search(rf"{minutes}\s*분\s*후", sentence)
            or re.search(rf"{minutes}\s*분\s*(?:이|을)?\s*(?:지나|경과)", sentence)
        ) and not bool(re.search(rf"{minutes}\s*분\s*(?:을|이)?\s*(?:이상|초과)", sentence))
    return bool(
        re.search(rf"\bmore\s+than\s+{minutes}\s*minutes?\b", sentence, re.IGNORECASE)
        or re.search(rf"{minutes}\s*분\s*(?:을|이)?\s*(?:초과|보다\s*오래)", sentence)
    ) and not bool(re.search(rf"{minutes}\s*분\s*이상", sentence))


def _is_output_claim(sentence: str) -> bool:
    return bool(
        re.search(r"(?:output|출력).*(?:decreas|reduc|감소|줄어|25\s*%)", sentence, re.IGNORECASE)
    )


def _reviewed_events(records: Sequence[Mapping[str, object]]) -> list[tuple[str, str, int, str]]:
    events: list[tuple[str, str, int, str]] = []
    for record in records:
        if record.get("kind") != "conditions" or record.get("reviewed") is not True:
            continue
        relations_value = record.get("relations")
        if not isinstance(relations_value, list):
            continue
        relations = cast("list[object]", relations_value)
        for row in relations:
            if not isinstance(row, dict):
                continue
            typed_row: dict[str, object] = {}
            raw_row = cast("dict[object, object]", row)
            for key, value in raw_row.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    typed_row = {}
                    break
                typed_row[key] = value
            if not typed_row:
                continue
            event = _event(typed_row)
            if event:
                events.append(event)
    return events


def check_relation_claim(text: str, records: Sequence[Mapping[str, object]]) -> None:
    """Raise ``AppError(..., 422)`` when an alert timing claim changes its source.

    Only ``kind == conditions`` and explicit ``reviewed is True`` records are
    considered. A sentence mentioning output reduction without an alert is not a
    timing claim and is intentionally ignored.
    """
    events = _reviewed_events(records)
    if not events:
        return
    modes = {event[0] for event in events}
    for sentence in _sentences(text):
        has_minutes = re.search(r"\d+\s*(?:minutes?|min|분)", sentence, re.IGNORECASE)
        alarm_time_mentioned = any(
            re.search(rf"{event[2]}\s*(?:분|minutes?\b)", sentence, re.IGNORECASE)
            for event in events
        )
        if (not _ALERT.search(sentence) or not has_minutes
                or (_is_output_claim(sentence) and not alarm_time_mentioned)):
            continue
        claimed_mode = _claim_mode(sentence)
        if len(modes) > 1 and not claimed_mode:
            raise AppError("경보 시간 조건의 모드 범위를 명시해야 합니다.", 422)
        candidates = [event for event in events if not claimed_mode or event[0] == claimed_mode]
        if not any(_matches_condition(sentence, event[1], event[2]) for event in candidates):
            scope = claimed_mode or ", ".join(sorted(modes))
            source = "; ".join(f"{event[0]}: {event[3]}" for event in candidates)
            raise AppError(f"{scope}의 원문 조건({source})과 경보 시간이 다릅니다.", 422)


_INSPECT = re.compile(r"\b(?:check(?:ed|ing)?|inspect\w*)\b|점검|검사|확인", re.IGNORECASE)
_REPLACE = re.compile(
    r"\breplac\w*\b|\bchang\w*\s+(?:the\s+)?(?:LEDs?|lamps?|filters?|batter\w*|components?)\b|교체",
    re.IGNORECASE,
)
_NEGATED_MAINTENANCE = re.compile(
    r"\b(?:do\s+not|don't|never)\b|않|아니|금지|하지\s*마", re.IGNORECASE,
)


def _maintenance_clauses(text: str) -> list[str]:
    compact = " ".join(text.split())
    # PDF extraction may split the leading thousands group ("12 0,000").
    compact = re.sub(r"\d[\d ]*,\d{3}(?:,\d{3})*",
                     lambda match: match[0].replace(" ", "").replace(",", ""), compact)
    return [part.strip() for part in re.split(
        r"(?<=[.!?;])\s+|,|\b(?:and|also|whereas)\b|그리고|(?:하|되|이)며\s*",
        compact, flags=re.IGNORECASE,
    ) if part.strip()]


def _maintenance_hours(clause: str) -> set[float]:
    # Korean particles can attach directly to 시간, so don't require a word
    # boundary after that unit (the English alternatives remain bounded).
    return {float(match[1]) for match in re.finditer(
        r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:hours?\b|hrs?\b|시간)", clause, re.IGNORECASE,
    )}


def _maintenance_actions(clause: str) -> set[str]:
    return {name for name, pattern in (("inspection", _INSPECT), ("replacement", _REPLACE))
            if pattern.search(clause)}


def _maintenance_bindings(source_text: str) -> dict[str, set[float]] | None:
    bindings: dict[str, set[float]] = {"inspection": set(), "replacement": set()}
    for clause in _maintenance_clauses(source_text):
        if _NEGATED_MAINTENANCE.search(clause):
            continue
        hours, actions = _maintenance_hours(clause), _maintenance_actions(clause)
        elliptical = re.match(r"another\b|다른|또\s*다른", clause, re.IGNORECASE)
        if ((hours and (len(actions) > 1 or len(hours) > 1))
                or (hours and not actions and elliptical)):
            return None
        if len(hours) == 1 and len(actions) == 1:
            bindings[next(iter(actions))].update(hours)
    if (any(len(values) != 1 for values in bindings.values())
            or bindings["inspection"] == bindings["replacement"]):
        return None
    return bindings


def check_maintenance_intervals(text: str, source_text: str) -> None:
    """Reject swapping or merging two explicitly distinct maintenance periods.

    This does not infer an interval from equipment lifetime or unrelated run
    time. Ambiguous source clauses, multiple periods per action, absent action
    pairs and negated statements are left to the existing grounding checks.
    """
    bindings = _maintenance_bindings(source_text)
    if bindings is None:
        return
    previous_hours: set[float] = set()
    for clause in _maintenance_clauses(text):
        if _NEGATED_MAINTENANCE.search(clause):
            previous_hours = set()
            continue
        hours, actions = _maintenance_hours(clause), _maintenance_actions(clause)
        if not hours and actions and re.search(
            r"이때|그때|동시에|함께|같은\s*(?:시점|때)|\bat\s+the\s+same\s+time\b",
            clause, re.IGNORECASE,
        ):
            hours = previous_hours
        if len(hours) == 1 and actions:
            for action in actions:
                if hours != bindings[action]:
                    message = "점검과 부품 교체의 서로 다른 시간 조건을 합치거나 바꾸었습니다."
                    raise AppError(message, 422)
        # An unrelated intervening clause must not lend its time to an action.
        previous_hours = hours if actions else set()


_PATIENT_ROLES = {
    "multiple": re.compile(
        "|".join((r"multiple\s+(?:patient|people)|different\s+patients|여러\s*(?:환자|사람)",  # noqa: FLY002
                  r"다수(?:의)?\s*환자|다른\s*환자|환자(?:가|이)\s*바뀌")), re.IGNORECASE,
    ),
    "single": re.compile(
        "|".join((r"individual\s+patient|single\s+patient|one\s+(?:patient|person)",  # noqa: FLY002
                  r"한\s*(?:명(?:의)?\s*)?환자|개별\s*환자|단일\s*환자")), re.IGNORECASE,
    ),
}
_NEGATED_PATIENT_ROLE = re.compile(
    r"아니|아닙|아닌|아님|아닐|않|금지|말고|마세요|\b(?:not|never|instead\s+of)\b",
    re.IGNORECASE,
)


def _patient_mode_bindings(source_text: str) -> dict[str, str]:
    """Read explicit quoted labels only, never infer mode names from a query."""
    if not re.search(r"\bmode\b|모드", source_text, re.IGNORECASE):
        return {}
    definitions = list(re.finditer(r"""["“']([^"”'\r\n]{1,40})["”']\s*:""", source_text))
    bindings: dict[str, str] = {}
    for index, match in enumerate(definitions):
        end = definitions[index + 1].start() if index + 1 < len(definitions) else len(source_text)
        # The first clause is the explicit role definition. Later examples,
        # storage/alarm limits and their negations do not redefine that role.
        definition = re.split(r"[.!?,]", source_text[match.end():end], maxsplit=1)[0]
        if _NEGATED_PATIENT_ROLE.search(definition):
            continue
        roles = [role for role, pattern in _PATIENT_ROLES.items() if pattern.search(definition)]
        label = " ".join(match[1].split())
        if len(roles) != 1:
            continue
        if label in bindings and bindings[label] != roles[0]:
            return {}
        bindings[label] = roles[0]
    return bindings if len(set(bindings.values())) == 2 else {}


def check_patient_mode_roles(text: str, source_text: str) -> None:
    """Reject an explicit mode assigned the other documented patient scope.

    Both single-patient and multiple-patient roles must be explicitly defined
    under source labels. Negated comparisons and clauses with several labels
    remain with semantic grounding; absence of a match is not acceptance.
    """
    bindings = _patient_mode_bindings(source_text)
    if not bindings:
        return
    label_patterns = {
        label: re.compile(r"(?<![A-Za-z])" + r"[\s-]*".join(
            re.escape(part) for part in re.split(r"[\s-]+", label)
        ) + r"(?![A-Za-z])", re.IGNORECASE)
        for label in bindings
    }
    clauses = re.split(r"[.!?;\n]|,|반면|\bwhereas\b", text, flags=re.IGNORECASE)
    for clause in clauses:
        if _NEGATED_PATIENT_ROLE.search(clause):
            continue
        labels = [label for label, pattern in label_patterns.items() if pattern.search(clause)]
        if len(labels) != 1:
            continue
        roles = {role for role, pattern in _PATIENT_ROLES.items() if pattern.search(clause)}
        if roles - {bindings[labels[0]]}:
            raise AppError("모드별 단일 환자와 여러 환자의 적용 범위를 바꾸었습니다.", 422)


__all__ = ["check_maintenance_intervals", "check_patient_mode_roles", "check_relation_claim"]
