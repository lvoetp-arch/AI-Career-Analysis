"""職話直說後端入口。

啟動（專案根目錄）：
    python -m uvicorn app.main:create_app --factory --app-dir backend --reload
"""
from __future__ import annotations

import importlib
import pkgutil
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType

import httpx
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.auth import SupabaseTokenVerifier, TokenVerifier
from app.core.config import ConfigError, Settings, load_settings
from app.core.db import AdminDb
from app.core.errors import register_exception_handlers
from app.core.llm import LLMClient, build_llm_client
from app.core.logging import RequestContextMiddleware, configure_logging
from app.core.ratelimit import RateLimiter, SupabaseUsageStore, UsageStore

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


def include_discovered_routers(app: FastAPI, package: ModuleType | None = None) -> None:
    """自動掛載 app.routers 底下各模組的 `router`，各站不必修改 main.py。"""
    if package is None:
        from app import routers as package  # noqa: PLC0415
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{package.__name__}.{info.name}")
        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            app.include_router(router)


def create_app(
    settings: Settings | None = None,
    *,
    token_verifier: TokenVerifier | None = None,
    usage_store: UsageStore | None = None,
    llm_client: LLMClient | None = None,
    http_client: httpx.Client | None = None,
    serve_frontend: bool = True,
) -> FastAPI:
    if settings is None:
        try:
            settings = load_settings(load_dotenv_file=True)
        except ConfigError as exc:
            raise SystemExit(f"[設定錯誤] {exc}") from None

    configure_logging()
    http = http_client or httpx.Client(base_url=settings.supabase_url, timeout=15.0)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        http.close()

    app = FastAPI(title="職話直說", lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")

    admin = AdminDb(http, settings)
    app.state.settings = settings
    app.state.http = http
    app.state.admin_db = admin
    app.state.token_verifier = token_verifier or SupabaseTokenVerifier(http, settings.supabase_anon_key)
    app.state.rate_limiter = RateLimiter(settings, usage_store or SupabaseUsageStore(admin))
    app.state.llm = llm_client or build_llm_client(settings)

    register_exception_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    if settings.allowed_origins:  # 後加的在外層：CORS 包住 RequestContext，500 回應也帶得到 CORS 標頭
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.allowed_origins),
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
            allow_credentials=False,  # 用 Bearer token，不用 cookie
        )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    include_discovered_routers(app)

    if serve_frontend and FRONTEND_DIR.is_dir():  # 必須最後掛載，否則會蓋掉 /api
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return app
