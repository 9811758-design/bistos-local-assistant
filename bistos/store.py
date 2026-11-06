"""SQLite storage with request-time authorization and serialized mutations."""

import json
import re
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

from bistos.document_integrity import document_paths, verify_format, verify_hash
from bistos.errors import AppError
from bistos.library import answer_eligible, asset_path, merge_document_metadata
from bistos.models import (
    Attachment,
    Document,
    LibraryAsset,
    LibraryAssetOverlay,
    Page,
    Product,
    User,
)


class Store:
    """Own the database and lock; each operation opens a short-lived connection."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir.resolve()
        self.db_path = self.data_dir / "bistos.sqlite3"
        self.lock = RLock()

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection]:
        with self.lock:
            connection = sqlite3.connect(self.db_path, timeout=30)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            try:
                with connection:
                    yield connection
            finally:
                connection.close()

    def initialize(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "documents").mkdir(exist_ok=True)
        (self.data_dir / "backups").mkdir(exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
                INSERT OR IGNORE INTO meta VALUES ('epoch','0');
                CREATE TABLE IF NOT EXISTS products (id TEXT PRIMARY KEY,name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,payload TEXT NOT NULL,
                    password_hash TEXT NOT NULL,salt TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),
                    csrf TEXT NOT NULL,expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS login_attempts (
                    username TEXT PRIMARY KEY,failures INTEGER NOT NULL,locked_until REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,product_id TEXT NOT NULL REFERENCES products(id),
                    payload TEXT NOT NULL,original_path TEXT NOT NULL,render_path TEXT NOT NULL,
                    UNIQUE(product_id,original_path));
                CREATE TABLE IF NOT EXISTS pages (
                    document_id TEXT NOT NULL REFERENCES documents(id),
                    number INTEGER NOT NULL,payload TEXT NOT NULL,
                    PRIMARY KEY(document_id,number));
                CREATE TABLE IF NOT EXISTS library_assets (
                    id TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS library_document_metadata (
                    document_id TEXT PRIMARY KEY REFERENCES documents(id),payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS manual_defaults (
                    product_id TEXT NOT NULL REFERENCES products(id),
                    country TEXT NOT NULL,document_id TEXT NOT NULL REFERENCES documents(id),
                    fingerprint TEXT NOT NULL,
                    PRIMARY KEY(product_id,country));
                CREATE TABLE IF NOT EXISTS availability_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    document_ids TEXT NOT NULL,
                    outcomes TEXT NOT NULL,
                    updated INTEGER NOT NULL,
                    unchanged INTEGER NOT NULL,
                    excluded INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS document_trash_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    trashed INTEGER NOT NULL,
                    document_ids TEXT NOT NULL,
                    outcomes TEXT NOT NULL,
                    reason TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS library_asset_overlays (
                    asset_id TEXT PRIMARY KEY REFERENCES library_assets(id),
                    payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS library_asset_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    payload TEXT NOT NULL);
            """)

    @staticmethod
    def record_availability_audit(
        db: sqlite3.Connection,
        actor_user_id: str,
        enabled: bool,
        document_ids: list[str],
        outcomes: list[dict[str, object]],
        updated: int,
        unchanged: int,
        excluded: int,
    ) -> None:
        db.execute(
            """INSERT INTO availability_audit
            (actor_user_id,created_at,enabled,document_ids,outcomes,updated,unchanged,excluded)
            VALUES (?,?,?,?,?,?,?,?)""",
            (
                actor_user_id,
                datetime.now(UTC).isoformat(),
                int(enabled),
                json.dumps(document_ids, ensure_ascii=False),
                json.dumps(outcomes, ensure_ascii=False),
                updated,
                unchanged,
                excluded,
            ),
        )

    @staticmethod
    def record_trash_audit(
        db: sqlite3.Connection,
        actor_user_id: str,
        trashed: bool,
        document_ids: list[str],
        outcomes: list[dict[str, object]],
        reason: str,
    ) -> None:
        db.execute(
            """INSERT INTO document_trash_audit
            (actor_user_id,created_at,trashed,document_ids,outcomes,reason)
            VALUES (?,?,?,?,?,?)""",
            (
                actor_user_id,
                datetime.now(UTC).isoformat(),
                int(trashed),
                json.dumps(document_ids, ensure_ascii=False),
                json.dumps(outcomes, ensure_ascii=False),
                reason,
            ),
        )

    def epoch(self) -> int:
        with self.connection() as db:
            row = db.execute("SELECT value FROM meta WHERE key='epoch'").fetchone()
            return int(row["value"]) if row else 0

    @staticmethod
    def bump_epoch(db: sqlite3.Connection) -> None:
        db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='epoch'")

    def products(self) -> list[Product]:
        with self.connection() as db:
            return [
                Product.model_validate(dict(row))
                for row in db.execute("SELECT id,name FROM products ORDER BY id")
            ]

    def available_products(self, user: User) -> list[Product]:
        """Products with at least one currently readable, answer-eligible document."""
        current = self.user(user.id)
        visible = {doc.product_id for doc in self.documents(current) if answer_eligible(doc)}
        return [product for product in self.products() if product.id in visible]

    def add_product(self, product_id: str, name: str) -> Product:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", product_id):
            raise AppError("제품 ID는 영문·숫자·점·밑줄·하이픈 80자 이내여야 합니다.")
        if not name.strip() or len(name) > 150:
            raise AppError("제품 이름을 150자 이내로 입력하세요.")
        product = Product(id=product_id, name=name.strip())
        with self.connection() as db:
            existing = db.execute(
                "SELECT id,name FROM products WHERE id=?", (product_id,)
            ).fetchone()
            if existing:
                return Product.model_validate(dict(existing))
            db.execute("INSERT INTO products VALUES (?,?)", (product.id, product.name))
        return product

    def user(self, user_id: str) -> User:
        with self.connection() as db:
            row = db.execute("SELECT payload FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise AppError("사용자 정보를 찾을 수 없습니다.", 401)
        return User.model_validate_json(row["payload"])

    def users(self) -> list[User]:
        with self.connection() as db:
            return [
                User.model_validate_json(row["payload"])
                for row in db.execute("SELECT payload FROM users ORDER BY username")
            ]

    def require(self, user: User, capability: str) -> User:
        current = self.user(user.id)
        if not current.active or capability not in current.capabilities:
            raise AppError("이 작업에 필요한 권한이 없습니다.", 403)
        return current

    def documents(self, user: User) -> list[Document]:
        current = self.user(user.id)
        if not current.active:
            raise AppError("사용할 수 없는 계정입니다.", 403)
        with self.connection() as db:
            metadata = {
                row["document_id"]: row["payload"]
                for row in db.execute("SELECT document_id,payload FROM library_document_metadata")
            }
            documents = [
                merge_document_metadata(
                    Document.model_validate_json(row["payload"]), metadata.get(row["id"], "{}"),
                )
                for row in db.execute("SELECT id,payload FROM documents ORDER BY rowid DESC")
            ]
        return [doc for doc in documents if self._can_read_metadata(doc, current)]

    @staticmethod
    def _can_read_metadata(doc: Document, user: User) -> bool:
        return "manage_documents" in user.capabilities or (
            "ask" in user.capabilities
            and doc.enabled
            and not doc.trashed
            and doc.state == "published"
            and (doc.kind != "service" or "read_service" in user.capabilities)
        )

    def document(self, doc_id: str, user: User) -> Document:
        current = self.user(user.id)
        with self.connection() as db:
            row = db.execute("SELECT payload FROM documents WHERE id=?", (doc_id,)).fetchone()
            metadata = db.execute(
                "SELECT payload FROM library_document_metadata WHERE document_id=?", (doc_id,),
            ).fetchone()
        if not row:
            raise AppError("문서를 찾을 수 없습니다.", 404)
        document = Document.model_validate_json(row["payload"])
        if metadata:
            document = merge_document_metadata(document, metadata["payload"])
        if document.id != doc_id:
            raise AppError("문서 식별 정보가 일치하지 않습니다.")
        if not current.active or not self._can_read_metadata(document, current):
            raise AppError("문서를 찾을 수 없거나 접근 권한이 없습니다.", 404)
        return document

    def _readable_document(self, doc_id: str, user: User) -> Document:
        current = self.user(user.id)
        document = self.document(doc_id, current)
        if document.kind == "service" and "read_service" not in current.capabilities:
            raise AppError("서비스 원문을 볼 수 있는 권한이 없습니다.", 403)
        return document

    def pages(self, doc_id: str, user: User) -> list[Page]:
        self._readable_document(doc_id, user)
        with self.connection() as db:
            return [
                Page.model_validate_json(row["payload"])
                for row in db.execute(
                    "SELECT payload FROM pages WHERE document_id=? ORDER BY number", (doc_id,)
                )
            ]

    def searchable_pages(self, user: User, product_id: str) -> list[tuple[Document, Page]]:
        current = self.require(user, "ask")
        result: list[tuple[Document, Page]] = []
        with self.lock:
            for doc in self.documents(current):
                if doc.product_id != product_id or not answer_eligible(doc):
                    continue
                if doc.kind == "service" and "read_service" not in current.capabilities:
                    continue
                result.extend((doc, page) for page in self.pages(doc.id, current))
        return result

    def save_library_asset(self, asset: LibraryAsset) -> None:
        asset_path(self.data_dir, asset)
        with self.connection() as db:
            prior = db.execute(
                "SELECT payload FROM library_assets WHERE id=?", (asset.id,),
            ).fetchone()
            if prior:
                if LibraryAsset.model_validate_json(prior["payload"]) != asset:
                    raise AppError("같은 첨부 식별자의 다른 자료가 이미 등록되어 있습니다.", 409)
                return
            db.execute(
                "INSERT INTO library_assets VALUES (?,?)", (asset.id, asset.model_dump_json()),
            )

    def library_catalog(self, user: User) -> list[LibraryAsset]:
        actor = self.require(user, "manage_documents")
        with self.connection() as db:
            catalog_query = (
                "SELECT a.payload,o.payload AS overlay FROM library_assets a "
                "LEFT JOIN library_asset_overlays o ON o.asset_id=a.id ORDER BY a.id"
            )
            rows = list(db.execute(
                catalog_query
            ))
            assets = [self._apply_asset_overlay(
                LibraryAsset.model_validate_json(row["payload"]), row["overlay"]
            ) for row in rows]
        return [
            asset for asset in assets
            if asset.kind != "service" or "read_service" in actor.capabilities
        ]

    @staticmethod
    def _apply_asset_overlay(asset: LibraryAsset, overlay: str | None) -> LibraryAsset:
        if not overlay:
            return asset
        change = LibraryAssetOverlay.model_validate_json(overlay)
        return asset.model_copy(update={
            "document_id": change.document_id,
            "status": change.status,
            "reason": change.reason,
        })

    def review_library_asset(  # noqa: C901
        self,
        user: User,
        asset_id: str,
        action: str,
        document_id: str = "",
        product_id: str = "",
        reason: str = "",
    ) -> LibraryAsset:
        actor = self.require(user, "manage_documents")
        if action not in {"link", "exclude"}:
            raise AppError("지원하지 않는 첨부 검토 작업입니다.", 422)
        with self.lock, self.connection() as db:
            row = db.execute(
                "SELECT payload FROM library_assets WHERE id=?", (asset_id,)
            ).fetchone()
            if not row:
                raise AppError("보류 첨부를 찾을 수 없습니다.", 404)
            raw = LibraryAsset.model_validate_json(row["payload"])
            if raw.kind == "service":
                self.require(actor, "read_service")
            existing = db.execute(
                "SELECT payload FROM library_asset_overlays WHERE asset_id=?", (asset_id,)
            ).fetchone()
            if existing:
                effective = self._apply_asset_overlay(raw, existing["payload"])
                decision = LibraryAssetOverlay.model_validate_json(existing["payload"])
                expected_status = "excluded" if action == "exclude" else "linked"
                if effective.status == expected_status and (
                    action == "exclude"
                    or (
                        effective.document_id == document_id
                        and decision.product_id == product_id
                    )
                ):
                    return effective
                raise AppError("이미 검토된 첨부는 새 결정으로 덮어쓸 수 없습니다.", 409)
            if action == "link":
                if not document_id or not product_id:
                    raise AppError("연결할 제품과 문서를 지정하세요.", 422)
                docrow = db.execute(
                    "SELECT payload FROM documents WHERE id=?", (document_id,)
                ).fetchone()
                if not docrow:
                    raise AppError("연결할 문서를 찾을 수 없습니다.", 404)
                doc = Document.model_validate_json(docrow["payload"])
                if doc.product_id != product_id:
                    raise AppError("첨부와 문서의 제품이 일치하지 않습니다.", 409)
                if (raw.kind == "service") != (doc.kind == "service"):
                    raise AppError("서비스 첨부와 연결 문서의 종류가 다릅니다.", 409)
                if doc.trashed:
                    raise AppError("휴지통 문서에는 첨부할 수 없습니다.", 409)
                status = "linked"
            else:
                status = "excluded"
                document_id = ""
            overlay = LibraryAssetOverlay(
                asset_id=asset_id, document_id=document_id, status=status,
                product_id=product_id, reason=reason.strip(),
                reviewed_at=datetime.now(UTC).isoformat(), reviewed_by=actor.id,
            )
            db.execute(
                "INSERT INTO library_asset_overlays VALUES (?,?)",
                (asset_id, overlay.model_dump_json()),
            )
            db.execute(
                (
                    "INSERT INTO library_asset_audit(actor_user_id,created_at,asset_id,"
                    "action,payload) "
                    "VALUES (?,?,?,?,?)"
                ),
                (actor.id, overlay.reviewed_at, asset_id, action, overlay.model_dump_json()),
            )
            return self._apply_asset_overlay(raw, overlay.model_dump_json())

    def document_attachments(self, doc_id: str, user: User) -> list[Attachment]:
        self._readable_document(doc_id, user)
        with self.connection() as db:
            attachment_query = (
                "SELECT a.payload,o.payload AS overlay FROM library_assets a "
                "LEFT JOIN library_asset_overlays o ON o.asset_id=a.id"
            )
            rows = list(db.execute(
                attachment_query
            ))
            assets = [self._apply_asset_overlay(
                LibraryAsset.model_validate_json(row["payload"]), row["overlay"]
            ) for row in rows]
        return [
            Attachment(id=asset.id, name=asset.name, sha256=asset.sha256, role=asset.role)
            for asset in assets if asset.document_id == doc_id
        ]

    def library_asset(self, asset_id: str, user: User) -> tuple[LibraryAsset, Path]:
        actor = self.user(user.id)
        if not actor.active:
            raise AppError("첨부 파일을 볼 수 있는 권한이 없습니다.", 403)
        with self.connection() as db:
            row = db.execute(
                (
                    "SELECT a.payload,o.payload AS overlay FROM library_assets a "
                    "LEFT JOIN library_asset_overlays o ON o.asset_id=a.id WHERE a.id=?"
                ),
                (asset_id,),
            ).fetchone()
        if not row:
            raise AppError("첨부 파일을 찾을 수 없습니다.", 404)
        asset = self._apply_asset_overlay(
            LibraryAsset.model_validate_json(row["payload"]), row["overlay"]
        )
        if asset.kind == "service":
            self.require(actor, "read_service")
        if asset.document_id:
            self._readable_document(asset.document_id, actor)
        else:
            self.require(actor, "manage_documents")
        if asset.status in {"pending", "excluded", "failed", "quarantined"}:
            self.require(actor, "manage_documents")
        return asset, asset_path(self.data_dir, asset)

    def bounded_path(self, relative: str) -> Path:
        path = (self.data_dir / relative).resolve()
        if not path.is_relative_to(self.data_dir) or path == self.data_dir:
            raise AppError("저장소 밖의 경로는 허용하지 않습니다.")
        return path

    def file_path(self, doc_id: str, user: User, original: bool = False) -> Path:
        document = self._readable_document(doc_id, user)
        with self.connection() as db:
            row = db.execute(
                "SELECT original_path,render_path FROM documents WHERE id=?", (doc_id,)
            ).fetchone()
        if not row:
            raise AppError("문서를 찾을 수 없습니다.", 404)
        original_path, render_path = document_paths(
            self.data_dir, document, row["original_path"], row["render_path"]
        )
        path = original_path if original else render_path
        verify_hash(path, document.original_sha256 if original else document.render_sha256)
        verify_format(path)
        return path

    def save_document(
        self,
        document: Document,
        pages: list[Page],
        original_path: Path,
        render_path: Path,
    ) -> None:
        original_relative = original_path.resolve().relative_to(self.data_dir).as_posix()
        render_relative = render_path.resolve().relative_to(self.data_dir).as_posix()
        document_paths(self.data_dir, document, original_relative, render_relative)
        with self.connection() as db:
            db.execute(
                "INSERT INTO documents VALUES (?,?,?,?,?)",
                (
                    document.id,
                    document.product_id,
                    document.model_dump_json(),
                    original_relative,
                    render_relative,
                ),
            )
            db.executemany(
                "INSERT INTO pages VALUES (?,?,?)",
                [(page.document_id, page.number, page.model_dump_json()) for page in pages],
            )

    def settings_json(self, key: str) -> str | None:
        with self.connection() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def set_settings_json(self, key: str, value: str) -> None:
        json.loads(value)
        with self.connection() as db:
            db.execute(
                "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
