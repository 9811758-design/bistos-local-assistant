import math
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from bistos.api_models import (
    Credentials,
    Ok,
    SessionView,
    SetupInput,
    StatusView,
    UserInput,
    UserUpdate,
)
from bistos.errors import AppError
from bistos.models import User
from bistos.services import Services


class _TunnelLoginLimiter:
    _window_seconds = 60.0
    _max_attempts = 20

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.Lock()
        self._attempts: deque[float] = deque()
        self._clock = clock

    def allow(self) -> tuple[bool, int]:
        with self._lock:
            now = self._clock()
            cutoff = now - self._window_seconds
            while self._attempts and self._attempts[0] <= cutoff:
                self._attempts.popleft()
            if len(self._attempts) >= self._max_attempts:
                retry_after = max(1, math.ceil(self._attempts[0] + self._window_seconds - now))
                return False, retry_after
            self._attempts.append(now)
            return True, 0

class Access:
    def __init__(self, services: Services) -> None:
        self.services = services

    def session(self, request: Request) -> SessionView:
        token = request.cookies.get("bistos_session", "")
        user, csrf = self.services.auth.session(token)
        return SessionView(user=user, csrf=csrf)

    def read(self, request: Request) -> User:
        return self.session(request).user

    def write(self, request: Request) -> User:
        session = self.session(request)
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), session.csrf):
            raise AppError("화면을 새로고침한 뒤 다시 시도해 주세요.", 403)
        return session.user

    def require(self, capability: str) -> Callable[[Request], User]:
        def dependency(request: Request) -> User:
            user = self.write(request)
            if capability not in user.capabilities:
                raise AppError("이 작업에 필요한 권한이 없습니다.", 403)
            return user

        return dependency


def auth_router(services: Services, access: Access) -> APIRouter:
    router = APIRouter(prefix="/api")
    tunnel_login_limiter = _TunnelLoginLimiter()

    @router.get("/status")
    def status() -> StatusView:
        model = services.answers.model_status()
        return StatusView(
            setup_required=services.auth.setup_required(),
            model_ready=model.ready,
            model_name=model.name,
            generation_enabled=model.enabled,
        )

    @router.post("/setup", status_code=201)
    def setup(payload: SetupInput, request: Request) -> User:
        if getattr(request.state, "bistos_tunnel", False):
            raise AppError("최초 계정은 운영 PC에서 만들어 주세요.", 403)
        if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
            raise AppError("최초 계정은 운영 PC에서 만들어 주세요.", 403)
        return services.auth.setup(payload.username, payload.password, payload.display_name)

    @router.post("/login")
    def login(payload: Credentials, request: Request, response: Response) -> SessionView:
        if getattr(request.state, "bistos_tunnel", False):
            allowed, retry_after = tunnel_login_limiter.allow()
            if not allowed:
                raise HTTPException(
                    status_code=429,
                    detail="임시 접속 로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.",
                    headers={"Retry-After": str(retry_after)},
                )
        user, token, csrf = services.auth.login(payload.username, payload.password)
        response.set_cookie(
            "bistos_session",
            token,
            max_age=8 * 3600,
            httponly=True,
            secure=services.settings.secure_cookie
            or getattr(request.state, "bistos_tunnel", False),
            samesite="strict",
        )
        return SessionView(user=user, csrf=csrf)

    @router.get("/me")
    def me(request: Request) -> SessionView:
        return access.session(request)

    @router.post("/logout")
    def logout(
        request: Request, response: Response, _user: Annotated[User, Depends(access.write)]
    ) -> Ok:
        services.auth.logout(request.cookies.get("bistos_session", ""))
        response.delete_cookie(
            "bistos_session",
            secure=services.settings.secure_cookie
            or getattr(request.state, "bistos_tunnel", False),
            samesite="strict",
        )
        return Ok()

    @router.get("/users")
    def users(user: Annotated[User, Depends(access.read)]) -> list[User]:
        if "manage_users" not in user.capabilities:
            raise AppError("계정 관리 권한이 없습니다.", 403)
        return services.store.users()

    @router.post("/users", status_code=201)
    def create_user(
        payload: UserInput, user: Annotated[User, Depends(access.require("manage_users"))]
    ) -> User:
        return services.auth.create_user(
            user,
            payload.username,
            payload.password,
            payload.display_name,
            payload.capabilities,
        )

    @router.patch("/users/{user_id}")
    def update_user(
        user_id: str,
        payload: UserUpdate,
        user: Annotated[User, Depends(access.require("manage_users"))],
    ) -> User:
        return services.auth.update_user(user, user_id, payload.capabilities, payload.active)

    @router.delete("/users/{user_id}")
    def delete_user(
        user_id: str, user: Annotated[User, Depends(access.require("manage_users"))]
    ) -> Ok:
        services.auth.delete_user(user, user_id)
        return Ok()

    return router
