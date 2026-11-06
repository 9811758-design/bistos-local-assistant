"""Concise local summaries of historical cases, never current-device diagnosis."""

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol

import jsonschema
from pydantic import ValidationError

from bistos.answer_explanation import Explanation, Grounding, check_support
from bistos.errors import AppError
from bistos.ollama import Ollama


class CaseSource(Protocol):
    @property
    def model(self) -> str: ...
    @property
    def symptom(self) -> str: ...
    @property
    def cause(self) -> str: ...
    @property
    def action(self) -> str: ...
    @property
    def result(self) -> str: ...


def _summary_action(action: str) -> str | None:
    """Project a labeled action to performed work before synthesis."""
    labeled = any(
        re.match(
            r"^\s*(?:처리\s*방안|처리\s*내역|plan|planned\s+action|actual(?:\s+action)?)\s*:",
            line,
            re.IGNORECASE,
        )
        for line in action.splitlines()
    )
    if not labeled:
        return action
    actual_lines: list[str] = []
    current = ""
    has_plan = False
    for line in action.splitlines():
        label, separator, value = line.partition(":")
        if separator:
            normalized = re.sub(r"\s+", "", label).casefold()
            if normalized in {"처리방안", "plan", "plannedaction"}:
                has_plan = True
                current = "plan"
            elif normalized in {"처리내역", "actualaction", "actual"}:
                current = "actual"
                if value.strip():
                    actual_lines.append(value.strip())
            else:
                current = ""
        elif current == "actual" and line.strip():
            actual_lines.append(line.strip())
    if has_plan and not actual_lines:
        return None
    return "\n".join(actual_lines) if has_plan else action


def summarize_cases(  # noqa: C901, PLR0911
    question: str,
    cases: Sequence[CaseSource],
    language: Literal["ko", "en"],
    ollama: Ollama,
    root: Path,
) -> str:
    """Return checked presentation text; original case data survives a failed summary."""
    if not cases:
        return ""
    passages: list[dict[str, str]] = []
    for index, case in enumerate(cases, 1):
        action = _summary_action(case.action)
        if action is None:
            continue
        passages.append(
            {
                "id": f"C{index}",
                "model": case.model,
                "symptom": case.symptom,
                "cause": case.cause,
                "action": action,
                "result": case.result,
            }
        )
    if not passages:
        return ""
    language_name = "한국어" if language == "ko" else "English"
    system = (root / "prompts/service-summary.system.txt").read_text(encoding="utf-8")
    system = system.replace("{{language}}", language_name)
    schema_path = root / "schemas/explanation.schema.json"
    try:
        request = json.dumps({"question": question, "cases": passages}, ensure_ascii=False)
        raw = ollama.generate(system, request, schema_path, output_tokens=768)
        jsonschema.validate(json.loads(raw), json.loads(schema_path.read_text(encoding="utf-8")))
        parsed = Explanation.model_validate_json(raw)
        if not parsed.claims:
            return ""
        claim = parsed.claims[0]
        # Internal grounding IDs belong to the source metadata, not the prose.
        clean_text = re.sub(r"\bC[1-5]\s+(?=사례)", "", claim.text)
        clean_text = re.sub(r"(?<=사례)\s+C[1-5]\b", "", clean_text)
        clean_text = re.sub(r"(?i)(?<=case)\s+C[1-5]\b", "", clean_text)
        clean_text = re.sub(r"(?<=사례)\s+(?=[은는이가을를의에])", "", clean_text)
        claim = claim.model_copy(update={"text": clean_text})
        by_id = {str(item["id"]): item for item in passages}
        if not set(claim.source_ids) <= by_id.keys():
            return ""
        support = json.dumps([by_id[key] for key in claim.source_ids], ensure_ascii=False)
        check_support(claim.text, support, {case.model for case in cases})
        if language == "ko" and not re.search(r"[가-힣]", claim.text):
            return ""
        if language == "en" and re.search(r"[가-힣]", claim.text):
            return ""
        if re.search(r"\d+\s*%|확률|보장|guarantee|probability", claim.text, re.IGNORECASE):
            return ""
        critic_system = (root / "prompts/service-grounding.system.txt").read_text(encoding="utf-8")
        checked = ollama.generate(
            critic_system,
            json.dumps(
                {
                    "question": question,
                    "cases": [by_id[key] for key in claim.source_ids],
                    "answer": claim.text,
                },
                ensure_ascii=False,
            ),
            root / "schemas/grounding.schema.json",
            output_tokens=256,
        )
        verdict = Grounding.model_validate_json(checked)
    except (AppError, ValidationError, json.JSONDecodeError, jsonschema.ValidationError):
        return ""
    return claim.text if verdict.supported and not verdict.issues else ""
