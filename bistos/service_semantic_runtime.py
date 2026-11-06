"""Optional background refresh for the local service semantic index."""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path
from threading import Event, Lock, Thread
from typing import cast

from bistos.service_semantic import CaseIdentity, LocalOllamaEmbeddings, SemanticSearch

log = logging.getLogger(__name__)
_DEFAULT_PREFIX = (
    "Instruct: Retrieve the most relevant service records for the user symptom.\nQuery: "
)


class SemanticRuntime:
    def __init__(self, index: SemanticSearch) -> None:
        self.index = index
        self._lock = Lock()
        self._stop = Event()
        self._running = False
        self._pending: Callable[[], Sequence[CaseIdentity]] | None = None
        self._worker: Thread | None = None
        self.last_status = "ready"

    def schedule_refresh(self, cases: Callable[[], Sequence[CaseIdentity]]) -> None:
        with self._lock:
            self._pending = cases
            if self._running:
                return
            self._running = True
            self._worker = Thread(target=self._run, name="service-semantic-refresh", daemon=True)
            self._worker.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                supplier = self._pending
                self._pending = None
                if supplier is None:
                    self._running = False
                    return
            try:
                self.last_status = "refreshing"
                self.index.rebuild(supplier())
                self.last_status = "ready"
            except Exception:
                self.last_status = "error"
                log.exception(
                    "service semantic index refresh failed; lexical search remains available"
                )

    def close(self) -> None:
        self._stop.set()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=2.0)


def configure_semantic(data_dir: Path) -> SemanticRuntime | None:  # noqa: PLR0911
    """Read opt-in JSON config; absent/disabled config creates no worker/index."""
    config_path = data_dir / "service-semantic.json"
    if not config_path.is_file():
        return None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.exception("service semantic configuration could not be read")
        return None
    if not isinstance(config, dict) or not config.get("enabled"):
        return None
    config = cast("dict[str, object]", config)
    model_value = config.get("model")
    digest_value = config.get("digest")
    model = model_value if isinstance(model_value, str) else ""
    digest = digest_value if isinstance(digest_value, str) else ""
    if not model or not digest:
        log.error("service semantic configuration is missing model/digest")
        return None
    try:
        min_value = config.get("min_score", 0.78)
        if not isinstance(min_value, (int, float)) or isinstance(min_value, bool):
            log.warning("service semantic min_score is not numeric; semantic retrieval disabled")
            return None
        min_score = float(min_value)
        if not math.isfinite(min_score) or not 0 <= min_score <= 1:
            log.warning("service semantic min_score is outside 0..1; semantic retrieval disabled")
            return None
        provider = LocalOllamaEmbeddings(model, digest)
        index = SemanticSearch(
            data_dir / "service-semantic.sqlite3",
            provider,
            min_score=min_score,
            query_prefix=str(config.get("query_prefix", _DEFAULT_PREFIX)),
        )
        return SemanticRuntime(index)
    except (OSError, TypeError, ValueError, OverflowError, sqlite3.Error):
        log.warning(
            "service semantic configuration is invalid; semantic retrieval disabled",
            exc_info=True,
        )
        return None
