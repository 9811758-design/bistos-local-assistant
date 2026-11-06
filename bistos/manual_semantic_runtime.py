"""Optional background refresh for the local manual-page semantic index."""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path
from threading import Event, Lock, Thread
from typing import cast

from bistos.manual_semantic import ManualSemanticSearch
from bistos.models import Document, Page
from bistos.service_semantic import LocalOllamaEmbeddings

log = logging.getLogger(__name__)


class ManualSemanticRuntime:
    def __init__(self, index: ManualSemanticSearch) -> None:
        self.index = index
        self._lock = Lock()
        self._stop = Event()
        self._running = False
        self._pending: Callable[[], Sequence[tuple[Document, Page]]] | None = None
        self._worker: Thread | None = None
        self.last_status = "ready"
        self._indexed_epoch: int | None = None
        self._pending_epoch: int | None = None

    def schedule_refresh(
        self, pages: Callable[[], Sequence[tuple[Document, Page]]]
    ) -> None:
        with self._lock:
            self._pending = pages
            if self._running:
                return
            self._running = True
            self._worker = Thread(target=self._run, name="manual-semantic-refresh", daemon=True)
            self._worker.start()

    def ensure_epoch(
        self, epoch: int, pages: Callable[[], Sequence[tuple[Document, Page]]]
    ) -> None:
        """Coalesce a refresh request after a document/availability epoch changes."""
        with self._lock:
            if self._indexed_epoch == epoch:
                return
            self._pending_epoch = epoch
        self.schedule_refresh(pages)

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                supplier = self._pending
                self._pending = None
                epoch = self._pending_epoch
                self._pending_epoch = None
                if supplier is None:
                    self._running = False
                    return
            try:
                self.last_status = "refreshing"
                self.index.rebuild(supplier())
                if epoch is not None:
                    with self._lock:
                        self._indexed_epoch = epoch
                self.last_status = "ready"
            except Exception:
                self.last_status = "error"
                log.exception("manual semantic refresh failed; keyword search remains available")

    def close(self) -> None:
        self._stop.set()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=2.0)


def configure_manual_semantic(data_dir: Path) -> ManualSemanticRuntime | None:  # noqa: PLR0911
    """Enable only with explicit local model/digest configuration."""
    config_path = data_dir / "manual-semantic.json"
    if not config_path.is_file():
        return None
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.exception("manual semantic configuration could not be read")
        return None
    if not isinstance(value, dict) or not value.get("enabled"):
        return None
    config = cast("dict[str, object]", value)
    model_value = config.get("model")
    digest_value = config.get("digest")
    model = model_value if isinstance(model_value, str) else ""
    digest = digest_value if isinstance(digest_value, str) else ""
    if not model or not digest:
        log.error("manual semantic configuration is missing model/digest")
        return None
    minimum = config.get("min_score", 0.64)
    if not isinstance(minimum, (int, float)) or isinstance(minimum, bool):
        return None
    minimum = float(minimum)
    if not math.isfinite(minimum) or not 0 <= minimum <= 1:
        return None
    try:
        provider = LocalOllamaEmbeddings(model, digest)
        index = ManualSemanticSearch(
            data_dir / "manual-semantic.sqlite3", provider, min_score=minimum,
        )
        return ManualSemanticRuntime(index)
    except (OSError, TypeError, ValueError, OverflowError, sqlite3.Error):
        log.warning(
            "manual semantic configuration is invalid; semantic retrieval disabled",
            exc_info=True,
        )
        return None
