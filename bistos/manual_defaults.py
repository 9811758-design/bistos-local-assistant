"""Explicit administrator baselines, separate from approval and validity metadata."""

import hashlib
import json
import sqlite3
from contextlib import AbstractContextManager
from typing import Protocol

from bistos.errors import AppError
from bistos.library import answer_eligible, matches_filters
from bistos.models import Document, ManualDefault, Product, SearchFilters, User


class DefaultStore(Protocol):
    def connection(self) -> AbstractContextManager[sqlite3.Connection]: ...
    def user(self, user_id: str) -> User: ...
    def products(self) -> list[Product]: ...
    def document(self, doc_id: str, user: User) -> Document: ...


def fingerprint(document: Document) -> str:
    fields = {
        "id", "product_id", "country", "kind", "revision", "language",
        "original_sha256", "render_sha256", "state", "approval_status", "validity",
    }
    serialized = json.dumps(document.model_dump(include=fields), sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()


class ManualDefaults:
    def __init__(self, store: DefaultStore) -> None:
        self.store = store

    def entries(self, user: User) -> list[ManualDefault]:
        actor = self.store.user(user.id)
        if not actor.active or not ({"ask", "manage_documents"} & set(actor.capabilities)):
            raise AppError("기준 매뉴얼을 볼 권한이 없습니다.", 403)
        with self.store.connection() as db:
            rows = db.execute(
                """SELECT product_id,country,document_id,fingerprint FROM manual_defaults
                ORDER BY product_id,country"""
            ).fetchall()
        result: list[ManualDefault] = []
        for row in rows:
            try:
                doc = self.store.document(row["document_id"], actor)
            except AppError:
                continue
            active = (
                answer_eligible(doc) and doc.kind in {"user", "quick"}
                and doc.product_id == row["product_id"]
                and doc.country.casefold() == str(row["country"]).casefold()
                and fingerprint(doc) == row["fingerprint"]
            )
            result.append(ManualDefault(
                product_id=row["product_id"], country=row["country"],
                document_id=doc.id, active=active,
            ))
        return result

    def set(
        self, user: User, product_id: str, country: str, document_id: str,
    ) -> list[ManualDefault]:
        # Hold the store transaction/lock through authorization and validation.
        with self.store.connection() as db:
            actor = self.store.user(user.id)
            if not actor.active or "manage_documents" not in actor.capabilities:
                raise AppError("기준 매뉴얼은 문서 관리자만 지정할 수 있습니다.", 403)
            if product_id not in {product.id for product in self.store.products()}:
                raise AppError("등록된 제품을 선택해 주세요.")
            market = country.strip().upper()
            if not document_id:
                db.execute(
                    "DELETE FROM manual_defaults WHERE product_id=? AND country=?",
                    (product_id, market),
                )
            else:
                if not market:
                    raise AppError("대상 국가가 확인된 문서만 기준 매뉴얼로 지정할 수 있습니다.")
                doc = self.store.document(document_id, actor)
                if (
                    doc.product_id != product_id or doc.country.strip().upper() != market
                    or not answer_eligible(doc) or doc.kind not in {"user", "quick"}
                ):
                    raise AppError("같은 제품·국가의 게시된 사용자·퀵 매뉴얼만 지정할 수 있습니다.")
                db.execute(
                    """INSERT INTO manual_defaults VALUES(?,?,?,?)
                    ON CONFLICT(product_id,country) DO UPDATE SET
                    document_id=excluded.document_id,fingerprint=excluded.fingerprint""",
                    (product_id, market, doc.id, fingerprint(doc)),
                )
        return self.entries(actor)

    def resolve(self, user: User, product_id: str, filters: SearchFilters) -> SearchFilters:
        # An explicit document or revision always wins over the administrator default.
        if filters.document_id or filters.revision:
            return filters
        choices: list[Document] = []
        for entry in self.entries(user):
            if not entry.active or not entry.country or entry.product_id != product_id:
                continue
            doc = self.store.document(entry.document_id, user)
            if matches_filters(doc, filters):
                choices.append(doc)
        # Multiple markets are deliberately left to the user, never ranked by revision.
        if len(choices) != 1:
            return filters
        doc = choices[0]
        return filters.model_copy(update={"document_id": doc.id, "country": doc.country})
