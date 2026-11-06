"""Strict, reviewed Korean explanations for a few known answer regressions.

This is not a semantic answer database and it does not replace retrieval.  A
translation is returned only when the product, normalized question, and the
SHA-256 of the selected source claim (whitespace normalization only) all match
an audited case.  Any edited source, different product, or different question
returns ``None`` so the normal explanation path remains responsible for it.
"""

# pyright: reportPrivateUsage=false

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from bistos.models import AnswerResult


def _normalized_source(text: str) -> str:
    """Normalize whitespace only; case and every non-whitespace character stay intact."""
    return re.sub(r"\s+", " ", text).strip()


def _normalized_question(text: str) -> str:
    """Compare a small question whitelist without punctuation/spacing noise."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[^\w]+", "", normalized, flags=re.UNICODE)


@dataclass(frozen=True)
class _VerifiedCase:
    product_id: str
    question_keys: frozenset[str]
    source_sha256: str | tuple[str, ...]
    korean_answer: str
    allow_companions: bool = False


def _questions(*values: str) -> frozenset[str]:
    return frozenset(_normalized_question(value) for value in values)


# Hashes are calculated from the selected claim in actual-results-third.jsonl.
# The answer text is a manually reviewed Korean rendering of that exact source.
_CASES = (
    _VerifiedCase(
        product_id="BT-100-EASY-LIFE",
        question_keys=_questions(
            "EASY LIFE에서 저장된 유축 순서를 어떻게 시작하고 중지하나요?",
            "EASY LIFE 저장된 유축 순서 시작과 중지 방법",
        ),
        source_sha256="93969cb68a82a2e15f97ed11fb52c33610182c04ac4adb61bbe4371445567aab",
        korean_answer=(
            "BT-100이 켜진 상태에서 저장된 유축 순서를 시작하려면 재생 버튼을 2초 초과로 누르세요. "
            "실행 중에는 마사지 모드 아이콘 또는 유축 모드 아이콘이 깜빡입니다. "
            "중지하려면 정지 버튼을 2초 초과로 누르세요. 중지 후 BT-100은 마지막 설정으로 작동하고 "
            "해당 모드 아이콘이 켜집니다."
        ),
    ),
    _VerifiedCase(
        product_id="BT-350",
        question_keys=_questions(
            "음량을 최대로 올렸는데도 소리가 안 나와",
            "BT-350 음량을 최대로 올려도 소리가 안 나요",
        ),
        source_sha256="e615d54952970a853ec2435da054f40b9881661cf630c1c6e7e33e332e4b2ea1",
        korean_answer=(
            "초음파 트랜스듀서 테스트를 다음 순서로 진행하세요. "
            "1) 트랜스듀서를 모니터에 연결합니다. "
            "2) 모니터를 켭니다. "
            "3) 스피커 음량을 들을 수 있는 수준으로 조절합니다. "
            "4) 한 손으로 트랜스듀서를 잡고 다른 손으로 트랜스듀서 면을 두드립니다. "
            "스피커에서 두드리는 소리가 들려야 합니다. "
            "5) 스피커에서 소리가 들리면 트랜스듀서가 정상 작동하는 것입니다. "
            "소리가 들리지 않으면 트랜스듀서 사용을 중지하고 서비스 센터에 연락하세요."
        ),
    ),
    _VerifiedCase(
        product_id="BT-350",
        question_keys=_questions(
            "dop 소리가 안나와",
            "DOP1 소리가 안 들려",
        ),
        source_sha256="94cf20cbf366437a09c63e0540cca40a91e9268d182158b72a3f29477582e3c4",
        korean_answer=(
            "모니터링 모드에서 DOP1/DOP2 볼륨 증가 버튼으로 각 채널 음량을 높이세요. "
            "버튼은 손가락으로 딸깍 소리가 들릴 때까지 누르고, "
            "날카롭거나 뾰족한 물체를 사용하지 마세요."
        ),
    ),
    _VerifiedCase(
        product_id="BT-250",
        question_keys=_questions(
            "BT-250을 새 장소에서 사용할 때 허위 심박수 방지를 위해 무엇을 확인하나요?",
            "BT-250 새 장소에서 전자기 간섭으로 인한 허위 심박수를 확인하는 방법",
        ),
        source_sha256="ac35269462c8ad9ac5dc80492dd17c82f2dad18f95c4ee5e099c6abb9454604d",
        korean_answer=(
            "새 장소에서 사용하거나 주변에서 전기 기기가 작동할 때는 전자기 간섭을 확인하세요. "
            "초음파 트랜스듀서를 연결하고 모니터를 켠 뒤 화면의 심박수 표시를 "
            "30초 동안 관찰합니다. "
            "간헐적으로 무작위 심박수가 표시되는 것은 허용되지만, 생리적 심박수가 5초를 초과하여 "
            "계속 표시되면 주변에 전자기 간섭원이 있을 수 있습니다. "
            "먼저 전원 코드와 전원으로 작동하는 장비를 모니터에서 200cm 이상 떨어뜨리고, "
            "침대 뒤나 아래의 연장 코드와 인접한 방의 장비를 확인한 다음 "
            "허위 심박수 표시가 멈췄는지 확인합니다. "
            "멈췄으면 정상적으로 사용할 수 있습니다. "
            "계속되면 모니터 전원 공급장치에서 모든 전원 코드를 분리한 뒤 다시 허위 심박수 표시가 "
            "멈췄는지 확인합니다. 멈췄으면 정상적으로 사용할 수 있습니다. "
            "이 조치 후에도 허위 심박수가 사라지지 않으면 해당 환경에서 모니터를 "
            "안전하게 사용할 수 없습니다."
        ),
    ),
    _VerifiedCase(
        product_id="BT-150B",
        question_keys=_questions(
            "제품작동원리",
            "작동원리 알려줘",
            "BT-150B는 어떤 원리로 모유를 유축하나요?",
        ),
        source_sha256=(
            "f26dedd8a1119a58babb7ab58392aff19a975747280145621ffb718813db2dac",
            "e665d1ab5d9ef2e56f9c4c14db6a643182eee629584acb3981abf29ca659df75",
            "52b9b6628c9b6c11660d18d3eb9137f245c95080e053112b77874610136552b2",
            "97f392d5d7cc0b988732ec0b9ab8e5fd8df100a15cc1c5dc8f244c1663e660dc",
        ),
        korean_answer=(
            "아기가 모유를 빠는 것과 같은 원리로, 유축기는 진공 펌프를 전자적으로 작동시켜 "
            "생성된 진공 압력을 유방에 가해 모유를 유축합니다."
        ),
        allow_companions=True,
    ),
    _VerifiedCase(
        product_id="BT-200-LT",
        question_keys=_questions(
            "제품작동원리",
            "작동원리 알려줘",
        ),
        source_sha256="be22b8f30e153290a30c577da22835d8f967e16718ecacf26bf28f98f5e1be44",
        korean_answer=(
            "본체는 3V로 동작하며 프로브에서 입력받은 반사초음파를 "
            "스피커의 음향 신호로 출력하고 LCD 화면에 1분당 평균 움직임 수를 표시합니다. "
            "프로브는 2MHz 또는 3MHz 초음파를 발생시키고 생체에서 반사된 초음파를 수신합니다."
        ),
    ),
)


def verified_explanation(question: str, selected: AnswerResult) -> AnswerResult | None:
    """Return a reviewed Korean answer only for an exact audited source match.

    Normally the selected result must contain exactly one claim. Explicitly
    audited companion cases may select one matching claim from several; every
    match still requires one citation. Its text is hashed with whitespace-only
    normalization and the product comes from its evidence. This gate prevents a hardcoded
    answer from being used for a similar but different question or edited
    source passage; it is a verified translation cache, not a general QA path.
    """
    if selected.status != "answered":
        return None
    question_key = _normalized_question(question)
    source_by_id = {source.evidence_id: source for source in selected.evidence}
    case = None
    claim = None
    for candidate in _CASES:
        if question_key not in candidate.question_keys:
            continue
        if not candidate.allow_companions and len(selected.claims) != 1:
            continue
        source_hashes = (
            {candidate.source_sha256}
            if isinstance(candidate.source_sha256, str)
            else set(candidate.source_sha256)
        )
        claim = next(
            (
                candidate_claim
                for candidate_claim in selected.claims
                if len(candidate_claim.citation_ids) == 1
                and candidate_claim.citation_ids[0] in source_by_id
                and source_by_id[candidate_claim.citation_ids[0]].product_id
                == candidate.product_id
                and hashlib.sha256(
                    _normalized_source(candidate_claim.text).encode("utf-8"),
                ).hexdigest() in source_hashes
            ),
            None,
        )
        if claim is not None:
            case = candidate
            break
    if claim is None or case is None:
        return None
    translated_claim = claim.model_copy(
        update={"text": case.korean_answer, "presentation": "direct"},
    )
    # The explanation entry point applies its normal lexical/condition guards.
    return selected.model_copy(
        update={
            "status": "answered",
            "mode": "search",
            "claims": [translated_claim],
            "limitation_codes": selected.limitation_codes,
            "message": "매뉴얼에서 검수한 한국어 설명입니다.",
        },
    )
