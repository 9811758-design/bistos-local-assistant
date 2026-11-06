"""Persistent owner-scoped guides with reported or hypothetical outcomes."""

import hashlib
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import ConfigDict, Field, ValidationError, model_validator

from bistos.errors import AppError
from bistos.models import Model, User
from bistos.service_answers import _summary_action  # pyright: ignore[reportPrivateUsage]
from bistos.service_history import CaseMatch, ServiceCase, ServiceHistory, normal
from bistos.store import Store

Mode = Literal["actual", "practice"]
Language = Literal["ko", "en"]
GuideStatus = Literal[
    "active", "paused", "resolved", "exhausted", "professional_review", "stopped", "source_changed"
]
EventAction = Literal[
    "resolved", "unresolved", "unable", "symptom_changed", "pause", "resume", "stop"
]
MAX_ATTEMPTS = 20


class GuideStep(Model):
    id: str
    case_id: str
    import_id: str
    action: str
    instruction: str
    risk: Literal["user_check", "professional", "unverified"]
    model: str
    symptom: str
    cause: str = ""
    result: str = ""
    filename: str
    sheet: str
    row_number: int = Field(ge=1)
    source_digest: str = ""


class GuideEvent(Model):
    action: EventAction
    note: str = ""
    at: str
    case_id: str = ""
    step_action: str = ""
    mode: Mode


class GuideSession(Model):
    # Transitions mutate a private copy; the complete result is validated before commit.
    model_config = ConfigDict(frozen=False)
    id: str
    revision: int = Field(ge=0)
    mode: Mode
    status: GuideStatus
    model: str
    query: str
    source: str
    language: Language
    created_at: str
    updated_at: str
    message: str
    current_step: GuideStep | None = None
    history: list[GuideEvent] = Field(default_factory=list)
    attempted_count: int = Field(default=0, ge=0, le=MAX_ATTEMPTS)
    attempted_actions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent(self) -> "GuideSession":
        if self.attempted_count != len(self.attempted_actions):
            raise ValueError("Attempt count mismatch")
        if any(event.mode != self.mode for event in self.history):
            raise ValueError("Actual and practice history must remain separate")
        if (self.status in {"active", "paused"}) != (self.current_step is not None):
            raise ValueError("Only active or paused sessions have a current step")
        if self.current_step and normal(self.current_step.model) != normal(self.model):
            raise ValueError("Step model mismatch")
        return self


def initialize_guides(db: sqlite3.Connection) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS service_guides (
            id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            payload TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""
    )


def _digest(case: ServiceCase) -> str:
    source = ServiceCase.model_validate(case.model_dump(include=set(ServiceCase.model_fields)))
    return hashlib.sha256(source.model_dump_json().encode()).hexdigest()


def _related(query: str, case: CaseMatch) -> bool:
    # CMS names the software; it does not prove that a sound fault is a network fault.
    network = r"통신|연결|연동|network|connect|communication|와이파이|wi.?fi|\blan\b|\bip\b"
    if re.search(network, query, re.IGNORECASE):
        return bool(re.search(network, case.symptom, re.IGNORECASE))
    return case.relevance == "high"


class GuideStore:
    def __init__(self, store: Store, history: ServiceHistory) -> None:
        self.store = store
        self.history = history

    def initialize(self) -> None:
        with self.store.connection() as db:
            initialize_guides(db)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _text(language: str, ko: str, en: str) -> str:
        return en if language == "en" else ko

    def _require(self, user: User) -> User:
        return self.store.require(user, "read_service")

    @staticmethod
    def _step(case: CaseMatch, mode: Mode, language: Language = "ko") -> GuideStep | None:
        if case.relevance != "high":
            return None
        actual = (_summary_action(case.action) or "").strip()
        performed = r"교체|수리|보정|교정|설정|확인|점검|재연결|재부팅|재결합|재장착|"
        performed += r"replac|repair|calibrat|check|reconnect|restart|reboot|adjust"
        if not re.search(performed, actual, re.IGNORECASE):
            return None
        if not actual or re.search(
            "".join(  # noqa: FLY002
                (
                    r"예정|계획|요청|필요|권장|추정|미실시|미수리|하지\s*않|안\s*함|불가|",
                    r"\b(?:plan(?:ned)?|recommend\w*|request\w*|not\s+(?:done|performed|replaced))\b",
                )
            ),
            actual,
            re.IGNORECASE,
        ):
            return None
        professional = bool(
            re.search(
                "".join(  # noqa: FLY002
                    (
                        r"내부|분해|분리|납땜|회로|전압|전류|펌웨어|업데이트|교정|보정|캘리브|교체|부품|",
                        r"보드|공장|초기화|설정|재설정|세팅|암호|SSID|전원\s*(?:리부팅|재부팅)|",
                        r"setting|password|reconfig|알람\s*(?:해제|끄)|",
                        r"internal|disassembl|solder|circuit|voltage|firmware|updat|calibrat|replac|",
                        r"\bboard\b|factory|reset|alarm.*(?:off|disable)",
                    )
                ),
                actual,
                re.IGNORECASE,
            )
        )
        external_object = bool(
            re.search(
                "".join(  # noqa: FLY002
                    (
                        r"랜\s*케이블|\bLAN\b|공유기|라우터|스피커|외부\s*(?:연결|케이블|커넥터)|",
                        r"router|speaker|external\s+(?:cable|connect)",
                    )
                ),
                actual,
                re.IGNORECASE,
            )
        )
        external_action = bool(
            re.search(
                r"확인|점검|재연결|check|reconnect",
                actual,
                re.IGNORECASE,
            )
        )
        safe = external_object and external_action and not professional
        if mode == "actual" and not safe:
            return None
        risk = "professional" if professional else ("user_check" if safe else "unverified")
        instruction = (
            GuideStore._text(
                language,
                "기록된 실제 조치를 실행하지 말고, 연습 결과만 선택하세요.",
                "Do not perform the recorded action. Choose a hypothetical practice outcome.",
            )
            if mode == "practice"
            else GuideStore._text(
                language,
                "아래 기록의 외부 연결 확인 범위를 검토하고 결과를 선택하세요.",
                "Review the recorded external connection check and select the observed outcome.",
            )
        )
        return GuideStep(
            id=hashlib.sha256(f"{case.case_id}:{actual}".encode()).hexdigest()[:24],
            case_id=case.case_id,
            import_id=case.import_id,
            action=actual,
            instruction=instruction,
            risk=risk,
            model=case.model,
            symptom=case.symptom,
            cause=case.cause,
            result=case.result,
            filename=case.filename,
            sheet=case.sheet,
            row_number=case.row_number,
            source_digest=_digest(case),
        )

    @staticmethod
    def _action_key(action: str) -> str:
        text = normal(action)
        # Outcome wording must not turn the same action into another attempt.
        text = re.sub(
            r"(?:후|하여|하고)(?:정상|연동|통신)(?:동작|작동|연결|확인|됨|완료)*$", "", text
        )
        text = text.replace("lancable", "랜케이블").replace("lan케이블", "랜케이블")
        text = text.replace("lan선", "랜케이블").replace("랜선", "랜케이블")
        text = text.replace("공유기부팅", "공유기재부팅")
        text = text.replace("재결합", "재연결").replace("재장착", "재연결")
        return "|".join(re.sub(r"[^a-z0-9가-힣]", "", part) for part in text.split("및"))

    def _source_valid(self, user: User, step: GuideStep) -> bool:
        try:
            case = self.history.detail(user, step.case_id)
        except AppError as exc:
            if exc.status_code in {401, 403}:
                raise
            return False
        return (
            case.import_id == step.import_id
            and normal(case.model) == normal(step.model)
            and _digest(case) == step.source_digest
        )

    def _mark_source_changed(self, session: GuideSession) -> GuideSession:
        session.status = "source_changed"
        session.current_step = None
        session.message = self._text(
            session.language,
            "서비스 원본이 갱신되거나 비활성화되어 현재 단계를 사용할 수 없습니다.",
            "The source was changed or deactivated. This step is no longer available.",
        )
        return session

    def _load(self, user: User, guide_id: str) -> GuideSession:
        with self.store.lock, self.store.connection() as db:
            self._require(user)
            row = db.execute(
                "SELECT payload FROM service_guides WHERE id=? AND owner_user_id=?",
                (guide_id, user.id),
            ).fetchone()
        if row is None:
            raise AppError("서비스 가이드를 찾을 수 없습니다.", 404)
        return GuideSession.model_validate_json(row[0])

    def create(
        self,
        user: User,
        query: str,
        model: str,
        source: str,
        case_id: str,
        mode: Mode,
        language: Language,
    ) -> dict[str, object]:
        self._require(user)
        if not model.strip() or source not in {"", "domestic", "overseas"}:
            raise AppError("제품과 자료 범위를 확인해 주세요.")
        candidates = self.history.candidates(
            user,
            query,
            model=model,
            source=source,
            language=language,
        )
        selected = next(
            (
                case
                for case in candidates
                if case.case_id == case_id and normal(case.model) == normal(model)
            ),
            None,
        )
        if selected is None:
            raise AppError("선택한 사례가 현재 제품·증상 검색 결과에 없습니다.", 409)
        step = self._step(selected, mode, language) if _related(query, selected) else None
        now = self._now()
        session = GuideSession(
            id=uuid.uuid4().hex,
            revision=0,
            mode=mode,
            status="active" if step else "professional_review",
            model=model,
            query=query.strip(),
            source=source,
            language=language,
            created_at=now,
            updated_at=now,
            current_step=step,
            message=self._text(
                language,
                "한 단계씩 확인한 뒤 결과를 선택하세요.",
                "Review each step and choose the outcome.",
            )
            if step
            else self._text(
                language,
                "기록된 조치는 전문 담당자의 확인이 필요하거나 실제 처리 내역이 없습니다.",
                "This record requires professional review or contains no performed action.",
            ),
        )
        with self.store.lock, self.store.connection() as db:
            current = self._require(user)
            fresh = self.history.detail(user, selected.case_id)
            if _digest(fresh) != _digest(selected):
                raise AppError("서비스 원본이 변경되었습니다. 다시 검색해 주세요.", 409)
            db.execute(
                "INSERT INTO service_guides VALUES(?,?,?,?,?,?)",
                (session.id, current.id, session.model_dump_json(), 0, now, now),
            )
        return session.model_dump()

    def _view(self, user: User, session: GuideSession) -> GuideSession:
        if session.current_step and not self._source_valid(user, session.current_step):
            self._mark_source_changed(session)
        return session

    def list(self, user: User) -> dict[str, object]:
        with self.store.lock, self.store.connection() as db:
            self._require(user)
            rows = db.execute(
                "SELECT payload FROM service_guides WHERE owner_user_id=? ORDER BY updated_at DESC",
                (user.id,),
            ).fetchall()
            items = [
                self._view(user, GuideSession.model_validate_json(row[0])).model_dump()
                for row in rows
            ]
        return {"items": items, "total": len(items)}

    def detail(self, user: User, guide_id: str) -> dict[str, object]:
        with self.store.lock:
            return self._view(user, self._load(user, guide_id)).model_dump()

    def _commit(
        self,
        user: User,
        original: GuideSession,
        session: GuideSession,
    ) -> dict[str, object]:
        with self.store.lock, self.store.connection() as db:
            current = self._require(user)
            steps = [step for step in (original.current_step, session.current_step) if step]
            if any(not self._source_valid(user, step) for step in steps):
                session = self._mark_source_changed(original.model_copy(deep=True))
            session.revision = original.revision + 1
            session.updated_at = self._now()
            checked = GuideSession.model_validate(session.model_dump())
            changed = db.execute(
                "".join(  # noqa: FLY002
                    (
                        "UPDATE service_guides SET payload=?,revision=?,updated_at=? ",
                        "WHERE id=? AND owner_user_id=? AND revision=?",
                    )
                ),
                (
                    checked.model_dump_json(),
                    checked.revision,
                    checked.updated_at,
                    checked.id,
                    current.id,
                    original.revision,
                ),
            )
            if changed.rowcount != 1:
                raise AppError("서비스 가이드가 갱신되었습니다. 다시 확인해 주세요.", 409)
        return checked.model_dump()

    def _advance(self, user: User, session: GuideSession) -> None:
        step = session.current_step
        if step:
            session.attempted_actions.append(self._action_key(step.action))
        session.attempted_count = len(session.attempted_actions)
        session.current_step = None
        if session.attempted_count >= MAX_ATTEMPTS:
            session.status = "exhausted"
            session.message = self._text(
                session.language,
                "확인 가능한 단계 한도에 도달했습니다.",
                "The maximum number of documented steps has been reached.",
            )
            return
        candidates = self.history.candidates(
            user,
            session.query,
            model=session.model,
            source=session.source,
            language=session.language,
        )
        blocked = False
        attempted_parts = {
            part for action in session.attempted_actions for part in action.split("|")
        }
        for candidate in candidates:
            key = self._action_key((_summary_action(candidate.action) or "").strip())
            if (
                candidate.relevance != "high"
                or not _related(session.query, candidate)
                or normal(candidate.model) != normal(session.model)
                or key in session.attempted_actions
                or set(key.split("|")) <= attempted_parts
            ):
                continue
            candidate_step = self._step(candidate, session.mode, session.language)
            if not candidate_step:
                blocked = blocked or bool(candidate.action.strip())
                continue
            session.current_step = candidate_step
            break
        if session.current_step:
            session.status = "active"
            session.message = self._text(
                session.language,
                "다음 확인 단계를 제시합니다.",
                "Review the next documented step.",
            )
        else:
            session.status = "professional_review" if blocked else "exhausted"
            session.message = self._text(
                session.language,
                "남은 기록은 전문 검토가 필요하거나 실제 조치를 확인할 수 없습니다."
                if blocked
                else "확인된 다음 조치를 찾지 못했습니다.",
                "Remaining records require review or do not confirm a performed action."
                if blocked
                else "No further documented action was found.",
            )

    def event(  # noqa: C901
        self,
        user: User,
        guide_id: str,
        revision: int,
        action: EventAction,
        note: str = "",
    ) -> dict[str, object]:
        original = self._load(user, guide_id)
        session = original.model_copy(deep=True)
        if revision != session.revision:
            raise AppError("서비스 가이드가 갱신되었습니다. 새로고침해 주세요.", 409)
        if session.status not in {"active", "paused"}:
            raise AppError("종료된 서비스 가이드는 변경할 수 없습니다.", 409)
        if session.status == "paused" and action not in {"resume", "stop"}:
            raise AppError("일시 중지된 가이드는 재개하거나 중단할 수 있습니다.", 409)
        if action == "resume" and session.status != "paused":
            raise AppError("일시 중지된 가이드만 재개할 수 있습니다.", 409)
        if action == "symptom_changed" and not note.strip():
            raise AppError("증상 변경 시 새 증상을 입력해야 합니다.")
        if action not in {
            "resolved",
            "unresolved",
            "unable",
            "symptom_changed",
            "pause",
            "resume",
            "stop",
        }:
            raise AppError("가이드 결과가 올바르지 않습니다.")
        step = session.current_step
        session.history.append(
            GuideEvent(
                action=action,
                note=note.strip(),
                at=self._now(),
                case_id=step.case_id if step else "",
                step_action=step.action if step else "",
                mode=session.mode,
            )
        )
        if action == "pause":
            session.status = "paused"
            session.message = self._text(
                session.language,
                "일시 중지했습니다. 현재 단계부터 재개할 수 있습니다.",
                "Paused. Resume from the current step.",
            )
        elif action == "resume":
            session.status = "active"
            session.message = self._text(
                session.language,
                "현재 단계를 다시 확인하세요.",
                "Review the current step again.",
            )
        elif action == "stop":
            session.status = "stopped"
            session.current_step = None
            session.message = self._text(
                session.language,
                "사용자가 진행을 중단했습니다.",
                "Stopped by the user.",
            )
        elif action == "resolved":
            session.status = "resolved"
            session.current_step = None
            session.message = (
                self._text(
                    session.language,
                    "연습에서 해결된 것으로 기록했습니다. 실제 수리 결과가 아닙니다.",
                    "Hypothetical resolution recorded. This is not an actual repair result.",
                )
                if session.mode == "practice"
                else self._text(
                    session.language,
                    "사용자가 해결됨으로 기록했습니다. 공식 수리 결과로 자동 승격하지 않습니다.",
                    "Resolution reported by the user; not an automatically verified repair.",
                )
            )
        else:
            if action == "symptom_changed":
                session.query = note.strip()
            self._advance(user, session)
        return self._commit(user, original, session)


def validate_guides(db: sqlite3.Connection) -> None:
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='service_guides'"
    ).fetchone():
        return
    try:
        for row in db.execute("SELECT * FROM service_guides").fetchall():
            session = GuideSession.model_validate_json(row["payload"])
            if (
                session.id != row["id"]
                or session.revision != row["revision"]
                or session.created_at != row["created_at"]
                or session.updated_at != row["updated_at"]
                or not db.execute(
                    "SELECT 1 FROM users WHERE id=?", (row["owner_user_id"],)
                ).fetchone()
            ):
                raise ValueError("Guide metadata mismatch")  # noqa: TRY301
            datetime.fromisoformat(session.created_at)
            datetime.fromisoformat(session.updated_at)
            for event in session.history:
                datetime.fromisoformat(event.at)
    except (ValueError, ValidationError, KeyError, IndexError, sqlite3.DatabaseError) as exc:
        raise AppError("서비스 가이드 백업의 형식이나 계정 연결이 올바르지 않습니다.") from exc

