"""Present explicit user-manual audio controls without diagnosing a fault."""

import re

from bistos.models import AnswerResult, Claim, Evidence


def audio_support(
    question: str, sources: list[Evidence], *, subject: str = "",
) -> AnswerResult | None:
    compact = re.sub(r"\s+", "", question).casefold()
    if not re.search(
        r"(?<![a-z0-9])dop(?:pler)?[12]?(?![a-z0-9])|도플러",
        question + " " + subject, re.IGNORECASE,
    ):
        return None
    if not any(
        term in compact
        for term in (
            "소리가안",
            "소리안",
            "소리가나지",
            "소리가들리지",
            "안들려",
            "안들림",
            "들리지않",
            "무음",
            "소리가너무작",
            "소리가작",
            "음량이너무작",
            "효과가없",
            "nosound",
            "noaudio",
            "soundnotcoming",
            "audionotworking",
            "cannothear",
            "quiet",
            "lowvolume",
        )
    ) or re.search(
        r"아니라|정상(?:이고|인데|임|입니다)|문제\s*없|경보|알람|\balarm\b|\bopen\b",
        question, re.IGNORECASE,
    ):
        return None
    # Do not repeat the same volume-up instruction after the user explicitly
    # says it was already tried and the symptom remains. A follow-up needs a
    # documented next check; inventing a repair step here would be misleading.
    persistent_audio_failure = (
        r"(?:안\s*들|소리가\s*안|소리가\s*나지|효과가\s*없|무음|"
        r"no\s+(?:sound|audio)|cannot\s*hear|sound\s+not\s+coming)"
    )
    volume_action = (
        r"(?:음량|볼륨).{0,18}(?:올렸|높였|올려도|높여도|설정했|맞췄|증가시켰|눌렀|눌러도)|"
        r"(?:음량|볼륨).{0,8}(?:최대|최고)(?:에서|인데|인\s*상태)|"
        r"\bpressed\b.{0,25}volume|volume.{0,25}\bpressed\b|"
        r"volume.{0,16}(?:turned\s+up|increased|raised|at\s+max)|"
        r"(?:set|turned\s+up|increased|raised).{0,16}volume|"
        r"volume.{0,16}(?:max|high).{0,12}(?:still|but)"
    )
    not_tried_pattern = (
        r"(?:음량|볼륨)[^.!?,;\n]{0,18}(?:(?:안|못)\s*(?:올렸|높였|눌렀|올려\s*봤|눌러\s*봤)"
        r"|(?:올리|높이|누르)지\s*않았)|"
        r"(?:not|haven't|didn't)\s+(?:increase[ds]?|raise[ds]?|press(?:ed)?)\s+(?:the\s+)?volume"
    )
    not_tried = re.search(not_tried_pattern, question, re.IGNORECASE)
    already_tried = bool(
        not not_tried
        and re.search(volume_action, question, re.IGNORECASE)
        and re.search(persistent_audio_failure, question, re.IGNORECASE)
    )
    # A documented troubleshooting procedure must still reach the normal
    # selection/verification path after the first check has failed.
    if already_tried and any(re.search(
        r"소리가\s*들리지\s*않으면|프로브\s*센서\s*시험|troubleshoot",
        source.text, re.IGNORECASE,
    ) for source in sources):
        return None
    requested = re.findall(r"\bdop\s*([12])(?![a-z0-9])", question, re.IGNORECASE)
    resolved = set(re.findall(
        r"\bdop\s*([12]).{0,12}(?:해결|정상|괜찮|소리가\s*나)",
        question, re.IGNORECASE,
    ))
    for source in sources:
        if source.kind not in {"user", "quick"}:
            continue
        text = " ".join(source.text.split())
        # These are complete, explicit button/function statements. Do not
        # infer a diagram, a numbered symbol, a repair step or a missing label.
        channels = set(
            re.findall(
                r"심박음량\s*증가\s*버튼\s+DOP([12])의\s*심박음량을\s*크게\s*한다[.]",
                text,
                re.IGNORECASE,
            )
        )
        wanted = set(requested) if requested else {"1", "2"}
        wanted -= resolved
        if not wanted:
            continue
        if not wanted <= channels:
            continue
        if already_tried:
            return AnswerResult(
                status="evidence_missing",
                mode="search",
                evidence=list(sources),
                limitation_codes=["no_documented_next_step"],
                message=(
                    "음량 증가 버튼을 이미 눌렀는데도 소리가 나지 않는 상태군요. "
                    "이번에 검색한 근거에서는 다음 점검 방법을 확인하지 못했습니다."
                ),
            )
        labels = "·".join(f"DOP{channel}" for channel in sorted(wanted))
        return AnswerResult(
            status="answered",
            mode="search",
            evidence=[source],
            claims=[
                Claim(
                    id="C1",
                    presentation="direct",
                    citation_ids=[source.evidence_id],
                    text=(f"{labels}의 심박음량 증가 버튼으로 "
                          "해당 채널의 음량을 높여 확인해 보세요."),
                )
            ],
            message="질문에 해당하는 음량 조작을 매뉴얼에서 확인했습니다.",
        )
    return None
