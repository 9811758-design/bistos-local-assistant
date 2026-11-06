"""Validated archive restoration, with a preserved rollback snapshot."""

import io
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bistos.document_files import digest
from bistos.document_integrity import verify_document_files
from bistos.errors import AppError
from bistos.library import asset_path, merge_document_metadata
from bistos.models import Document, LibraryAsset, LibraryAssetOverlay, Page, User
from bistos.service_guides import initialize_guides, validate_guides
from bistos.service_history import initialize_tables, validate_snapshots
from bistos.store import Store


class BackupEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=0)


class BackupManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    format: Literal["bistos-backup-1"] = "bistos-backup-1"
    created_at: str
    files: list[BackupEntry]


class BackupService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def create(self, user: User) -> Path:
        self.store.require(user, "manage_documents")
        self.store.require(user, "manage_users")
        if any(doc.kind == "service" for doc in self.store.documents(user)):
            self.store.require(user, "read_service")
        with self.store.connection() as db:
            if self._has_service_assets(db):
                self.store.require(user, "read_service")
        with self.store.lock, tempfile.TemporaryDirectory(dir=self.store.data_dir) as temporary:
            stage = Path(temporary)
            snapshot = stage / "bistos.sqlite3"
            with self.store.connection() as source, closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
            files = [
                snapshot,
                *sorted((self.store.data_dir / "documents").rglob("*")),
                *sorted((self.store.data_dir / "library-assets").rglob("*")),
                *sorted((self.store.data_dir / "service-sources").rglob("*")),
            ]
            timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            output = (
                self.store.data_dir / "backups" / f"bistos-{timestamp}-{uuid.uuid4().hex[:8]}.zip"
            )
            entries: list[BackupEntry] = []
            with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in files:
                    if not path.is_file():
                        continue
                    if path.is_symlink():
                        raise AppError("저장소의 심볼릭 링크는 백업할 수 없습니다.")
                    relative = (
                        "bistos.sqlite3"
                        if path == snapshot
                        else path.relative_to(self.store.data_dir).as_posix()
                    )
                    content = path.read_bytes()
                    entries.append(
                        BackupEntry(path=relative, sha256=digest(content), size=len(content))
                    )
                    archive.writestr(relative, content)
                manifest = BackupManifest(created_at=datetime.now(UTC).isoformat(), files=entries)
                archive.writestr("manifest.json", manifest.model_dump_json())
            return output

    def restore(self, user: User, content: bytes) -> None:
        self.store.require(user, "manage_documents")
        self.store.require(user, "manage_users")
        if len(content) > 8 * 1024 * 1024 * 1024:
            raise AppError("백업 파일은 8GiB 이하여야 합니다.")
        with self.store.lock, tempfile.TemporaryDirectory(dir=self.store.data_dir) as temporary:
            stage = Path(temporary)
            self._extract(content, stage)
            self._validate_database(stage)
            with closing(sqlite3.connect(stage / "bistos.sqlite3")) as snapshot:
                documents = [
                    Document.model_validate_json(row[0])
                    for row in snapshot.execute("SELECT payload FROM documents")
                ]
                if self._has_service_assets(snapshot):
                    self.store.require(user, "read_service")
            if any(doc.kind == "service" for doc in documents):
                self.store.require(user, "read_service")
            self.create(user)
            self._replace(stage)

    @staticmethod
    def _has_service_assets(db: sqlite3.Connection) -> bool:
        if (
            db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='service_imports'"
            ).fetchone()
            and db.execute("SELECT 1 FROM service_imports LIMIT 1").fetchone()
        ):
            return True
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='library_assets'"
        ).fetchone():
            return False
        return any(
            LibraryAsset.model_validate_json(row[0]).kind == "service"
            for row in db.execute("SELECT payload FROM library_assets")
        )

    @staticmethod
    def _extract(content: bytes, stage: Path) -> None:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                names = [entry.filename for entry in entries]
                if len(names) != len(set(names)) or len(names) > 50000:
                    raise AppError("중복 파일 또는 파일 수 제한을 초과한 백업입니다.")
                if sum(entry.file_size for entry in entries) > 12 * 1024 * 1024 * 1024:
                    raise AppError("백업 압축 해제 크기가 12GiB 제한을 초과합니다.")
                for entry in entries:
                    path = PurePosixPath(entry.filename)
                    allowed = (
                        entry.filename in {"manifest.json", "bistos.sqlite3"}
                        or (
                            len(path.parts) == 3
                            and path.parts[0] == "documents"
                            and path.parts[2] in {"original.pdf", "original.docx", "render.pdf"}
                        )
                        or (
                            len(path.parts) == 2
                            and path.parts[0] == "library-assets"
                            and re.fullmatch(r"[a-f0-9]{64}\.(pdf|docx|doc|ai|xls)", path.parts[1])
                        )
                        or (
                            len(path.parts) == 2
                            and path.parts[0] == "service-sources"
                            and re.fullmatch(r"[a-f0-9]{64}\.xlsx", path.parts[1])
                        )
                    )
                    if (
                        not allowed
                        or path.is_absolute()
                        or ".." in path.parts
                        or any(char in entry.filename for char in "\\:\x00")
                        or stat.S_ISLNK(entry.external_attr >> 16)
                    ):
                        raise AppError("백업에 허용하지 않는 경로가 있습니다.")
                manifest = BackupManifest.model_validate_json(archive.read("manifest.json"))
                expected = {entry.path for entry in manifest.files}
                if len(expected) != len(manifest.files) or expected != set(names) - {
                    "manifest.json"
                }:
                    raise AppError("백업 파일 목록이 일치하지 않습니다.")
                if "bistos.sqlite3" not in expected:
                    raise AppError("백업에 데이터베이스가 없습니다.")
                for entry in manifest.files:
                    value = archive.read(entry.path)
                    if len(value) != entry.size or digest(value) != entry.sha256:
                        raise AppError("백업 파일 해시가 일치하지 않습니다.")
                    target = stage / entry.path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(value)
        except (zipfile.BadZipFile, KeyError, ValidationError, RuntimeError, OSError) as exc:
            raise AppError("올바른 비스토스 백업 파일이 아닙니다.") from exc

    @staticmethod
    def _validate_database(stage: Path) -> None:
        try:
            with closing(sqlite3.connect(stage / "bistos.sqlite3")) as db:
                db.row_factory = sqlite3.Row
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise AppError("백업 데이터베이스 무결성 검사에 실패했습니다.")
                if db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view')"
                ).fetchone():
                    raise AppError("백업 데이터베이스에 허용하지 않는 구조가 있습니다.")
                users = [
                    User.model_validate_json(row["payload"])
                    for row in db.execute("SELECT payload FROM users")
                ]
                if not any(user.active and "manage_users" in user.capabilities for user in users):
                    raise AppError("백업에 활성 관리자가 없습니다.")
                if db.execute("PRAGMA foreign_key_check").fetchone():
                    raise AppError("백업 데이터 연결이 일치하지 않습니다.")
                BackupService._validate_documents(stage, db)
                BackupService._validate_assets(stage, db)
                BackupService._validate_asset_overlays(db)
                BackupService._validate_metadata(db)
                validate_snapshots(db, stage)
                validate_guides(db)
        except (sqlite3.DatabaseError, ValidationError, OSError) as exc:
            raise AppError("백업 데이터베이스를 검증할 수 없습니다.") from exc

    @staticmethod
    def _validate_metadata(db: sqlite3.Connection) -> None:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='library_document_metadata'"
        ).fetchone():
            return
        for row in db.execute("SELECT document_id,payload FROM library_document_metadata"):
            document = db.execute(
                "SELECT payload FROM documents WHERE id=?",
                (row["document_id"],),
            ).fetchone()
            if not document:
                raise AppError("백업 메타데이터의 연결 문서를 찾을 수 없습니다.")
            merge_document_metadata(
                Document.model_validate_json(document["payload"]),
                row["payload"],
            )

    @staticmethod
    def _validate_assets(stage: Path, db: sqlite3.Connection) -> None:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='library_assets'"
        ).fetchone():
            return
        for row in db.execute("SELECT id,payload FROM library_assets"):
            asset = LibraryAsset.model_validate_json(row["payload"])
            if asset.id != row["id"]:
                raise AppError("백업 첨부 파일 식별 정보가 일치하지 않습니다.")
            if (
                asset.document_id
                and not db.execute(
                    "SELECT 1 FROM documents WHERE id=?", (asset.document_id,)
                ).fetchone()
            ):
                raise AppError("백업 첨부 파일의 연결 문서를 찾을 수 없습니다.")
            asset_path(stage, asset)

    @staticmethod
    def _validate_asset_overlays(db: sqlite3.Connection) -> None:  # noqa: C901
        """Validate additive held-attachment decisions without rewriting raw assets."""
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='library_asset_overlays'"
        ).fetchone():
            return
        for row in db.execute("SELECT asset_id,payload FROM library_asset_overlays"):
            raw_row = db.execute(
                "SELECT payload FROM library_assets WHERE id=?", (row["asset_id"],)
            ).fetchone()
            if not raw_row:
                raise AppError("백업 첨부 overlay의 원본을 찾을 수 없습니다.")
            raw = LibraryAsset.model_validate_json(raw_row["payload"])
            overlay = LibraryAssetOverlay.model_validate_json(row["payload"])
            if overlay.asset_id != raw.id:
                raise AppError("백업 첨부 overlay의 식별 정보가 일치하지 않습니다.")
            if overlay.status == "excluded":
                if overlay.document_id:
                    raise AppError("제외 첨부 overlay에 문서 연결이 있습니다.")
                continue
            if overlay.status != "linked" or not overlay.document_id:
                raise AppError("백업 첨부 overlay 상태가 올바르지 않습니다.")
            target_row = db.execute(
                "SELECT payload FROM documents WHERE id=?", (overlay.document_id,)
            ).fetchone()
            if not target_row:
                raise AppError("백업 첨부 overlay의 연결 문서를 찾을 수 없습니다.")
            target = Document.model_validate_json(target_row["payload"])
            if overlay.product_id and overlay.product_id != target.product_id:
                raise AppError("백업 첨부 overlay 제품과 문서 제품이 다릅니다.")
            if (raw.kind == "service") != (target.kind == "service"):
                raise AppError("서비스 첨부와 연결 문서의 종류가 다릅니다.")

    @staticmethod
    def _validate_documents(stage: Path, db: sqlite3.Connection) -> None:
        for row in db.execute("SELECT * FROM documents"):
            doc = Document.model_validate_json(row["payload"])
            if doc.id != row["id"] or doc.product_id != row["product_id"]:
                raise AppError("백업 문서의 식별 정보가 일치하지 않습니다.")
            verify_document_files(stage, doc, row["original_path"], row["render_path"])
            pages = [
                Page.model_validate_json(item["payload"])
                for item in db.execute(
                    "SELECT payload FROM pages WHERE document_id=? ORDER BY number", (doc.id,)
                )
            ]
            if [page.number for page in pages] != list(range(1, doc.page_count + 1)):
                raise AppError("백업 페이지 순서 또는 수가 일치하지 않습니다.")
            if any(page.document_id != doc.id for page in pages):
                raise AppError("백업 페이지의 문서 ID가 일치하지 않습니다.")

    def _replace(self, stage: Path) -> None:  # noqa: PLR0915
        snapshot_epoch = self.store.epoch()
        with self.store.connection() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        old_db = self.store.data_dir / f"rollback-{uuid.uuid4().hex}.sqlite3"
        old_documents = self.store.data_dir / f"rollback-documents-{uuid.uuid4().hex}"
        documents = self.store.data_dir / "documents"
        replacement_documents = stage / "documents"
        replacement_documents.mkdir(exist_ok=True)
        assets = self.store.data_dir / "library-assets"
        assets.mkdir(exist_ok=True)
        old_assets = self.store.data_dir / f"rollback-assets-{uuid.uuid4().hex}"
        replacement_assets = stage / "library-assets"
        replacement_assets.mkdir(exist_ok=True)
        service_sources = self.store.data_dir / "service-sources"
        service_sources.mkdir(exist_ok=True)
        old_service_sources = self.store.data_dir / f"rollback-service-sources-{uuid.uuid4().hex}"
        replacement_service_sources = stage / "service-sources"
        replacement_service_sources.mkdir(exist_ok=True)
        try:
            self.store.db_path.replace(old_db)
            documents.rename(old_documents)
            assets.rename(old_assets)
            service_sources.rename(old_service_sources)
            shutil.copyfile(stage / "bistos.sqlite3", self.store.db_path)
            replacement_documents.rename(documents)
            replacement_assets.rename(assets)
            replacement_service_sources.rename(service_sources)
            with self.store.connection() as db:
                db.execute("DELETE FROM sessions")
                db.execute("DELETE FROM login_attempts")
                db.execute("UPDATE meta SET value=? WHERE key='epoch'", (str(snapshot_epoch + 1),))
                db.execute(
                    """CREATE TABLE IF NOT EXISTS library_assets
                    (id TEXT PRIMARY KEY,payload TEXT NOT NULL)"""
                )
                db.execute(
                    """CREATE TABLE IF NOT EXISTS library_document_metadata
                    (document_id TEXT PRIMARY KEY REFERENCES documents(id),payload TEXT NOT NULL)"""
                )
                db.execute(
                    """CREATE TABLE IF NOT EXISTS manual_defaults (
                    product_id TEXT NOT NULL REFERENCES products(id),
                    country TEXT NOT NULL,document_id TEXT NOT NULL REFERENCES documents(id),
                    fingerprint TEXT NOT NULL,PRIMARY KEY(product_id,country))"""
                )
            self.store.initialize()
            with self.store.connection() as db:
                initialize_tables(db)
                initialize_guides(db)
        except (OSError, sqlite3.DatabaseError):
            if old_documents.exists() and documents.exists():
                failed = self.store.data_dir / f"failed-restore-documents-{uuid.uuid4().hex}"
                documents.rename(failed)
            if old_db.exists():
                old_db.replace(self.store.db_path)
            if old_documents.exists():
                old_documents.rename(documents)
            if old_assets.exists():
                if assets.exists():
                    failed_assets = (
                        self.store.data_dir / f"failed-restore-assets-{uuid.uuid4().hex}"
                    )
                    assets.rename(failed_assets)
                old_assets.rename(assets)
            if old_service_sources.exists():
                if service_sources.exists():
                    failed_service_sources = (
                        self.store.data_dir / f"failed-restore-service-sources-{uuid.uuid4().hex}"
                    )
                    service_sources.rename(failed_service_sources)
                old_service_sources.rename(service_sources)
            raise AppError("복원에 실패하여 기존 데이터로 되돌렸습니다.") from None
        self.store.bounded_path(old_db.relative_to(self.store.data_dir).as_posix()).unlink()
        checked_old_documents = self.store.bounded_path(
            old_documents.relative_to(self.store.data_dir).as_posix()
        )
        shutil.rmtree(checked_old_documents)
        checked_old_assets = self.store.bounded_path(
            old_assets.relative_to(self.store.data_dir).as_posix()
        )
        shutil.rmtree(checked_old_assets)
        checked_old_service_sources = self.store.bounded_path(
            old_service_sources.relative_to(self.store.data_dir).as_posix()
        )
        shutil.rmtree(checked_old_service_sources)
