import json
from pathlib import Path
from typing import Final, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaError
from pydantic import ValidationError

from bistos.errors import AppError
from bistos.models import AnswerResult, Claim, Conflict, Evidence, Model

AnswerStatus = Literal[
    "answered",
    "partial_answer",
    "clarification_required",
    "evidence_missing",
    "conflict",
    "visual_review_required",
    "service_required",
]

STATUS_MESSAGES: Final = {
    "answered": "로컬 AI가 찾은 매뉴얼 근거입니다. 원문과 실제 장비의 적용 조건을 확인하세요.",
    "partial_answer": (
        "질문의 일부 항목만 매뉴얼 근거를 확인했습니다. 나머지는 추측하지 않았습니다."
    ),
    "clarification_required": "제품 또는 적용 조건을 먼저 확인해 주세요.",
    "evidence_missing": "현재 게시된 자료만으로 답변을 확정할 수 없습니다.",
    "conflict": "매뉴얼의 설명이 다릅니다. 원문과 실제 장비 적용 값을 담당자에게 확인하세요.",
    "visual_review_required": "도면·표의 배치 검토가 필요하여 답변을 보류했습니다.",
    "service_required": "서비스 담당자의 확인이 필요한 요청입니다.",
}


class ModelAnswer(Model):
    status: AnswerStatus
    claims: list[Claim]
    conflicts: list[Conflict]
    questions: list[str]
    limitation_codes: list[str]


def validate_answer(raw: str, evidence: list[Evidence], root: Path) -> AnswerResult:
    """Reject malformed outputs and citations not bound to this generation request."""
    try:
        parsed = ModelAnswer.model_validate_json(raw)
        schema = json.loads((root / "schemas" / "answer.schema.json").read_text(encoding="utf-8"))
        # Validate model-supplied fields, not server-owned defaults such as
        # presentation. The generator cannot mark its own output as direct.
        Draft202012Validator(schema).validate(json.loads(raw))
    except (ValidationError, SchemaError, json.JSONDecodeError) as error:
        raise AppError("AI 답변 형식 검사를 통과하지 못했습니다.", 422) from error
    claim_ids = [claim.id for claim in parsed.claims]
    if len(claim_ids) != len(set(claim_ids)):
        raise AppError("AI 답변에 중복 주장 ID가 있습니다.", 422)
    allowed = {item.evidence_id for item in evidence}
    groups = [claim.citation_ids for claim in parsed.claims] + [
        variant.citation_ids for conflict in parsed.conflicts for variant in conflict.variants
    ]
    if any(not citations or not set(citations) <= allowed for citations in groups):
        raise AppError("AI 답변의 인용이 이번 요청 근거에 속하지 않습니다.", 422)
    if parsed.status == "answered" and parsed.limitation_codes:
        raise AppError("AI 답변 상태와 제한 코드가 모순됩니다.", 422)
    return AnswerResult(
        **parsed.model_dump(),
        mode="ai",
        evidence=evidence,
        message=STATUS_MESSAGES[parsed.status],
    )
