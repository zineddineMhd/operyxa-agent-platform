import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from operyxa.api.routes import (
    approvals,
    auth,
    platform,
    policies,
    projects,
    pull_requests,
    runs,
    teams,
    workflows,
)
from operyxa.config import Settings, get_settings
from operyxa.db.session import Database
from operyxa.errors import AppError


def _error(request: Request, status: int, code: str, message: str, details=None) -> JSONResponse:
    error = {
        "code": code,
        "message": message,
        "request_id": getattr(request.state, "request_id", None),
    }
    if details:
        error["details"] = details
    return JSONResponse({"error": error}, status_code=status)


def create_app(settings: Settings | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.environment in ("staging", "production") and settings.jwt_secret_key == "change-me":
        raise RuntimeError("JWT_SECRET_KEY must be set outside local environments")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await app.state.db.dispose()

    app = FastAPI(title="Operyxa API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db or Database(settings.database_url)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        return _error(request, exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [
            {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
            for e in exc.errors()
        ]
        return _error(request, 422, "VALIDATION_ERROR", "the request is invalid", details)

    @app.get("/healthz", tags=["platform"])
    async def healthz() -> dict:
        async with app.state.db.session() as s:
            await s.execute(text("SELECT 1"))
        return {"status": "ok"}

    for router in (
        auth.router,
        projects.router,
        runs.router,
        approvals.router,
        platform.router,
        policies.router,
        teams.router,
        workflows.router,
        pull_requests.router,
    ):
        app.include_router(router, prefix="/api/v1")
    return app
