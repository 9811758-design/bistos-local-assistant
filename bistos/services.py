from dataclasses import dataclass

from bistos.answers import AnswerEngine
from bistos.auth import Auth
from bistos.backup import BackupService
from bistos.documents import DocumentService
from bistos.jobs import JobManager
from bistos.manual_semantic_runtime import ManualSemanticRuntime, configure_manual_semantic
from bistos.models import Document, Page
from bistos.search import SearchEngine
from bistos.service_guides import GuideStore
from bistos.service_history import ServiceHistory
from bistos.service_semantic_runtime import SemanticRuntime, configure_semantic
from bistos.settings import Settings
from bistos.store import Store


@dataclass(frozen=True, slots=True)
class Services:
    settings: Settings
    store: Store
    auth: Auth
    documents: DocumentService
    backup: BackupService
    answers: AnswerEngine
    jobs: JobManager
    search: SearchEngine
    service_history: ServiceHistory
    service_guides: GuideStore
    semantic_runtime: SemanticRuntime | None = None
    manual_semantic_runtime: ManualSemanticRuntime | None = None

    def close(self) -> None:
        self.jobs.close()
        if self.semantic_runtime is not None:
            self.semantic_runtime.close()
        if self.manual_semantic_runtime is not None:
            self.manual_semantic_runtime.close()

    @classmethod
    def build(cls, settings: Settings) -> "Services":
        store = Store(settings.data_dir)
        store.initialize()
        manual_semantic_runtime = configure_manual_semantic(settings.data_dir)
        index_user = next(
            (
                candidate for candidate in store.users()
                if candidate.active and "ask" in candidate.capabilities
                and "read_service" in candidate.capabilities
            ),
            next(
                (candidate for candidate in store.users()
                 if candidate.active and "ask" in candidate.capabilities),
                None,
            ),
        )
        def page_supplier() -> list[tuple[Document, Page]]:
            if index_user is None:
                return []
            return [
                item
                for product in store.products()
                for item in store.searchable_pages(index_user, product.id)
            ]

        def refresh_manual_index() -> None:
            if manual_semantic_runtime is not None:
                manual_semantic_runtime.ensure_epoch(store.epoch(), page_supplier)

        search = SearchEngine(
            store,
            semantic=(
                manual_semantic_runtime.index
                if manual_semantic_runtime is not None
                else None
            ),
            before_search=refresh_manual_index,
        )
        answers = AnswerEngine(store, settings, search=search)
        service_history = ServiceHistory(store, settings.root, answers.ollama)
        service_history.initialize()
        semantic_runtime = configure_semantic(settings.data_dir)
        if semantic_runtime is not None:
            service_history.semantic = semantic_runtime.index
            semantic_runtime.schedule_refresh(service_history.semantic_cases)
        service_guides = GuideStore(store, service_history)
        service_guides.initialize()
        refresh_manual_index()
        return cls(
            settings=settings,
            store=store,
            auth=Auth(store),
            documents=DocumentService(store, settings),
            backup=BackupService(store),
            answers=answers,
            jobs=JobManager(answers, store, translator=answers.translate),
            search=search,
            service_history=service_history,
            service_guides=service_guides,
            semantic_runtime=semantic_runtime,
            manual_semantic_runtime=manual_semantic_runtime,
        )
