"""Question-scoped source requirements; never a source of answer facts.

Every prompt hint is an exact sentence from an already eligible cited page.
Patterns check coverage only. Existing numeric, condition and grounding checks
still decide whether the generated claims are supported.
"""

import re
from dataclasses import dataclass

from bistos.answer_selection import parameter_requirement_passages
from bistos.errors import AppError
from bistos.models import AnswerResult, Claim


@dataclass(frozen=True)
class Detail:
    label: str
    question: str
    source: str
    answer: tuple[str, ...]
    when_mentioned: bool = False
    parameter_scoped: bool = False


_BATTERY_TEST_QUESTION = (
    r"(?=.*(?:배터리|battery))(?=.*(?:성능|performance|runtime|사용\s*시간|지속\s*시간))"
    r"(?=.*(?:시험|확인|검사|test|check))"
)
_MAINTENANCE_SCOPE_QUESTION = (
    r"(?=.*(?:사용자|user|서비스|service))(?=.*(?:유지\s*관리|세척|내부|모터|maintenance|repair))"
    r"(?=.*(?:범위|누가|담당|까지만|scope|who|responsib))"
)


_DETAILS = (
    Detail(
        "환자 눈에 직접 조사 금지",
        r"초점|focal|focus|조명|빛|light",
        r"Do\s+not\s+glare\s+the\s+light\s+directly\s+to\s+the\s+patient.?s\s+eyes",
        (r"눈|eyes", r"직접|direct",
         (r"비추지\s*(?:마|말|않)|조사하지\s*(?:마|말|않)|비추면\s*안|조사하면\s*안|"
          r"직접.{0,15}(?:금지|안\s*됩)|do\s+not\s+(?:glare|shine)")),
    ),
    Detail(
        "용지 규격의 접지 형식",
        r"(?=.*(?:용지|종이|paper))(?=.*(?:규격|사양|크기|치수|spec|size|dimension))"  # noqa: ISC003
        + r"(?!.*(?:고장|안\s*나|안\s*돼|오류|걸림|교체|절차|방법|순서|troubleshoot|fault|error|"
        + r"jam|replace|procedure|steps|how))",
        r"Pack\s+Style\s*:\s*Z[-\s]*Fold",
        (r"Z[-\s]*Fold|Z\s*(?:형|자|접지)|지그재그",),
    ),
    Detail(
        "사용 중 서비스·유지관리 금지",
        _MAINTENANCE_SCOPE_QUESTION,
        r"Do\s+not\s+perform\s+servicing\s+or\s+maintenance\s+while\s+using",
        (r"사용\s*중|사용하는\s*동안|while\s+using",
         (r"(?:정비|유지\s*관리|서비스|servicing|maintenance).{0,30}"
          r"(?:금지|하지\s*(?:마|말|않)|하면\s*안|해서는\s*안|할\s*수\s*없)|"
          r"do\s+not\s+perform\s+(?:servicing|maintenance)")),
    ),
    Detail(
        "사용자 유지관리의 액세서리 세척·소독 범위",
        _MAINTENANCE_SCOPE_QUESTION,
        r"Maintenance\s+tasks.{0,90}limited\s+to\s+washing\s+and\s+sterilizing\s+accessories",
        (r"액세서리|accessor", r"세척|wash", r"소독|멸균|steriliz"),
    ),
    Detail(
        "배터리 성능 시험의 중단 없는 충전",
        _BATTERY_TEST_QUESTION,
        r"charge\s+battery.{0,90}uninterruptedly",
        (r"무중단|중단\s*없이|끊지\s*않고|연속(?:으로)?|uninterrupted|without\s+interruption",),
    ),
    Detail(
        "시험한 배터리 사용 시간이 현저히 짧을 때의 서비스 문의",
        _BATTERY_TEST_QUESTION,
        r"If\s+the\s+battery\s+operating\s+time\s+is\s+significantly\s+shorter.{0,180}"  # noqa: ISC003
        + r"contact.{0,50}service\s+personnel.{0,50}replacing",
        (r"현저|상당히|크게|significant", r"짧|short", r"서비스|service",
         r"연락|문의|contact", r"교체|replac"),
    ),
    Detail(
        "배터리 성능 시험의 환자 분리와 모든 감시 중단",
        _BATTERY_TEST_QUESTION,
        r"Disconnect\s+the\s+monitor\s+from\s+the\s+patient\s+and\s+stop\s+all\s+monitoring",
        (r"환자|patient", r"분리|disconnect", r"모든|전체|all",
         r"감시|모니터링|측정|monitoring|measurement", r"중단|정지|stop"),
    ),
    Detail(
        "배터리 시험의 DC 분리와 장비 종료까지 배터리 작동",
        _BATTERY_TEST_QUESTION,
        r"Disconnect\s+the\s+DC\s+power.{0,100}until\s+the\s+monitor\s+is\s+turned\s+off",
        (r"DC.{0,20}(?:분리|차단)|disconnect\s+the\s+DC",
         r"배터리|battery",
         r"꺼질\s*때까지|꺼지기까지|종료될\s*때까지|until.{0,40}(?:off|shutdown)"),
    ),
    Detail(
        "배터리 시험 시 배터리함을 열지 말라는 경고",
        _BATTERY_TEST_QUESTION,
        r"Do\s+not\s+open\s+the\s+battery\s+compartment",
        ((r"배터리\s*(?:함|구획|칸).{0,20}(?:열지|개방하지|열면\s*안|열어서는\s*안)|"
          r"do\s+not\s+open\s+the\s+battery\s+compartment"),),
    ),
    Detail(
        "세척한 부품의 깨끗하고 서늘한 보관 장소",
        r"(?=.*(?:보관|stor))(?=.*(?:부품|세척|유축|parts|wash|pump))",
        r"Store\s+dry\s+parts\s+in\s+a\s+clean,?\s+cool\s+place",
        (r"(?:깨끗|청결|clean).{0,25}(?:서늘|시원|cool)|"  # noqa: ISC003
         + r"(?:서늘|시원|cool).{0,25}(?:깨끗|청결|clean)",),
    ),
    Detail(
        "블루투스 연결 후 정보 교환과 메인 메뉴 표시",
        r"(?=.*(?:블루투스|Bluetooth))(?=.*(?:연결|connect))"  # noqa: ISC003
        + r"(?=.*(?:순서|절차|방법|어떻게|이후|후|order|steps|procedure|how|after))"
        + r"(?!.*(?:안\s*되|안\s*돼|실패|오류|cannot|can't|fail))",
        r"Once\s+connected.{0,180}sharing\s+the\s+mutual\s+information.{0,120}main\s+menu",
        (r"정보|information", r"교환|주고받|공유|shar|exchang",
         r"메인\s*메뉴|주\s*메뉴|main\s+menu"),
    ),
    Detail(
        "교체 배터리의 동일 모델과 규격",
        r"(?:배터리|battery).*(?:교체|바꿔|replace)|replace.*battery",
        r"batteries\s+of\s+(?:the\s+)?same\s+model\s+and\s+specification.{0,40}replaced",
        (r"(?:같|동일|same).{0,20}(?:모델|model)", r"규격|사양|specification"),
    ),
    Detail(
        "온도 프로브 교체의 호환 불일치 조건",
        r"(?:프로브|probe).{0,30}(?:교체|replace)|replace.{0,30}probe",
        r"만약\s*서로\s*다른\s*것이라면\s*프로브를\s*교체|"  # noqa: ISC003
        + r"if.{0,50}(?:incompatible|not\s+match).{0,60}replace",
        (r"호환.{0,15}(?:않|안|불가)|(?:적합|일치)하지\s*않|맞지\s*않|불일치|"  # noqa: ISC003
         + r"incompatible|not\s+match|프로브.{0,15}(?:기기|장치)(?:의\s*규격)?"
         + r"(?:이|가)?\s*(?:서로\s*)?(?:다르|다를|다른)",),
        when_mentioned=True,
    ),
    Detail(
        "설치 시 액세서리 케이블 연결 공간",
        r"(?=.*(?:벽|wall))(?=.*(?:간격|거리|공간|비워|띄|clearance|distance|space))",
        r"액세서리\s*케이블.{0,60}충분한\s*공간|sufficient\s+space.{0,60}accessor\w*\s+cables?",
        (r"케이블|cable", r"공간|space"),
    ),
    Detail(
        "SpO2 파형 설정의 진입 영역",
        r"(?=.*(?:SpO2|산소포화도|pleth))(?=.*(?:파형|wave))(?=.*(?:어디|메뉴|경로|where|menu))",
        r"Select\s+SpO2\s+parameter\s+area.{0,100}SpO2\s+Setup|"  # noqa: ISC003
        + r"SpO2\s*파라미터\s*영역.{0,100}SpO2\s+Setup",
        (r"(?:파라미터|파형|parameter|wave).{0,12}(?:영역|area)", r"SpO2\s*Setup"),
        parameter_scoped=True,
    ),
    Detail(
        "파형 속도 설정 항목",
        r"(?=.*(?:SpO2|산소포화도|pleth))(?=.*(?:파형|wave))(?=.*(?:어디|메뉴|경로|where|menu))",
        r"Wave\s+Speed.{0,60}(?:파형\s*속도|wave\s*speed)|"  # noqa: ISC003
        + r"Select.{0,15}Wave\s+Speed",
        (r"Wave\s*Speed|파형\s*속도\s*(?:항목|메뉴)",),
        parameter_scoped=True,
    ),
    Detail(
        "질문한 손가락 접촉 시간",
        r"(?:엄지|손가락|thumb|finger).*(?:얼마나|시간|how\s+long|duration)",
        r"\d+\s*[~\u2013-]\s*\d+\s*초.{0,100}엄지|thumb.{0,100}\d+\s*(?:to|[-\u2013])\s*\d+\s*seconds",
        (r"\d+\s*(?:~|\u2013|-|에서|부터|to)\s*\d+\s*(?:초|seconds)",),
    ),
    Detail(
        "손가락 접촉 면적이 작은 경우의 측정 한계",
        r"(?:엄지|손가락|thumb|finger).*(?:어떻게|접촉|how|contact)",
        r"접촉면(?:적)?이\s*적을\s*경우.{0,40}부정확|contact\s+area.{0,60}inaccurate",
        (r"접촉\s*면(?:적)?|contact\s+area", r"적|작|small", r"부정확|inaccurate"),
    ),
    Detail(
        "퇴원 처리의 확인 단계",
        r"퇴원|discharge\s+patient",
        r"Discharge\s+Patient.{0,80}Warning.{0,50}OK",
        (r"경고|Warning", r"확인|(?<![A-Za-z])OK(?![A-Za-z])"),
    ),
    Detail(
        "필요한 경우에만 환자 기록 삭제",
        r"퇴원|discharge\s+patient",
        r"discharge\s+the\s+patient\s+only\s+when\s+needed|"  # noqa: ISC003
        + r"필요한\s*경우에만.{0,30}퇴원|퇴원.{0,30}필요한\s*경우에만",
        (r"필요.{0,15}(?:만|경우|때)|only\s+when\s+needed",),
    ),
    Detail(
        "부적절한 조율기 설정의 PVC 계수 영향",
        r"조율기|pace\s*maker|pacemaker",
        r"including\s+PVCs?\s+count",
        (r"PVC.{0,20}(?:계수|횟수|개수|카운트|count)|(?:계수|카운트).{0,10}PVC",),
    ),
    Detail(
        "Start NIBP 메뉴 선택 후 측정 모드 선택",
        r"(?:NIBP|혈압).*(?:시작|정지|원격|start|stop)|(?:원격|remote).*NIBP",
        r"choose\s+measur\w*\s+mode\s+for\s+NIBP",
        (r"측정\s*모드|measur\w*\s+mode", r"선택|choose|select"),
    ),
    Detail(
        "강제 전원 차단의 정상 종료 실패 조건",
        r"강제|전원.{0,15}(?:빼|뽑|분리)|(?:unplug|disconnect).{0,15}power|forced?\s+shutdown",
        r"if.{0,90}not\s+turned\s+off\s+properly.{0,100}disconnect\s+the\s+power",
        (r"정상.{0,20}(?:종료|꺼)(?![^.!?]{0,20}(?:따르|준수|지키)).{0,12}(?:않|못|안|실패)|"  # noqa: ISC003
         + r"정상\s*종료(?:에|가)?\s*실패|(?:종료|꺼지)(?:가|되지|지)?\s*(?:않|못)",
         r"경우|때|실패하면|if|when"),
        when_mentioned=True,
    ),
    Detail(
        "보관 전 배터리 충전",
        r"(?:배터리|battery).*(?:어떻게.*보관|보관.*방법|보관.*어떻게)|"  # noqa: ISC003
        + r"(?:how.*stor|stor.*how).*(?:battery|batteries)",
        r"charg\w*\s+(?:the\s+)?battery.{0,90}\bstor\w*|충전.{0,70}보관",
        (r"충전|charg",),
    ),
    Detail(
        "서비스 담당자 자격과 제조사 승인",
        r"(?:사용자|user|서비스|service).*(?:범위|누가|담당|scope|who|responsib)|"
        + _BATTERY_TEST_QUESTION,
        r"qualified\s+service\s+personnel\s+authorized\s+by\s+the\s+manufacturer",
        (r"자격|qualified", r"제조사|제조업체|manufacturer",
         r"승인|인가|authoriz|권한을?\s*받(?:은|아|았|는)"),
    ),
    Detail(
        "질문한 시리얼 포트 설정",
        r"(?:serial|시리얼|직렬).*(?:COM|com)",
        r"(?:serial|시리얼).{0,160}COM\s*(?:포\s*트|port)",
        (r"시리얼|serial|직렬", r"COM\s*(?:포트|port)?"),
    ),
    Detail(
        "질문한 통신 베드 번호",
        r"(?:serial|시리얼|COM).*(?:침대|베드|bed)|(?:침대|베드|bed).*(?:serial|시리얼|COM)",
        r"(?:select|input).{0,30}\[?bed\s*number|\[?베드번호\]?.{0,15}선택",
        (r"베드|침대|bed",),
    ),
    Detail(
        "유두 측정에서 유륜 제외",
        r"(?:깔때기|funnel|flange).*(?:크기|선택|지름|사이즈|size|select)",
        r"not\s+to\s+include\s+areola|유륜.{0,25}(?:포함하지|제외)",
        (r"유륜|areola", r"제외|포함하지|포함.{0,6}않|not\s+include|exclud"),
    ),
    Detail(
        "맥동에 따른 흡광 변화",
        r"(?:Sp\s*O\s*2|산소포화도).*(?:원리|계산|principle|calculat)|"  # noqa: ISC003
        + r"^(?:제품|기기)\s*(?:작동|동작)?\s*원리",
        r"(?:absorbed\s+during\s+pulsating|흡수.{0,30}맥동|맥동.{0,35}흡수)",
        (r"맥동|박동|pulsat", r"흡광|흡수|absorb"),
    ),
    Detail(
        "지정 ECG 케이블과 전극",
        r"(?:ECG|심전도).*(?:연결|요구|주의|connect|require)",
        r"ECG\s+cable\s+and\s+ECG\s+electrodes\s+specified|지정.{0,30}(?:케이블|전극)",
        (r"지정|명시|specified", r"케이블|cable", r"전극|electrode"),
    ),
    Detail(
        "타전도체 및 접지 비접촉",
        r"(?:ECG|심전도).*(?:연결|요구|주의|connect|require)",
        r"(?:other\s+conductors?.{0,80}ground|ground.{0,80}other\s+conductors?|다른\s*전도체.{0,80}접지)",
        (
            r"전도체|도체|conduct",
            r"접지|ground",
            r"접촉.{0,25}(?:않|금지|안\s*되|마)|닿지|not\s+contact",
        ),
    ),
    Detail(
        "ECG 표시 항목",
        r"(?:ECG|심전도).*(?:표시|display)",
        r"heart\s+rate\s*\(HR\).{0,100}ST.{0,100}arrhythmia",
        (r"심박|\bHR\b|heart\s*rate", r"\bST\b", r"부정맥|arrhythmia"),
    ),
    Detail(
        "측정 전 적합한 SpO2 센서 선택",
        r"(?:SpO2|산소포화도).*(?:시작|순서|절차|start|procedure)",
        r"(?:적절한\s*SpO2\s*센서를\s*선택|select\s+(?:an?\s+)?appropriate\s+SpO2\s+sensor)",
        (r"적절|적합|appropriate|suitable", r"센서|sensor", r"선택|select"),
    ),
    Detail(
        "기기·프로브·케이블 적합성",
        r"(?:SpO2|산소포화도).*(?:시작|순서|절차|start|procedure)",
        r"기기와\s*프로브\s*및\s*케이블의\s*적합성|compatib\w*.{0,60}(?:probe|cable)",
        (
            r"기기|장치|device|monitor",
            r"프로브|탐촉자|probe",
            r"케이블|cable",
            r"적합|호환|compatib",
        ),
    ),
    Detail(
        "커프와 심장 높이",
        r"(?:NIBP|커프|cuff).*(?:측정|조건|measurement|condition)",
        r"(?:cuff.{0,150}right\s+atrium|right\s+atrium.{0,150}cuff|커프.{0,100}우심방)",
        (r"커프|cuff", r"우심방|심장|right\s+atrium|heart", r"높이|수준|수평|level|height"),
    ),
    Detail(
        "측정 중 말하지 않기",
        r"(?:NIBP|커프|cuff).*(?:측정|조건|measurement|condition)",
        r"not\s+talk.{0,40}measur|not\s+to\s+talk|no\s+talking|말하지\s*않|말하지\s*마",
        (r"말하지|말.{0,12}않|대화.{0,12}(?:금지|않)|not\s+talk",),
    ),
    Detail(
        "입력 신호 한계",
        r"입력\s*신호|input\s*signal",
        r"input\s+signal\s+amplitude.{0,160}minimum",
        (r"신호|signal", r"진폭|amplitude", r"최소|minimum", r"부정확|정확하지|inaccura"),
    ),
)


def _sentences(text: str) -> list[str]:
    flat = " ".join(text.replace("\\n", "\n").split())
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|[⚫➢✓]", flat) if part.strip()]


def required_details(
    question: str, source: str, answer: str = "", *, parameter_scope: bool = False,
) -> list[tuple[Detail, str]]:
    result: list[tuple[Detail, str]] = []
    parameter_passages = (
        parameter_requirement_passages(question, source) if parameter_scope else None
    )
    for detail in _DETAILS:
        requested = re.search(detail.question, question, re.IGNORECASE)
        mentioned = detail.when_mentioned and re.search(detail.question, answer, re.IGNORECASE)
        if not (requested or mentioned):
            continue
        candidates = (
            parameter_passages if detail.parameter_scoped and parameter_passages is not None
            else [source]
        )
        sentences = (sentence for candidate in candidates for sentence in _sentences(candidate))
        for sentence in sentences:
            if len(sentence) <= 900 and re.search(detail.source, sentence, re.IGNORECASE):
                result.append((detail, sentence))
                break
    return result


def required_detail_phrases(
    question: str, source: str, *, parameter_scope: bool = False,
) -> list[str]:
    phrases = [
        text for _, text in required_details(question, source, parameter_scope=parameter_scope)
    ]
    contact = _leakage_contact_sentence(source)
    if contact and re.search(r"누액|액.{0,8}새|leak", question, re.IGNORECASE):
        phrases.append(contact)
    return phrases


def _leakage_contact_sentence(source: str) -> str:
    for sentence in _sentences(source):
        if re.search(
            r"if.{0,35}(?:skin|cloth).{0,65}contact.{0,35}leaked\s+liquid"  # noqa: ISC003
            + r".{0,50}(?:cleanse|wash).{0,35}water",
            sentence, re.IGNORECASE,
        ):
            return sentence
    return ""


def _check_leakage_washing_condition(text: str, source: str) -> None:
    if not _leakage_contact_sentence(source):
        return
    previous = ""
    for clause in re.split(r"[.!?;\n]", text):
        prior = previous
        previous = clause
        if not re.search(r"세척|씻|헹|wash|cleanse|rinse", clause, re.IGNORECASE):
            continue
        if not re.search(r"물|water", clause, re.IGNORECASE):
            continue
        broadened = re.search(
            r"(?:액(?:이|가)?\s*새|누액.{0,8}(?:생기|발생하|있))거나|"  # noqa: ISC003
            + r"(?:leak(?:age)?|leaked\s+liquid).{0,20}\bor\b.{0,30}(?:skin|cloth)",
            clause, re.IGNORECASE,
        )
        contact = re.search(
            r"(?:피부|옷|눈).{0,25}(?:닿|묻|튀|들어|접촉)|"  # noqa: ISC003
            + r"(?:skin|cloth|eyes?).{0,35}(?:contact|touch|splash)|"
            + r"(?:contact|touch|splash).{0,35}(?:skin|cloth|eyes?)",
            clause, re.IGNORECASE,
        )
        eye_continuation = (
            re.search(r"눈.{0,25}(?:묻|튀|들어|접촉).{0,35}닦지", prior)
            and re.match(r"\s*(?:깨끗한\s*)?물로\s*(?:헹|씻|세척)", clause)
        )
        device_target = re.search(
            r"(?:배터리|건전지|기기|장치)(?:를|는|도)?\s*(?:깨끗한\s*)?물로\s*"  # noqa: ISC003
            + r"(?:씻|세척|헹)|(?:wash|cleanse|rinse)\s+(?:the\s+)?(?:battery|device)",
            clause, re.IGNORECASE,
        )
        if broadened or device_target or not (contact or eye_continuation):
            raise AppError("누액을 물로 씻는 조치의 피부·옷·눈 접촉 조건이 달라졌습니다.", 422)


def check_required_details(question: str, text: str, source: str) -> None:
    _check_leakage_washing_condition(text, source)
    missing = [
        detail.label
        for detail, _ in required_details(question, source, text)
        if not all(re.search(pattern, text, re.IGNORECASE) for pattern in detail.answer)
    ]
    if missing:
        raise AppError("질문에 필요한 원문 조건이 빠졌습니다: " + ", ".join(missing), 422)


def complete_required_evidence(question: str, selected: AnswerResult) -> AnswerResult:
    """Recover omitted requirements from the same selected document only.

    No new pages or documents are retrieved here. The source list has already
    passed permission, publication, product and version gates. Exact page
    sentences carry their own evidence IDs through the ordinary validation path.
    """
    if selected.status != "answered" or not selected.claims:
        return selected
    source_by_id = {source.evidence_id: source for source in selected.evidence}
    first = next(
        (
            source_by_id[citation]
            for claim in selected.claims
            for citation in claim.citation_ids
            if citation in source_by_id
        ),
        None,
    )
    if first is None:
        return selected
    claims = list(selected.claims)
    existing = " ".join(" ".join(claim.text.split()) for claim in claims)
    for source in selected.evidence:
        if source.document_id != first.document_id:
            continue
        # A shared document does not make ECG/RESP/SpO2 settings interchangeable.
        # Reuse its section hierarchy only for parameter-menu requirements;
        # independent safety requirements in a mixed question remain eligible.
        additions = [
            text for text in required_detail_phrases(question, source.text, parameter_scope=True)
            if text not in existing
        ]
        if not additions:
            continue
        # Reuse a source ID when possible; do not invent uncited evidence.
        index = next(
            (i for i, claim in enumerate(claims) if claim.citation_ids == [source.evidence_id]),
            None,
        )
        extra = "\n".join(dict.fromkeys(additions))
        if index is not None and len(claims[index].text) + len(extra) + 2 <= 2400:
            claims[index] = claims[index].model_copy(
                update={"text": claims[index].text + "\n\n" + extra}
            )
        elif len(claims) < 4 and len(extra) <= 2400:
            number = len(claims) + 1
            while f"C{number}" in {claim.id for claim in claims}:
                number += 1
            claims.append(
                Claim(id=f"C{number}", text=extra, citation_ids=[source.evidence_id])
            )
        else:
            continue
        existing += " " + " ".join(extra.split())
    return selected.model_copy(update={"claims": claims})
