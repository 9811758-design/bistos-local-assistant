import sqlite3
from contextlib import AbstractContextManager
from typing import Protocol

from bistos.answers import AnswerStore, current_result
from bistos.errors import AppError
from bistos.models import AnswerResult, Job, User


def can_follow_up(result: AnswerResult) -> bool:
    """Only grounded answers and bounded symptom confirmations carry context."""
    citations = {item.evidence_id for item in result.evidence}
    if not result.claims and (
        result.inquiry_mode == "troubleshooting"
        and result.consultation_state in {"active", "exhausted"}
        and result.status == "clarification_required"
        and set(result.limitation_codes) <= {
            "symptom_confirmation_required", "validity_unconfirmed",
        }
        and "symptom_confirmation_required" in result.limitation_codes
        and result.questions and result.evidence and not result.conflicts and not result.topics
    ):
        return True
    if not result.claims or any(
        not claim.citation_ids or not set(claim.citation_ids) <= citations
        for claim in result.claims
    ):
        return False
    blocked = {
        "model_unclear", "version_applicability_unclear", "source_conflict",
        "visual_review_pending", "question_scope_unclear", "applicability_unconfirmed",
        "explanation_unverified",
    }
    if result.conflicts or blocked.intersection(result.limitation_codes):
        return False
    if result.status == "answered":
        return True
    if result.status == "partial_answer":
        return bool(
            "incomplete_evidence" in result.limitation_codes
            and all(claim.presentation == "direct" for claim in result.claims)
            and not result.topics
        )
    return bool(
        result.status in {"clarification_required", "service_required"}
        and "symptom_confirmation_required" in result.limitation_codes
        and result.questions and not result.topics
    )


class JobStore(AnswerStore, Protocol):
    def connection(self) -> AbstractContextManager[sqlite3.Connection]: ...


class JobHistory:
    def __init__(self, store: JobStore) -> None:
        self.store = store
        with store.connection() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY,user_id TEXT NOT NULL,product_id TEXT NOT NULL,
                created REAL NOT NULL,payload TEXT NOT NULL)""",
            )

    def save(self, owner: str, product_id: str, created: float, job: Job) -> None:
        with self.store.connection() as connection:
            connection.execute(
                """INSERT INTO jobs(id,user_id,product_id,created,payload) VALUES(?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload""",
                (job.id, owner, product_id, created, job.model_dump_json()),
            )

    def get(self, user: User, identifier: str) -> Job:
        actor = self.store.user(user.id)
        if not actor.active or "ask" not in actor.capabilities:
            raise AppError("질문 기록 접근 권한이 없습니다.", 403)
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT payload,product_id FROM jobs WHERE id=? AND user_id=?",
                (identifier, actor.id),
            ).fetchone()
        if row is None:
            raise AppError("질문 기록을 찾을 수 없습니다.", 404)
        job = Job.model_validate_json(row["payload"])
        if job.result is not None:
            try:
                current_result(self.store, actor, job.result)
                self.ancestors(actor, job)
                self._translation_source(actor, job)
            except AppError:
                return Job(
                    id=job.id,
                    status="failed",
                    question="접근할 수 없는 질문 기록",
                    error="근거의 게시 상태 또는 권한이 변경되어 이전 답변을 표시할 수 없습니다.",
                )
        return job

    def _translation_source(self, user: User, job: Job) -> None:
        if not job.source_job_id:
            return
        with self.store.connection() as db:
            row = db.execute(
                "SELECT payload,product_id FROM jobs WHERE id=? AND user_id=?",
                (job.source_job_id, user.id),
            ).fetchone()
        if row is None:
            raise AppError("원본 질문 기록을 찾을 수 없습니다.", 403)
        source = Job.model_validate_json(row["payload"]).model_copy(
            update={"product_id": str(row["product_id"])}
        )
        if source.source_job_id or source.status != "completed" or source.result is None:
            raise AppError("원본 질문 기록을 볼 수 없습니다.", 403)
        current_result(self.store, user, source.result)
        self.ancestors(user, source)

    def ancestors(self, user: User, job: Job) -> list[Job]:
        """Authorize every context ancestor, including when reading a later answer."""
        parents: list[Job] = []
        seen = {job.id}
        parent_id = job.parent_job_id
        product_id = job.product_id
        if parent_id and not product_id:
            product_id = self.product_id(user, job.id)
        while parent_id:
            if parent_id in seen or len(parents) >= 8:
                raise AppError("대화가 길어졌습니다. 새 질문으로 시작해 주세요.", 409)
            seen.add(parent_id)
            with self.store.connection() as db:
                row = db.execute(
                    "SELECT payload,product_id FROM jobs WHERE id=? AND user_id=?",
                    (parent_id, user.id),
                ).fetchone()
            if row is None or row["product_id"] != product_id:
                raise AppError("같은 사용자·제품의 답변에만 이어서 질문할 수 있습니다.", 409)
            parent = Job.model_validate_json(row["payload"]).model_copy(
                update={"product_id": str(row["product_id"])}
            )
            if (
                parent.status != "completed" or parent.result is None
                or not can_follow_up(parent.result)
                or parent.source_job_id
            ):
                raise AppError("근거가 확인된 완료 답변에만 이어서 질문할 수 있습니다.", 409)
            current_result(self.store, user, parent.result)
            parents.append(parent)
            parent_id = parent.parent_job_id
        return parents

    def list(self, user: User) -> list[Job]:
        with self.store.connection() as connection:
            rows = connection.execute(
                """SELECT id FROM jobs WHERE user_id=?
                AND COALESCE(json_extract(payload,'$.source_job_id'),'')=''
                ORDER BY created DESC LIMIT 100""",
                (user.id,),
            ).fetchall()
        return [self.get(user, row["id"]) for row in rows]

    def product_id(self, user: User, identifier: str) -> str:
        """Read queue metadata only after the caller has authorized the source job."""
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT product_id FROM jobs WHERE id=? AND user_id=?",
                (identifier, user.id),
            ).fetchone()
        if row is None:
            raise AppError("질문 기록을 찾을 수 없습니다.", 404)
        return str(row["product_id"])

    def cached_translation(self, user: User, source_id: str, language: str = "en") -> Job | None:
        with self.store.connection() as connection:
            rows = connection.execute(
                """SELECT id FROM jobs WHERE user_id=?
                AND json_extract(payload,'$.source_job_id')=?
                AND json_extract(payload,'$.status')='completed'
                AND json_extract(payload,'$.result.translation.language')=?
                AND COALESCE(json_extract(payload,'$.result.translation_error'),'')=''
                ORDER BY created DESC""",
                (user.id, source_id, language),
            ).fetchall()
        for row in rows:
            job = self.get(user, row["id"])
            if job.status == "completed" and job.result is not None:
                return job
        return None

    def reset_pending(self) -> None:
        with self.store.connection() as connection:
            rows = connection.execute("SELECT id,payload FROM jobs").fetchall()
            for row in rows:
                job = Job.model_validate_json(row["payload"])
                if job.status in {"queued", "running"}:
                    updated = job.model_copy(
                        update={
                            "status": "cancelled",
                            "error": "프로그램 재시작 또는 복원으로 질문이 취소되었습니다.",
                        }
                    )
                    connection.execute(
                        "UPDATE jobs SET payload=? WHERE id=?",
                        (updated.model_dump_json(), job.id),
                    )
