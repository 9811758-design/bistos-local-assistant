import logging
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal, Protocol

from bistos.answer_translation import answer_texts, needs_answer_translation
from bistos.answers import current_result
from bistos.conversation import answer_context, followup_scope
from bistos.errors import AppError
from bistos.job_history import JobHistory, JobStore
from bistos.manual_defaults import ManualDefaults
from bistos.models import AnswerContext, AnswerResult, AnswerTranslation, Job, SearchFilters, User


class Engine(Protocol):
    def answer(
        self, user: User, product_id: str, question: str, use_ai: bool = True,
        filters: SearchFilters | None = None,
        context: AnswerContext | None = None,
        inquiry_mode: Literal["auto", "product", "troubleshooting"] = "auto",
    ) -> AnswerResult: ...


@dataclass(frozen=True, slots=True)
class Pending:
    job: Job
    user: User
    product_id: str
    use_ai: bool
    created: float
    queued_at: float


class JobManager:
    """Serialize generations, retaining the running slot until the backend call returns."""

    def __init__(
        self,
        engine: Engine,
        store: JobStore,
        wait_timeout: float = 120,
        *,
        translator: Callable[[User, AnswerResult, Literal["ko", "en"]], AnswerResult] | None = None,
    ) -> None:
        self.engine = engine
        self.store = store
        self.translator = translator
        self.records = JobHistory(store)
        self.records.reset_pending()
        self.wait_timeout = wait_timeout
        self.condition = threading.Condition(threading.RLock())
        self.pending: deque[Pending] = deque()
        self.running: Pending | None = None
        self.cancelled: set[str] = set()
        self.closed = False
        self.maintaining = False
        self.worker = threading.Thread(target=self._run, name="bistos-answers", daemon=True)
        self.worker.start()

    def submit(
        self,
        user: User,
        product_id: str,
        question: str,
        use_ai: bool = True,
        language: Literal["ko", "en"] = "ko",
        filters: SearchFilters | None = None,
        *,
        parent_job_id: str = "",
        inquiry_mode: Literal["auto", "product", "troubleshooting"] = "auto",
    ) -> Job:
        actor = self.store.user(user.id)
        if not actor.active or "ask" not in actor.capabilities:
            raise AppError("질문 권한이 없습니다.", 403)
        if not question.strip() or len(question) > 2000:
            raise AppError("질문은 1~2000자로 입력해 주세요.")
        with self.condition:
            self._check_capacity(actor)
            selected = filters or SearchFilters()
            if parent_job_id:
                parent, selected = followup_scope(
                    self.records, actor, product_id, parent_job_id, selected,
                )
                parent_job_id = parent.id
            else:
                selected = ManualDefaults(self.store).resolve(actor, product_id, selected)
            job = Job(
                id=uuid.uuid4().hex, status="queued", question=question, language=language,
                filters=selected, product_id=product_id, parent_job_id=parent_job_id,
                inquiry_mode=inquiry_mode,
            )
            return self._enqueue(actor, product_id, use_ai, job)

    def translate(self, user: User, identifier: str, language: Literal["ko", "en"] = "en") -> Job:
        """Translate one owned answer through the same bounded generation queue."""
        actor = self.store.user(user.id)
        with self.condition:
            requested = self.records.get(actor, identifier)
            source_id = requested.source_job_id or requested.id
            source = self.records.get(actor, source_id)
            if source.status != "completed" or source.result is None:
                raise AppError("완료된 답변만 번역할 수 있습니다.", 409)
            if (source.result.translation is not None
                    and source.result.translation.language == language
                    and not source.result.translation_error):
                return source
            # If the original presentation is already in the requested locale,
            # return the immutable source instead of creating a no-op job.
            # This also prevents repeated POSTs from queueing duplicate jobs
            # when a source answer was originally written in English.
            if not needs_answer_translation(source.result, language):
                return source
            cached = self.records.cached_translation(actor, source_id, language)
            if cached is not None:
                return cached
            active = list(self.pending)
            if self.running is not None:
                active.append(self.running)
            for entry in active:
                if (entry.user.id == actor.id and entry.job.source_job_id == source_id
                        and entry.job.language == language):
                    return self.get(actor, entry.job.id)
            self._check_capacity(actor)
            job = Job(
                id=uuid.uuid4().hex,
                status="queued",
                question=source.question,
                language=language,
                source_job_id=source_id, inquiry_mode=source.inquiry_mode,
                product_id=self.records.product_id(actor, source_id),
            )
            return self._enqueue(actor, self.records.product_id(actor, source_id), True, job)

    def _check_capacity(self, actor: User) -> None:
        if self.closed or self.maintaining:
            raise AppError("프로그램 점검 중입니다. 잠시 후 다시 질문해 주세요.", 503)
        active_users = {entry.user.id for entry in self.pending}
        if self.running is not None:
            active_users.add(self.running.user.id)
        if actor.id in active_users:
            raise AppError(
                "이전 질문이 처리 중입니다. 완료되거나 취소 처리가 끝난 후 다시 질문해 주세요.",
                429,
            )
        if len(self.pending) >= 8:
            raise AppError("질문 대기열이 가득 찼습니다. 잠시 후 다시 시도해 주세요.", 429)

    def _enqueue(self, actor: User, product_id: str, use_ai: bool, job: Job) -> Job:
        entry = Pending(job, actor, product_id, use_ai, time.time(), time.monotonic())
        self.records.save(actor.id, product_id, entry.created, job)
        self.pending.append(entry)
        self.condition.notify_all()
        return job

    def get(self, user: User, identifier: str) -> Job:
        job = self.records.get(user, identifier)
        with self.condition:
            positions = {entry.job.id: index for index, entry in enumerate(self.pending, 1)}
            return job.model_copy(update={"queue_position": positions.get(identifier, 0)})

    def history(self, user: User) -> list[Job]:
        return self.records.list(user)

    def cancel(self, user: User, identifier: str) -> Job:
        job = self.records.get(user, identifier)
        with self.condition:
            matching = [entry for entry in self.pending if entry.job.id == identifier]
            if self.running is not None and self.running.job.id == identifier:
                matching.append(self.running)
            if not matching:
                return job
            entry = matching[0]
            self.pending = deque(item for item in self.pending if item.job.id != identifier)
            if self.running is not None and self.running.job.id == identifier:
                self.cancelled.add(identifier)
            cancelled = job.model_copy(
                update={
                    "status": "cancelled",
                    "result": None,
                    "error": "취소되었습니다. 실행 자리는 로컬 계산이 종료되면 반환됩니다.",
                }
            )
            self.records.save(entry.user.id, entry.product_id, entry.created, cancelled)
            return cancelled

    @contextmanager
    def maintenance(self) -> Generator[None]:
        with self.condition:
            if self.running is not None or self.pending:
                raise AppError("처리 중인 질문이 끝난 후 백업 또는 복원을 실행해 주세요.", 409)
            self.maintaining = True
            try:
                yield
            finally:
                self.maintaining = False
                self.condition.notify_all()

    def reset(self) -> None:
        with self.condition:
            if self.running is not None:
                raise AppError("질문 처리 중에는 기록을 초기화할 수 없습니다.", 409)
            self.pending.clear()
            self.cancelled.clear()
            self.records.reset_pending()

    def close(self) -> None:
        with self.condition:
            self.closed = True
            for entry in self.pending:
                self.records.save(
                    entry.user.id,
                    entry.product_id,
                    entry.created,
                    entry.job.model_copy(
                        update={"status": "cancelled", "error": "프로그램을 종료했습니다."}
                    ),
                )
            self.pending.clear()
            if self.running is not None:
                self.cancelled.add(self.running.job.id)
            self.condition.notify_all()
        self.worker.join(timeout=5)

    def _run(self) -> None:
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or bool(self.pending))
                if self.closed:
                    return
                entry = self.pending.popleft()
                self.running = entry
            self._execute(entry)
            with self.condition:
                self.running = None
                self.condition.notify_all()

    def _answer(self, entry: Pending) -> AnswerResult:
        if entry.job.source_job_id:
            source = self.records.get(entry.user, entry.job.source_job_id)
            if source.status != "completed" or source.result is None:
                raise AppError("완료된 답변만 번역할 수 있습니다.", 409)
            return source.result
        if entry.job.inquiry_mode != "auto":
            context = (answer_context(self.records, entry.user, entry.job)
                       if entry.job.parent_job_id else None)
            return self.engine.answer(
                entry.user, entry.product_id, entry.job.question, entry.use_ai,
                filters=entry.job.filters, context=context, inquiry_mode=entry.job.inquiry_mode,
            )
        if entry.job.parent_job_id:
            context = answer_context(self.records, entry.user, entry.job)
            return self.engine.answer(
                entry.user, entry.product_id, entry.job.question, entry.use_ai,
                filters=entry.job.filters, context=context,
            )
        if entry.job.filters != SearchFilters():
            return self.engine.answer(
                entry.user, entry.product_id, entry.job.question, entry.use_ai,
                filters=entry.job.filters,
            )
        return self.engine.answer(
            entry.user, entry.product_id, entry.job.question, entry.use_ai
        )

    def _execute(self, entry: Pending) -> None:
        with self.condition:
            if entry.job.id in self.cancelled:
                self.cancelled.discard(entry.job.id)
                return
            if time.monotonic() - entry.queued_at > self.wait_timeout:
                self.records.save(
                    entry.user.id,
                    entry.product_id,
                    entry.created,
                    entry.job.model_copy(
                        update={"status": "failed", "error": "질문 대기 제한 시간을 넘겼습니다."}
                    ),
                )
                return
            self.records.save(
                entry.user.id,
                entry.product_id,
                entry.created,
                entry.job.model_copy(update={"status": "running"}),
            )
        try:
            result = self._answer(entry)
            with self.condition:
                cancelled = entry.job.id in self.cancelled
            if (
                entry.job.source_job_id
                and result.translation is not None
                and result.translation.language != entry.job.language
                and not needs_answer_translation(result, entry.job.language)
                and not cancelled
            ):
                result = result.model_copy(update={
                    "translation": AnswerTranslation(
                        language=entry.job.language, texts=answer_texts(result)
                    ),
                    "translation_error": "",
                })
            if needs_answer_translation(result, entry.job.language) and not cancelled:
                if self.translator is None:
                    result = result.model_copy(
                        update={"translation_error": "로컬 답변 번역을 사용할 수 없습니다."}
                    )
                else:
                    result = self.translator(entry.user, result, entry.job.language)
            result = current_result(self.store, entry.user, result)
            self.records.ancestors(entry.user, entry.job)
            completed = entry.job.model_copy(update={"status": "completed", "result": result})
        except AppError as error:
            completed = entry.job.model_copy(update={"status": "failed", "error": error.detail})
        except Exception:
            logging.getLogger(__name__).exception("Unexpected answer worker failure")
            completed = entry.job.model_copy(
                update={
                    "status": "failed",
                    "error": "질문 처리 중 내부 오류가 발생했습니다. 관리자에게 알려 주세요.",
                }
            )
        with self.condition:
            if entry.job.id in self.cancelled:
                completed = entry.job.model_copy(
                    update={"status": "cancelled", "error": "질문을 취소했습니다."}
                )
                self.cancelled.discard(entry.job.id)
            self.records.save(entry.user.id, entry.product_id, entry.created, completed)
