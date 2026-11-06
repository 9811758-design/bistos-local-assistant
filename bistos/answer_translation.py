"""Translate presentation text only; never ask the model to rewrite evidence or citations."""

import json
import re
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from bistos.errors import AppError
from bistos.models import AnswerResult, AnswerTranslation, Model
from bistos.ollama import Ollama

FAILURE_MESSAGE = "영어 번역을 완료하지 못했습니다. 원래 답변을 표시합니다."
KOREAN_FAILURE_MESSAGE = "한국어 번역을 완료하지 못했습니다. 원문 답변을 표시합니다."
_NUMBER = r"[-+\u2212]?\d+(?:\.\d+)?"
_UNIT = r"mmHg|bpm|mA|mV|Hz|시간|분|초|회|번|도|℃|°C|%|V|A"
_UNIT_EN = {
    "초": r"seconds?|secs?",
    "분": r"minutes?|mins?",
    "시간": r"hours?|hrs?",
    "회": r"times?",
    "번": r"times?",
    "도": r"degrees?",
    "℃": r"℃|°C|degrees?\s+Celsius",
    "°C": r"℃|°C|degrees?\s+Celsius",
}
_BOUND_EN = {
    "이상": r"at\s+least",
    "이하": r"at\s+most",
    "이내": r"within",
    "초과": r"more\s+than",
    "미만": r"less\s+than",
}
_LABEL = re.compile(r'\[[^\]\n]+\]|"[^"\n]+"|\u201c[^\u201d\n]+\u201d|\u2018[^\u2019\n]+\u2019')
_BOUND = re.compile(rf"({_NUMBER})\s*({_UNIT})?\s*(이상|이하|이내|초과|미만)")
_PROTECTED = re.compile(r"__TERM_\d+__")
_IP_PATTERN = r"(?<![\w.])(?:\d{1,3}\.){3}[0-9A-Za-zxX]+(?![A-Za-z0-9_-]|\.[A-Za-z0-9_-])"
_FILE_PATTERN = (
    r"(?<![\w.])[A-Za-z][A-Za-z0-9_.-]*\.(?:exe|dll|pdf|docx?)"
    r"(?![A-Za-z0-9_-]|\.[A-Za-z0-9_-])"
)
_TECHNICAL = re.compile(rf"(?:{_IP_PATTERN}|{_FILE_PATTERN})")
_UNIT_WORD = {"초": "seconds", "분": "minutes", "시간": "hours", "회": "times", "번": "times",
              "도": "degrees"}
_BOUND_WORD = {"이상": "at least", "이하": "at most", "이내": "within",
               "초과": "more than", "미만": "less than"}
_EN_UNIT = r"seconds?|secs?|minutes?|mins?|hours?|hrs?|times?|degrees?|°C|℃|mmHg|bpm|mA|mV|Hz|%|V|A"
_EN_BOUND = {
    "at least": "이상", "at most": "이하", "within": "이내",
    "more than": "초과", "less than": "미만",
}
_ACTION_TARGETS = {
    "alarm": (r"alarm", r"경보|알람"),
    "print": (r"print", r"인쇄|프린트|출력"),
    "power": (r"power", r"전원"),
    "hood": (r"hood|cover", r"후드|덮개"),
}
_KO_UNIT = {
    "second": r"초", "seconds": r"초", "sec": r"초", "secs": r"초",
    "minute": r"분", "minutes": r"분", "min": r"분", "mins": r"분",
    "hour": r"시간", "hours": r"시간", "hr": r"시간", "hrs": r"시간",
    "time": r"회|번", "times": r"회|번", "degree": r"도|℃", "degrees": r"도|℃",
    "°C": r"℃|°C|도", "℃": r"℃|°C|도", "mmHg": r"mmHg", "bpm": r"bpm",
    "mA": r"mA", "mV": r"mV", "Hz": r"Hz", "%": r"%", "V": r"V", "A": r"A",
}
_SMALL_NUMBERS = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
    "eighteen", "nineteen", "twenty",
)
_KO_NUMBER_WORDS = {
    1: r"첫째|첫\s*번째|한\s*(?=(?:번|회|초|분|시간|개|곳|단계|번째))|하나",
    2: r"둘째|두\s*번째|두\s*(?=(?:번|회|초|분|시간|개|곳|단계|번째))|둘",
    3: r"셋째|세\s*번째|세\s*(?=(?:번|회|초|분|시간|개|곳|단계|번째))|셋",
    4: r"넷째|네\s*번째|네\s*(?=(?:번|회|초|분|시간|개|곳|단계|번째))|넷",
    5: r"다섯째|다섯",
    6: r"여섯째|여섯",
    7: r"일곱째|일곱",
    8: r"여덟째|여덟",
    9: r"아홉째|아홉",
    10: r"열째|열",
}
_EN_NUMBER_WORDS = {
    1: r"one", 2: r"two", 3: r"three", 4: r"four", 5: r"five",
    6: r"six", 7: r"seven", 8: r"eight", 9: r"nine", 10: r"ten",
}


class TranslatedText(Model):
    text: str = Field(min_length=1, max_length=6000)


def answer_texts(result: AnswerResult) -> dict[str, str]:
    texts = {f"claim:{claim.id}": claim.text for claim in result.claims}
    for index, conflict in enumerate(result.conflicts):
        texts[f"conflict:{index}:topic"] = conflict.topic
        for variant_index, variant in enumerate(conflict.variants):
            texts[f"conflict:{index}:variant:{variant_index}"] = variant.text
    texts.update({f"question:{index}": text for index, text in enumerate(result.questions)})
    return texts


def _quantity(number: str, unit: str) -> str:
    # Lookarounds prevent accepting 2 seconds when the translation says 12 seconds.
    unit_pattern = _UNIT_EN.get(unit, re.escape(unit))
    return rf"(?<![\d.]){re.escape(number)}\s*(?:{unit_pattern})(?![A-Za-z])"


def _validate_quantities(  # noqa: C901
    source: str, translated: str, target: Literal["en", "ko"]
) -> None:
    source_for_numbers = _TECHNICAL.sub("", source) if target == "ko" else source
    translated_for_numbers = _TECHNICAL.sub("", translated) if target == "ko" else translated
    if Counter(re.findall(_NUMBER, source_for_numbers)) != Counter(
        re.findall(_NUMBER, translated_for_numbers)
    ):
        raise AppError("번역의 숫자가 원래 답변과 일치하지 않습니다.", 422)
    if target == "en":
        for number, unit in re.findall(rf"({_NUMBER})\s*({_UNIT})", source):
            if not re.search(_quantity(number, unit), translated):
                raise AppError("번역의 수치 단위를 확인할 수 없습니다.", 422)
        bounds = re.findall(rf"({_NUMBER})\s*({_UNIT})?\s*(이상|이하|이내|초과|미만)", source)
        for number, unit, bound in bounds:
            quantity = (
                _quantity(number, unit)
                if unit
                else rf"(?<![\d.]){re.escape(number)}(?![\d.])"
            )
            if not re.search(rf"{_BOUND_EN[bound]}\s+{quantity}", translated, re.IGNORECASE):
                raise AppError("번역에서 수치 조건이 변경되었거나 누락되었습니다.", 422)
        return
    # English source units and bounds must survive in Korean output. Technical
    # tokens are removed from the numeric scan because their exact restoration
    # is checked separately.
    unit_pattern = re.compile(rf"(?P<number>{_NUMBER})\s*(?P<unit>{_EN_UNIT})", re.IGNORECASE)
    for match in unit_pattern.finditer(_TECHNICAL.sub("", source)):
        number = match.group("number")
        unit = match.group("unit")
        korean_unit = _KO_UNIT.get(unit.lower(), re.escape(unit))
        if not re.search(
            rf"(?<![\d.]){re.escape(number)}\s*(?:{korean_unit})(?![A-Za-z])",
            translated,
        ):
            raise AppError("번역의 수치 단위를 확인할 수 없습니다.", 422)
    quantity_pattern = re.compile("".join([
        r"(?P<rel>at\s+least|at\s+most|within|more\s+than|less\s+than)\s+",
        rf"(?P<number>{_NUMBER})\s*(?P<unit>{_EN_UNIT})?",
    ]), re.IGNORECASE)
    for match in quantity_pattern.finditer(_TECHNICAL.sub("", source)):
        relation = _EN_BOUND[re.sub(r"\s+", " ", match.group("rel").lower())]
        number = match.group("number")
        unit = match.group("unit")
        unit_pattern = _KO_UNIT.get(unit.lower(), re.escape(unit)) if unit else ""
        if not re.search(
            rf"(?<![\d.]){re.escape(number)}\s*{unit_pattern}\s*{relation}",
            translated,
        ):
            raise AppError("번역의 수치 단위 또는 조건이 변경되었습니다.", 422)


def _validate_known_action_targets(
    source: str, translated: str, target: Literal["en", "ko"]
) -> None:
    """Check explicit press/push instructions, not general semantic equivalence."""
    english, korean = (source, translated) if target == "ko" else (translated, source)
    korean_targets = "|".join(pattern[1] for pattern in _ACTION_TARGETS.values())
    for english_target, korean_target in _ACTION_TARGETS.values():
        english_press = (
            r"(?P<negative>\b(?:do\s+not|don't|never|must\s+not)\s+)?"
            rf"\b(?:press|push)\s+(?:the\s+)?(?:{english_target})(?:\s+button)?\b"
        )
        korean_press = (
            rf"(?:{korean_target})(?:\s*버튼)?\s*(?:은|는|을|를)?\s*(?:다시\s*)?"
            r"(?P<prefix>안\s*)?(?:누르|눌러|누름)"
            rf"(?P<tail>(?:(?!{korean_targets})[^.;,!?\n]){{0,24}})"
        )
        english_polarities = {
            bool(match.group("negative"))
            for match in re.finditer(english_press, english, re.IGNORECASE)
        }
        korean_polarities = {
            bool(match.group("prefix")
                 or re.search(r"지\s*(?:마|말|않)|말고|마세요|금지", match.group("tail")))
            for match in re.finditer(korean_press, korean)
        }
        if (english_polarities and korean_polarities
                and english_polarities != korean_polarities):
            raise AppError("번역에서 알려진 동작 대상의 금지 조건이 변경되었습니다.", 422)


def validate_translation(  # noqa: C901, PLR0912
    source: str, translated: str, target: Literal["en", "ko"] = "en"
) -> None:
    """Reject detectable numeric/condition loss; this is not a semantic proof."""
    source = source.replace("\u2212", "-")
    translated = translated.replace("\u2212", "-")
    _validate_quantities(source, translated, target)
    _validate_known_action_targets(source, translated, target)
    for label in _LABEL.findall(source):
        if label not in translated:
            raise AppError("번역에서 원문 표시 이름이 변경되었습니다.", 422)
    outside_labels = _LABEL.sub("", translated)
    if Counter(_TECHNICAL.findall(source)) != Counter(_TECHNICAL.findall(translated)):
        raise AppError("번역의 기술 식별자가 변경되었습니다.", 422)
    repeat_pattern = "|".join((  # noqa: FLY002
        r"다시|재차|한\s*번\s*더|반복|재시작|재부팅|재시도|재연결|재접속|재실행",
        r"\bagain\b|\brepeat\w*\b|\bonce\s+more\b",
        r"\bre[-\s]?(?:start|boot|try|connect|open)\w*\b",
    ))
    source_repeats = bool(re.search(repeat_pattern, source, re.IGNORECASE))
    translated_repeats = bool(re.search(repeat_pattern, translated, re.IGNORECASE))
    if source_repeats != translated_repeats:
        raise AppError("번역에서 반복 동작 조건이 변경되었습니다.", 422)
    negative_pattern = (
        r"(?:않|없|아닙|아니다|아닌|아님|금지|불가|지\s*(?:마|말)|안\s*(?:되|됩|누르|눌러)|말아|못|마세요|"
        r"\b(?:not|never|cannot|can't|don't|doesn't|isn't|aren't|unable|without|"
        r"no|prohibited|forbidden|unavailable)\b|n't\b)"
    )
    source_negative = bool(re.search(negative_pattern, source, re.IGNORECASE))
    translated_negative = bool(re.search(negative_pattern, translated, re.IGNORECASE))
    if source_negative != translated_negative:
        raise AppError("번역에서 부정 또는 금지 조건이 변경되었습니다.", 422)
    if target == "en":
        if re.search(r"[가-힣]", outside_labels):
            raise AppError("영어로 번역되지 않은 답변입니다.", 422)
        if re.search(r"\bautomatic\w*\b", translated, re.IGNORECASE) and not re.search(
            r"자동|automatic", source, re.IGNORECASE
        ):
            raise AppError("번역에 원문에 없는 자동 동작이 추가되었습니다.", 422)
    else:
        if not re.search(r"[가-힣]", outside_labels):
            raise AppError("한국어로 번역되지 않은 답변입니다.", 422)
        if re.search(r"\bautomatic\w*\b", source, re.IGNORECASE) and not re.search(
            r"자동", translated
        ):
            raise AppError("번역에서 자동 동작 조건을 확인할 수 없습니다.", 422)
        if re.search(r"자동", translated) and not re.search(
            r"\bautomatic\w*\b", source, re.IGNORECASE
        ):
            raise AppError("번역에 원문에 없는 자동 동작이 추가되었습니다.", 422)
    identifiers = r"\bBT[- ]?\d+[A-Za-z0-9-]*"
    if Counter(re.findall(identifiers, source)) != Counter(re.findall(identifiers, translated)):
        raise AppError("번역의 제품 식별자가 변경되었습니다.", 422)


def _protect_terms(source: str) -> tuple[str, dict[str, str]]:
    if _PROTECTED.search(source):
        raise AppError("번역 입력에 예약된 표시가 있습니다.", 422)
    replacements: dict[str, str] = {}

    def remember(text: str) -> str:
        token = f"__TERM_{len(replacements):03d}__"
        replacements[token] = text
        return token

    def bound(match: re.Match[str]) -> str:
        number, unit, relation = match.groups()
        quantity = f"{number} {_UNIT_WORD.get(unit, unit)}" if unit else number
        return remember(f"{_BOUND_WORD[relation]} {quantity}")

    masked = _LABEL.sub(lambda match: remember(match.group()), source)
    masked = _TECHNICAL.sub(lambda match: remember(match.group()), masked)
    return _BOUND.sub(bound, masked), replacements


def _restore_terms(output: str, replacements: dict[str, str]) -> str:
    tokens = Counter(_PROTECTED.findall(output))
    expected = Counter(replacements.keys())
    if any(token not in expected or count != 1 for token, count in tokens.items()):
        raise AppError("번역에서 보호된 이름 또는 조건이 변경되었습니다.", 422)
    # Some local models copy a protected label/technical term literally
    # instead of returning its token. Accept that only when every missing
    # token's exact value is present; the final validators still decide safety.
    if any(token not in tokens and value not in output for token, value in replacements.items()):
        raise AppError("번역에서 보호된 이름 또는 조건이 변경되었습니다.", 422)
    # Some local models expand tokens themselves. The restored final text must
    # still pass the exact label, numeric, unit and bound checks below.
    return _PROTECTED.sub(lambda match: replacements[match.group()], output)


def _numeric_unit_words(source: str, output: str) -> str:
    """Normalize e.g. 'four times' to '4 times' only for a source quantity."""
    for number, unit in re.findall(rf"({_NUMBER})\s*({_UNIT})", source):
        if not number.isdecimal() or int(number) >= len(_SMALL_NUMBERS):
            continue
        word = _SMALL_NUMBERS[int(number)]
        unit_pattern = _UNIT_EN.get(unit, re.escape(unit))
        output = re.sub(
            rf"\b{word}(?=\s+(?:{unit_pattern})(?![A-Za-z]))",
            number,
            output,
            flags=re.IGNORECASE,
        )
    return output


def _numeric_korean_words(source: str, output: str) -> str:
    """Normalize common local-model Korean cardinal words to source digits."""
    source_clean = _TECHNICAL.sub("", source)
    output_clean = _TECHNICAL.sub("", output)
    source_numbers = [
        int(value)
        for value in re.findall(r"(?<![\d.])(\d{1,2})(?![\d.])", source_clean)
    ]
    for number in source_numbers:
        output_count = Counter(
            re.findall(rf"(?<![\d.]){number}(?![\d.])", output_clean)
        )[str(number)]
        if output_count >= source_numbers.count(number):
            continue
        words = _KO_NUMBER_WORDS.get(number)
        english_words = _EN_NUMBER_WORDS.get(number, "")
        if words or english_words:
            pattern = "|".join(value for value in (words, english_words) if value)
            output = re.sub(pattern, str(number), output, flags=re.IGNORECASE)
            output_clean = _TECHNICAL.sub("", output)
    return output


def _translate_texts(
    texts: dict[str, str], ollama: Ollama, root: Path, target: Literal["en", "ko"]
) -> dict[str, str]:
    if len(texts) > 20 or any(len(text) > 1600 for text in texts.values()):
        raise AppError("번역할 답변이 한도를 초과했습니다.", 413)
    system = (root / "prompts" / f"translate.system.{target}.txt").read_text(encoding="utf-8")
    translated: dict[str, str] = {}
    for key, source in texts.items():
        if (
            not source.strip()
            or (target == "en" and not re.search(r"[가-힣]", source))
            or (target == "ko" and not _needs_korean_text(source))
        ):
            translated[key] = source
            continue
        masked, replacements = _protect_terms(source)
        # One bounded paragraph per call avoids truncating multi-claim answers.
        raw = ollama.generate(
            system,
            json.dumps(
                {"text": masked, "protected_terms": replacements}, ensure_ascii=False
            ),
            root / "schemas" / "translation.schema.json",
            output_tokens=1024,
        )
        output = _restore_terms(TranslatedText.model_validate_json(raw).text.strip(), replacements)
        output = _numeric_unit_words(source, output)
        if target == "ko":
            output = _numeric_korean_words(source, output)
        if not output:
            raise AppError("번역 결과가 비어 있습니다.", 422)
        validate_translation(source, output, target)
        translated[key] = output
    return translated


def translate_answer(
    result: AnswerResult,
    ollama: Ollama,
    root: Path,
    language: Literal["en", "ko"] = "en",
) -> AnswerResult:
    if result.translation is not None and result.translation.language == language:
        return result
    texts = answer_texts(result)
    if not texts:
        return result
    try:
        translated = _translate_texts(texts, ollama, root, language)
        return result.model_copy(
            update={
                "translation": AnswerTranslation(language=language, texts=translated),
                "translation_error": "",
            }
        )
    except (AppError, ValidationError):
        # Never publish a partly translated answer or replace its validated original.
        message = FAILURE_MESSAGE if language == "en" else KOREAN_FAILURE_MESSAGE
        return result.model_copy(update={"translation": None, "translation_error": message})


def needs_answer_translation(result: AnswerResult, language: Literal["en", "ko"]) -> bool:
    """Whether presentation text contains enough opposite-language prose to translate."""
    direct = [claim.text for claim in result.claims if claim.presentation == "direct"]
    # Raw conflict excerpts live in the original-source dialog. They must not
    # hide an already localized direct answer behind a translation requirement.
    texts = [*direct, *result.questions] if direct else list(answer_texts(result).values())
    if language == "en":
        return any(re.search(r"[가-힣]", text) for text in texts)
    return any(_needs_korean_text(text) for text in texts)


def _needs_korean_text(text: str) -> bool:
    """Translate English or English-heavy mixed prose into Korean."""
    if not text.strip() or not re.search(r"[A-Za-z]{3}", text):
        return False
    if not re.search(r"[가-힣]", text):
        return True
    words = re.findall(r"[A-Za-z]{3,}", text)
    hangul = re.findall(r"[가-힣]+", text)
    return len(words) >= 2 and len(words) >= len(hangul)
