import logging
import re

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.backup_state import is_backup_in_progress
from app.core.config import get_settings
from app.core.ip_block import is_ip_blocked
from app.core.logging import configure_logging
from app.core.platform_settings import get_platform_settings
from app.core.rate_limit import build_rate_limit_message, check_rate_limit, get_client_ip
from app.db.session import get_sessionmaker


def _append_cors_headers(response: JSONResponse, origin: str, allow_origin_regex: str) -> JSONResponse:
    if origin and re.match(allow_origin_regex, origin):
        response.headers.setdefault("Access-Control-Allow-Origin", origin)
        response.headers.setdefault("Access-Control-Allow-Credentials", "true")
        vary = response.headers.get("Vary", "")
        if "Origin" not in vary:
            response.headers["Vary"] = f"{vary}, Origin".strip(", ").strip()
    return response


def create_app() -> FastAPI:
    settings = get_settings()
    environment = settings.environment.strip().lower()
    is_test_env = environment == "test"
    is_local_env = environment in {"local", "development", "dev"}
    allow_origin_regex = (
        r"^https?://("
        r"localhost|127\.0\.0\.1|76\.13\.112\.16|"
        r"10(?:\.\d{1,3}){3}|"
        r"192\.168(?:\.\d{1,3}){2}|"
        r"172\.(?:1[6-9]|2\d|3[0-1])(?:\.\d{1,3}){2}|"
        r"[A-Za-z0-9-]+\.local|"
        r"floussy\.online|www\.floussy\.online|api\.floussy\.online|"
        r"7sabek\.ma|www\.7sabek\.ma|api\.7sabek\.ma"
        r")(:\d+)?$"
    )

    configure_logging()
    logger = logging.getLogger("app.cors")
    app = FastAPI(title=settings.app_name)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:3000",
            "http://localhost:3000",
            "http://127.0.0.1:3001",
            "http://localhost:3001",
        ],
        allow_origin_regex=allow_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def backup_guard(request, call_next):
        if is_backup_in_progress():
            path = request.url.path
            if not (
                path.startswith("/admin/backups/import")
                or path.startswith("/admin/activity")
                or path.startswith("/health")
            ):
                return JSONResponse(
                    {"detail": "Restauration en cours. Réessaie dans quelques minutes."},
                    status_code=503,
                )
        return await call_next(request)

    @app.middleware("http")
    async def log_cors_headers(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/auth"):
            origin = request.headers.get("origin")
            cookie = request.headers.get("cookie")
            logger.info(
                "CORS debug %s %s origin=%s cookie=%s",
                request.method,
                request.url.path,
                origin,
                cookie,
            )
            logger.info(
                "CORS response %s allow-credentials=%s allow-origin=%s",
                request.url.path,
                response.headers.get("access-control-allow-credentials"),
                response.headers.get("access-control-allow-origin"),
            )
        return response

    @app.middleware("http")
    async def ip_block_guard(request, call_next):
        if is_test_env:
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        if (
            path.startswith("/health")
            or path.startswith("/docs")
            or path.startswith("/openapi")
        ):
            return await call_next(request)

        SessionLocal = get_sessionmaker()
        async with SessionLocal() as db:
            if await is_ip_blocked(db, get_client_ip(request)):
                platform_settings = await get_platform_settings(db, create_if_missing=True)
                support_email = (platform_settings.support_email or "").strip()
                message = (
                    "Cette connexion est suspecte. Le système l'a bloquée automatiquement "
                    "après détection d'une utilisation suspecte. Contacte le support."
                )
                if support_email:
                    message = f"{message} ({support_email})"
                response = JSONResponse(
                    {
                        "detail": "IP_ADDRESS_BLOCKED",
                        "message": message,
                    },
                    status_code=403,
                )
                return _append_cors_headers(
                    response,
                    request.headers.get("origin", ""),
                    allow_origin_regex,
                )
        return await call_next(request)

    @app.middleware("http")
    async def api_rate_limit(request, call_next):
        if is_test_env or is_local_env:
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        if (
            path.startswith("/public")
            or path.startswith("/health")
            or path.startswith("/docs")
            or path.startswith("/openapi")
        ):
            return await call_next(request)

        if path in {"/auth/login", "/auth/register"}:
            return await call_next(request)

        SessionLocal = get_sessionmaker()
        async with SessionLocal() as db:
            platform_settings = await get_platform_settings(db, create_if_missing=True)
            limit = platform_settings.rate_limit_api_max
            window_seconds = platform_settings.rate_limit_api_window_minutes * 60
            if limit > 0 and window_seconds > 0:
                ip = get_client_ip(request)
                result = await check_rate_limit(db, f"api:{ip}", limit, window_seconds)
                if not result.allowed:
                    response = JSONResponse(
                        {"detail": build_rate_limit_message(result.retry_after)},
                        status_code=429,
                        headers={"Retry-After": str(result.retry_after)},
                    )
                    return _append_cors_headers(
                        response,
                        request.headers.get("origin", ""),
                        allow_origin_regex,
                    )
        return await call_next(request)

    @app.middleware("http")
    async def ensure_cors_headers_on_errors(request: Request, call_next):
        origin = request.headers.get("origin", "")
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled error on %s %s", request.method, request.url.path)
            response = JSONResponse({"detail": "Internal Server Error"}, status_code=500)
        return _append_cors_headers(response, origin, allow_origin_regex)

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(self)")
        if not is_local_env:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response

    app.include_router(api_router)

    return app


app = create_app()
