"""Guard recommendation wording against unsupported mandatory claims."""

import re

from .errors import AppError

_RECOMMENDATION_EN = r"\brecommend(?:ed|s|ing)?\b|\badvis(?:e|ed|es|ing)?\b"
_RECOMMENDATION_KO = r"\bsuggest(?:ed|s|ing)?\b|권장|권고|추천"
_RECOMMENDATION = re.compile(
    rf"(?:{_RECOMMENDATION_EN}|{_RECOMMENDATION_KO})", re.IGNORECASE,
)
_MANDATORY_EN = r"\b(?:must|required|shall|mandatory|need\s+to)\b"
_MANDATORY_KO = r"반드시|필수|의무|해야\s*(?:합니다|한다|함)?|하여야\s*(?:합니다|한다|함)?"
_MANDATORY = re.compile(rf"(?:{_MANDATORY_EN}|{_MANDATORY_KO})", re.IGNORECASE)
_CLAUSE_SPLIT = re.compile(r"(?<=[.!?。！？;；])\s+|\n+")  # noqa: RUF001
_TIMING_GROUPS: dict[str, re.Pattern[str]] = {
    "each_use": re.compile(
        r"before\s+(?:each\s+)?use|before\s+every\s+use|사용\s*전|사용할\s*때마다",
        re.IGNORECASE,
    ),
    "service": re.compile(
        r"before\s+service|during\s+service|정비|서비스|수리|repair",
        re.IGNORECASE,
    ),
    "treatment": re.compile(r"during\s+treatment|치료\s*중|치료할\s*때", re.IGNORECASE),
}

# These groups are intentionally generic.  They provide a small bilingual
# bridge so a Korean explanation can be checked against an English source
# without tying the guard to a product or question.
_ACTION_GROUPS: dict[str, re.Pattern[str]] = {
    "replace": re.compile(r"\breplac\w*\b|교체", re.IGNORECASE),
    "check": re.compile(
        r"\b(?:check|inspect|verify|confirm|examine)\w*\b|확인|점검|검사|확인",
        re.IGNORECASE,
    ),
    "measure": re.compile(r"\bmeasure\w*\b|측정", re.IGNORECASE),
    "clean": re.compile(r"\b(?:clean|sanitize|disinfect)\w*\b|세척|소독|청소", re.IGNORECASE),
    "charge": re.compile(r"\bcharg\w*\b|충전", re.IGNORECASE),
    "connect": re.compile(r"\bconnect\w*\b|연결", re.IGNORECASE),
    "use": re.compile(r"\buse\w*\b|사용", re.IGNORECASE),
    "wear": re.compile(r"\bwear\w*\b|착용", re.IGNORECASE),
    "protect": re.compile(r"\bprotect\w*\b|보호", re.IGNORECASE),
    "leave": re.compile(r"\bleav\w*\b|담가두|담가\s*두|방치|말리", re.IGNORECASE),
}
_OBJECT_GROUPS: dict[str, re.Pattern[str]] = {
    "light": re.compile(r"\blight\b|\bled\b|illumination|조명|빛|광원|led", re.IGNORECASE),
    "intensity": re.compile(r"\bintens\w*\b|\bstrength\b|강도|광도|세기", re.IGNORECASE),
    "cable": re.compile(r"\bcable\b|\bcord\b|케이블|코드|전원선", re.IGNORECASE),
    "battery": re.compile(r"\bbatter\w*\b|배터리", re.IGNORECASE),
    "temperature": re.compile(r"\btemperatur\w*\b|온도", re.IGNORECASE),
    "pressure": re.compile(r"\bpressure\b|압력", re.IGNORECASE),
    "manual": re.compile(r"\bmanual\b|설명서|매뉴얼", re.IGNORECASE),
    "eye": re.compile(r"\beye\w*\b|눈", re.IGNORECASE),
    "nipple": re.compile(r"\bnipple\w*\b|젖꼭지", re.IGNORECASE),
}


def _features(clause: str) -> tuple[set[str], set[str]]:
    actions = {name for name, pattern in _ACTION_GROUPS.items() if pattern.search(clause)}
    # "Hours of use" is an interval qualifier, not a second action. It must
    # not let mandatory use of approved parts override a recommended check.
    without_usage_intervals = re.sub(
        r"\b(?:hours?|days?|weeks?|months?|years?)\s+(?:of\s+)?use\b", "", clause,
        flags=re.IGNORECASE,
    )
    if not _ACTION_GROUPS["use"].search(without_usage_intervals):
        actions.discard("use")
    # "Period of use" describes a replacement interval, not an instruction to
    # use a damaged device. Keep that noun phrase separate from the disposal
    # predicate in the next Korean clause.
    if re.search(r"period\s+of\s+use|사용\s*기간|교체\s*주기", clause, re.IGNORECASE):
        actions.discard("use")
        actions.add("replace")
    objects = {name for name, pattern in _OBJECT_GROUPS.items() if pattern.search(clause)}
    return actions, objects


def _same_action(
    source_features: tuple[set[str], set[str]], answer_features: tuple[set[str], set[str]],
) -> bool:
    source_actions, source_objects = source_features
    answer_actions, answer_objects = answer_features
    if not source_actions.intersection(answer_actions):
        return False
    return not source_objects or bool(source_objects.intersection(answer_objects))


def _timing(clause: str) -> set[str]:
    return {name for name, pattern in _TIMING_GROUPS.items() if pattern.search(clause)}


def _mandatory_action_features(clause: str) -> tuple[set[str], set[str]]:
    # A finding in an if-clause is not the action made mandatory afterwards:
    # "if damage is found, stop using" does not mandate an inspection interval.
    condition = re.match(r".*?(?:확인되면|발견되면|보이면)\s*", clause)
    if condition and not _MANDATORY.search(condition[0]):
        clause = clause[condition.end():]
    return _features(clause)


def _supported_inspection_content(
    recommended_clause: str, answer_clause: str, source_clauses: list[str],
) -> bool:
    """Separate a recommended cadence from the explicit content of an inspection."""
    if not re.search(r"inspection\s+interval|점검\s*주기|검사\s*주기",
                     recommended_clause, re.IGNORECASE):
        return False
    if not re.search(r"(?:점검|검사)\s*(?:시|할\s*때)", answer_clause):
        return False
    if re.search(r"\d|매번|매주|매일|매월|매년|사용\s*전|정기|주기|every|each|periodic",
                 answer_clause, re.IGNORECASE):
        return False
    return any(
        re.match(r"^[\s•⚫-]*(?:Examine|Inspect|Check)\b", source_clause, re.IGNORECASE)
        and not _RECOMMENDATION.search(source_clause)
        and _same_action(_features(source_clause), _features(answer_clause))
        for source_clause in source_clauses
    )


def check_modality(answer: str, source: str) -> None:
    """Reject a mandatory rewrite of a source recommendation for the same action.

    The check is clause-scoped.  A mandatory, unrelated safety clause in the
    same passage must not make a recommendation look mandatory, and an explicit
    mandatory clause for the same action takes precedence over a nearby
    recommendation.
    """
    # PDF line wrapping is not a clause boundary: the object and "recommended"
    # may be on different physical lines in the same source sentence.
    source = " ".join(source.replace("\\n", "\n").split())
    _check_prohibition_weakening(answer, source)
    _check_optional_posture_benefit(answer, source)
    source_clauses = [clause.strip() for clause in _CLAUSE_SPLIT.split(source) if clause.strip()]
    # A descriptive copula may join a recommendation and a separate mandatory
    # safety action in one sentence. Do not split verbal conjunctions such as
    # "확인하고": a final mandatory verb can still govern both actions there.
    answer = re.sub(r"(?:이며|이고)\s*,?\s*", ". ", answer)
    answer_clauses = [clause.strip() for clause in _CLAUSE_SPLIT.split(answer) if clause.strip()]
    mandatory_source = [
        (clause, _features(clause))
        for clause in source_clauses
        if _MANDATORY.search(clause) and not _RECOMMENDATION.search(clause)
    ]
    mandatory_answers = [
        clause for clause in answer_clauses if _MANDATORY.search(clause)
    ]
    for source_clause in source_clauses:
        if not _RECOMMENDATION.search(source_clause):
            continue
        source_features = _features(source_clause)
        if not source_features[0]:
            continue
        if any(
            _same_action(source_features, mandatory_features)
            and (
                not _timing(source_clause)
                or not _timing(mandatory_clause)
                or bool(_timing(source_clause) & _timing(mandatory_clause))
            )
            for mandatory_clause, mandatory_features in mandatory_source
        ):
            continue
        if any(
            _same_action(source_features, _mandatory_action_features(clause))
            and not _supported_damage_replacement(clause, source_clauses)
            and not _supported_inspection_content(source_clause, clause, source_clauses)
            for clause in mandatory_answers
        ):
            raise AppError(
                "원문이 권장한 동일한 조작을 답변에서 의무 사항으로 강화했습니다.", 422,
            )


def _check_prohibition_weakening(answer: str, source: str) -> None:
    """Keep a matched object/action prohibition distinct from mere advice."""
    strong = r"금지|해서는\s*안|해선\s*안|하면\s*안|하지\s*(?:마|말)|두지\s*(?:마|말)"
    soft = r"권장하지\s*않|추천하지\s*않|not\s+recommend|not\s+advis"
    answers = [clause for clause in _CLAUSE_SPLIT.split(answer) if clause.strip()]
    for clause in _CLAUSE_SPLIT.split(source):
        if not re.search(r"\bdo\s+not\b|\bnever\b", clause, re.IGNORECASE):
            continue
        features = _features(clause)
        if not all(features):
            continue
        matching = [out for out in answers if _same_action(features, _features(out))]
        if any(re.search(soft, out, re.IGNORECASE)
               and not re.search(strong, out, re.IGNORECASE) for out in matching):
            raise AppError("동일한 대상·조작의 원문 금지를 단순 비권장으로 약화했습니다.", 422)


def _check_optional_posture_benefit(answer: str, source: str) -> None:
    posture = r"posture|position|자세|체위"
    change = r"chang\w*|바꿔|바꾸|변경"
    source_clauses = _CLAUSE_SPLIT.split(source)
    if not any(re.search(posture, clause, re.IGNORECASE)
               and re.search(change, clause, re.IGNORECASE)
               and re.search(r"may\s+(?:maximize|improve|increase)", clause, re.IGNORECASE)
               for clause in source_clauses):
        return
    if any(re.search(posture, clause, re.IGNORECASE)
           and re.search(change, clause, re.IGNORECASE) and _MANDATORY.search(clause)
           for clause in source_clauses):
        return
    for clause in _CLAUSE_SPLIT.split(answer):
        if (re.search(posture, clause, re.IGNORECASE)
                and re.search(
                    r"(?:자세|체위)[^.!?]{0,30}(?:바꿔야|바꾸어야|변경해야)|"  # noqa: ISC003
                    + r"반드시[^.!?]{0,20}(?:자세|체위).{0,20}(?:바꾸|변경)|"
                    + r"must\s+change.{0,30}(?:posture|position)", clause, re.IGNORECASE,
                )
                and not re.search(r"해야\s*(?:하는\s*)?것은\s*아니|필수.{0,5}아니", clause)):
            raise AppError("자세 변경의 가능한 이점을 필수 조치로 강화했습니다.", 422)


def _supported_damage_replacement(answer_clause: str, source_clauses: list[str]) -> bool:
    """Recognize damage-triggered replacement separately from periodic advice."""
    if not re.search(r"손상|마모|파손|약화|damage|wear|weakness", answer_clause, re.IGNORECASE):
        return False
    if not re.search(r"(?:손상|마모|파손|약화).{0,20}(?:시|면|경우)|if|when", answer_clause,
                     re.IGNORECASE):
        return False
    if re.search(r"\d+\s*(?:개월|달|년|months?|years?)", answer_clause, re.IGNORECASE):
        return False
    return any(
        not _RECOMMENDATION.search(clause)
        and not re.search(r"not\s+(?:be\s+)?replac|교체하지\s*마|교체\s*금지", clause,
                          re.IGNORECASE)
        and re.search(r"damage|wear|weakness|손상|마모|파손|약화", clause, re.IGNORECASE)
        and re.search(r"replac\w*|throw\s+away|discard|교체|폐기|버리", clause, re.IGNORECASE)
        for clause in source_clauses
    )
