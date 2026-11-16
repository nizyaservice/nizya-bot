"""Сборка приложения портала.

Запуск: ``uvicorn portal.main:app``. Клиент к API бота и подключение к Redis
живут в lifespan — один раз на процесс, а не на запрос.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from redis.asyncio import from_url

from .api import CabinetUnavailableError, build_client
from .config import get_settings
from .deps import CsrfError, LoginRequiredError
from .routes import auth as auth_routes, pages as page_routes
from .sessions import SessionStore
from .templating import render


logger = structlog.get_logger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / 'static'


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    app.state.api_client = build_client(settings.api_base_url, settings.api_timeout_seconds)

    redis = from_url(settings.redis_url, decode_responses=True)
    app.state.redis = redis
    app.state.session_store = SessionStore(redis, settings.session_ttl_seconds)

    logger.info('Портал поднят', api_base_url=settings.api_base_url)
    try:
        yield
    finally:
        await app.state.api_client.aclose()
        await redis.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title='Nizya portal',
        docs_url='/docs' if settings.debug else None,
        redoc_url=None,
        openapi_url='/openapi.json' if settings.debug else None,
        lifespan=lifespan,
    )

    app.mount('/static', StaticFiles(directory=str(STATIC_DIR)), name='static')
    app.include_router(auth_routes.router)
    app.include_router(page_routes.router)

    @app.exception_handler(LoginRequiredError)
    async def _login_required(_request: Request, _exc: LoginRequiredError) -> RedirectResponse:
        # Страница требует входа. Возвращаем 303, чтобы браузер пошёл GET'ом,
        # а не повторил POST на /login.
        return RedirectResponse('/login', status_code=303)

    @app.exception_handler(CsrfError)
    async def _csrf_failed(request: Request, _exc: CsrfError) -> HTMLResponse:
        return render(
            request,
            'error.html',
            {'title': 'Страница устарела', 'message': 'Обновите страницу и повторите действие.'},
            status_code=400,
        )

    @app.exception_handler(CabinetUnavailableError)
    async def _api_down(request: Request, _exc: CabinetUnavailableError) -> HTMLResponse:
        # Туннель до бота лёг: честная заглушка вместо стектрейса.
        return render(
            request,
            'error.html',
            {'title': 'Сервис недоступен', 'message': 'Не получилось связаться с сервисом. Попробуйте позже.'},
            status_code=503,
        )

    return app


app = create_app()
