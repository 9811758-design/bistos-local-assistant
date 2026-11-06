"""Translate only the search query using the pinned local model."""

import json
import re
from pathlib import Path

from pydantic import Field, ValidationError

from bistos.errors import AppError
from bistos.models import Model
from bistos.ollama import Ollama
from bistos.question_intent import full_charge_question


class RetrievalTranslation(Model):
    english_query: str = Field(min_length=1, max_length=1000)


def explicit_quantity_query(question: str) -> str:
    """Expose native Korean counts to the existing numeric fidelity check.

    Only a complete count immediately preceding a time unit is normalized.
    The original question remains the answer input and is kept in retrieval.
    """
    counts = {"한": 1, "두": 2, "세": 3, "석": 3, "네": 4, "넉": 4,
              "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}
    pattern = r"(?<![가-힣])(" + "|".join(counts) + r")\s*(달|개월|시간|분|초)(?![가-힣])"
    return re.sub(pattern, lambda m: f"{counts[m[1]]}{m[2]}", question)


def retrieval_query(question: str, ollama: Ollama, root: Path) -> str:
    """Keep the user's question for answering; add validated English retrieval terms."""
    if not re.search(r"[가-힣]", question):
        return question
    translation_question = explicit_quantity_query(question)
    payload: dict[str, object] = {"question": translation_question}
    terminology: dict[str, str] = {}
    if "유축" in question:
        terminology.update({"유축기": "breast pump", "유축": "expressing breast milk"})
    if "완충" in question and full_charge_question(question):
        terminology["완충"] = "fully charged battery"
    if terminology:
        payload["terminology"] = terminology
    try:
        translated = RetrievalTranslation.model_validate_json(
            ollama.generate(
                (root / "prompts" / "query-translation.system.txt").read_text(encoding="utf-8"),
                json.dumps(payload, ensure_ascii=False),
                root / "schemas" / "query-translation.schema.json",
            ),
        ).english_query.strip()
    except (AppError, ValidationError, OSError):
        return question
    # Query expansion may improve recall but must not invent model numbers or measurements.
    numbers = re.findall(r"\d+(?:\.\d+)*", translation_question)
    if sorted(numbers) != sorted(re.findall(r"\d+(?:\.\d+)*", translated)):
        return question
    models = re.findall(r"\b[A-Za-z]{1,12}[- ]?\d+[A-Za-z0-9-]*\b", question)
    if any(model.casefold() not in translated.casefold() for model in models):
        return question
    if (not re.search(r"[A-Za-z]", translated) or re.search(r"[가-힣]", translated)
            or ("유축" in question and not re.search(r"breast|milk", translated, re.IGNORECASE))
            or ("완충" in question and full_charge_question(question)
                and not re.search(r"charg", translated, re.IGNORECASE))):
        return question
    return question + "\n" + translated
