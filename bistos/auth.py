"""Password-derived credentials, hashed sessions, and account authorization."""

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import uuid

from pydantic import TypeAdapter, ValidationError

from bistos.errors import AppError
from bistos.models import Capability, User
from bistos.store import Store


def password_hash(password: str, salt: str) -> str:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=bytes.fromhex(salt),
        n=16384,
        r=8,
        p=1,
        dklen=32,
    ).hex()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Auth:
    def __init__(self, store: Store) -> None:
        self.store = store

    def setup_required(self) -> bool:
        return not self.store.users()

    def setup(self, username: str, password: str, display_name: str) -> User:
        with self.store.lock:
            if not self.setup_required():
                raise AppError("최초 관리자 설정은 이미 완료되었습니다.", 409)
            return self._create(
                username, password, display_name, ["ask", "manage_documents", "manage_users"]
            )

    def _create(
        self,
        username: str,
        password: str,
        display_name: str,
        capabilities: list[Capability],
    ) -> User:
        normalized = username.strip().casefold()
        if not re.fullmatch(r"[a-z0-9_.-]{3,60}", normalized):
            raise AppError("사용자 이름은 영문·숫자·점·밑줄·하이픈 3~60자로 입력하세요.")
        if len(password) < 6 or len(password.encode("utf-8")) > 256:
            raise AppError("비밀번호는 6자 이상, UTF-8 256바이트 이내로 입력하세요.")
        if not display_name.strip() or len(display_name) > 80:
            raise AppError("표시 이름을 80자 이내로 입력하세요.")
        parsed = self._capabilities(capabilities)
        user = User(
            id=uuid.uuid4().hex,
            username=normalized,
            display_name=display_name.strip(),
            capabilities=parsed,
        )
        salt = secrets.token_hex(16)
        digest = password_hash(password, salt)
        try:
            with self.store.connection() as db:
                db.execute(
                    "INSERT INTO users VALUES (?,?,?,?,?)",
                    (user.id, user.username, user.model_dump_json(), digest, salt),
                )
        except sqlite3.IntegrityError as exc:
            raise AppError("이미 사용 중인 사용자 이름입니다.", 409) from exc
        return user

    @staticmethod
    def _capabilities(capabilities: list[Capability]) -> list[Capability]:
        try:
            parsed = TypeAdapter(list[Capability]).validate_python(capabilities)
        except ValidationError as exc:
            raise AppError("알 수 없는 권한입니다.") from exc
        return list(dict.fromkeys(parsed))

    def create_user(
        self,
        actor: User,
        username: str,
        password: str,
        display_name: str,
        capabilities: list[Capability],
    ) -> User:
        with self.store.lock:
            self.store.require(actor, "manage_users")
            return self._create(username, password, display_name, capabilities)

    def login(self, username: str, password: str) -> tuple[User, str, str]:
        normalized = username.strip().casefold()
        if len(normalized) > 60 or len(password.encode("utf-8")) > 256:
            raise AppError("사용자 이름 또는 비밀번호가 올바르지 않습니다.", 401)
        now = time.time()
        with self.store.lock:
            with self.store.connection() as db:
                attempt = db.execute(
                    "SELECT failures,locked_until FROM login_attempts WHERE username=?",
                    (normalized,),
                ).fetchone()
                if attempt and float(attempt["locked_until"]) > now:
                    raise AppError("로그인 시도가 많습니다. 잠시 후 다시 시도하세요.", 429)
                row = db.execute("SELECT * FROM users WHERE username=?", (normalized,)).fetchone()
            salt = str(row["salt"]) if row else "0" * 32
            actual = password_hash(password, salt)
            valid = row is not None and hmac.compare_digest(actual, str(row["password_hash"]))
            user = User.model_validate_json(row["payload"]) if row else None
            if not valid or user is None or not user.active:
                failures = (
                    int(attempt["failures"]) + 1
                    if attempt and float(attempt["locked_until"]) == 0
                    else 1
                )
                with self.store.connection() as db:
                    db.execute(
                        "INSERT OR REPLACE INTO login_attempts VALUES (?,?,?)",
                        (normalized, failures, now + 60 if failures >= 5 else 0),
                    )
                raise AppError("사용자 이름 또는 비밀번호가 올바르지 않습니다.", 401)
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            with self.store.connection() as db:
                db.execute("DELETE FROM login_attempts WHERE username=?", (normalized,))
                db.execute("DELETE FROM sessions WHERE expires<?", (now,))
                db.execute(
                    "INSERT INTO sessions VALUES (?,?,?,?)",
                    (token_hash(token), user.id, csrf, now + 12 * 60 * 60),
                )
            return user, token, csrf

    def session(self, token: str) -> tuple[User, str]:
        with self.store.connection() as db:
            row = db.execute(
                "SELECT user_id,csrf,expires FROM sessions WHERE token_hash=?", (token_hash(token),)
            ).fetchone()
        if not row or float(row["expires"]) <= time.time():
            raise AppError("다시 로그인하세요.", 401)
        user = self.store.user(row["user_id"])
        if not user.active:
            raise AppError("비활성화된 계정입니다.", 401)
        return user, str(row["csrf"])

    def logout(self, token: str) -> None:
        with self.store.connection() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))

    def update_user(
        self,
        actor: User,
        user_id: str,
        capabilities: list[Capability],
        active: bool,
    ) -> User:
        with self.store.lock:
            self.store.require(actor, "manage_users")
            user = self.store.user(user_id)
            parsed = self._capabilities(capabilities)
            removes_admin = "manage_users" in user.capabilities and (
                not active or "manage_users" not in parsed
            )
            admins = [
                item
                for item in self.store.users()
                if item.active and "manage_users" in item.capabilities and item.id != user_id
            ]
            if user.active and removes_admin and not admins:
                raise AppError("마지막 관리자는 비활성화하거나 관리자 권한을 제거할 수 없습니다.")
            updated = user.model_copy(update={"capabilities": parsed, "active": active})
            with self.store.connection() as db:
                db.execute(
                    "UPDATE users SET payload=? WHERE id=?", (updated.model_dump_json(), user_id)
                )
                self.store.bump_epoch(db)
            return updated

    def delete_user(self, actor: User, user_id: str) -> None:
        with self.store.lock:
            self.store.require(actor, "manage_users")
            user = next((item for item in self.store.users() if item.id == user_id), None)
            if user is None:
                raise AppError("삭제할 계정을 찾을 수 없습니다.", 404)
            if user.active and "manage_users" in user.capabilities and not any(
                item.active and "manage_users" in item.capabilities and item.id != user_id
                for item in self.store.users()
            ):
                raise AppError("마지막 관리자 계정은 삭제할 수 없습니다.")
            if actor.id == user_id:
                raise AppError("현재 로그인한 본인 계정은 삭제할 수 없습니다.")
            with self.store.connection() as db:
                db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
                db.execute("DELETE FROM login_attempts WHERE username=?", (user.username,))
                db.execute("DELETE FROM users WHERE id=?", (user_id,))
                self.store.bump_epoch(db)
