from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware

from bistos.errors import AppError
from bistos.routes_auth import Access, auth_router
from bistos.routes_documents import document_router
from bistos.routes_questions import question_router
from bistos.routes_service_guides import service_guides_router
from bistos.routes_service_history import service_history_router
from bistos.services import Services
from bistos.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    chosen = settings or Settings.load()
    services = Services.build(chosen)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        yield
        services.close()

    app = FastAPI(
        title="비스토스 제품도우미",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    allowed_hosts = chosen.allowed_hosts
    if chosen.tunnel_host is not None and chosen.tunnel_host not in allowed_hosts:
        allowed_hosts = (*allowed_hosts, chosen.tunnel_host)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    access = Access(services)

    @app.exception_handler(AppError)
    async def app_error(_request: Request, error: AppError) -> JSONResponse:
        return JSONResponse({"detail": error.detail}, status_code=error.status_code)

    @app.middleware("http")
    async def boundary(  # noqa: PLR0911, PLR0912
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        raw_host = request.headers.get("host")
        if chosen.tunnel_host is not None and raw_host != chosen.tunnel_host:
            parsed_host = urlsplit(f"//{raw_host or ''}").hostname
            if parsed_host == chosen.tunnel_host:
                return JSONResponse({"detail": "허용되지 않은 터널 호스트입니다."}, status_code=403)
        if chosen.tunnel_host is not None and raw_host == chosen.tunnel_host:
            peer = request.client.host if request.client is not None else None
            if peer not in {"127.0.0.1", "::1"}:
                return JSONResponse({"detail": "허용되지 않은 터널 요청입니다."}, status_code=403)
            request.scope["scheme"] = "https"
            request.state.bistos_tunnel = True
        else:
            request.state.bistos_tunnel = False
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.netloc != request.headers.get("host") or parsed.scheme != request.url.scheme:
                return JSONResponse({"detail": "허용되지 않은 요청 출처입니다."}, status_code=403)
        if (
            request.client
            and request.client.host not in {"127.0.0.1", "::1", "testclient"}
            and request.url.scheme != "https"
        ):
            return JSONResponse({"detail": "사내 접속에는 HTTPS가 필요합니다."}, status_code=403)
        limit = 32768
        if request.url.path in {"/api/documents", "/api/restore", "/api/service-history/import"}:
            limit = (8193 if request.url.path == "/api/restore" else 101) * 1024 * 1024
            if request.url.path == "/api/service-history/import":
                limit = 26 * 1024 * 1024
            if request.method == "POST" and "content-length" not in request.headers:
                return JSONResponse({"detail": "파일 크기를 확인할 수 없습니다."}, status_code=411)
        size = request.headers.get("content-length", "0")
        if not size.isdecimal() or int(size) > limit:
            return JSONResponse(
                {"detail": "요청 크기가 허용 범위를 초과했습니다."}, status_code=413
            )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(auth_router(services, access))
    app.include_router(document_router(services, access))
    app.include_router(question_router(services, access))
    app.include_router(service_history_router(services, access))
    app.include_router(service_guides_router(services, access))

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(chosen.root / "static" / "index.html")

    app.mount("/static", StaticFiles(directory=chosen.root / "static", check_dir=False))
    return app
