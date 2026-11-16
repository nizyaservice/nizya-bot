"""Зависимости FastAPI: клиент к API, хранилище сессий, текущая сессия.

Тут же обновление access-токена: он живёт 15 минут, а страницы портала
открывают неделями. При 401 портал один раз обменивает refresh-токен и
повторяет запрос; если и это не вышло — сессия мертва, человека ведём на вход.
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from fastapi import Depends, Request

from .api import CabinetApi, CabinetApiError
from .config import PortalSettings, get_settings
from .sessions import Session, SessionStore


logger = structlog.get_logger(__name__)


class LoginRequiredError(Exception):
    """Страница требует входа. Обработчик приложения ведёт на /login."""


class SessionExpiredError(LoginRequiredError):
    """Refresh-токен не принят: сессию удалили или ей больше недели."""


def get_api(request: Request) -> CabinetApi:
    return CabinetApi(request.app.state.api_client)


def get_store(request: Request) -> SessionStore:
    return request.app.state.session_store


def get_portal_settings() -> PortalSettings:
    return get_settings()


async def get_session(
    request: Request,
    store: SessionStore = Depends(get_store),
    settings: PortalSettings = Depends(get_portal_settings),
) -> Session | None:
    """Сессия по cookie; ``None`` — не вошёл."""
    return await store.get(request.cookies.get(settings.session_cookie))


async def require_session(session: Session | None = Depends(get_session)) -> Session:
    if session is None:
        raise LoginRequiredError
    return session


async def with_fresh_token(
    session: Session,
    store: SessionStore,
    api: CabinetApi,
    call: Callable[[str], Awaitable[Any]],
) -> Any:
    """Выполнить запрос с токеном сессии, обновив его при 401 — один раз.

    Повторяем ровно один раз: если и свежий токен получает 401, дело не в
    сроке, и цикл обновлений ничего не починит.
    """
    try:
        return await call(session.access_token)
    except CabinetApiError as error:
        if error.status_code != 401:
            raise

    try:
        tokens = await api.refresh_tokens(session.refresh_token)
    except CabinetApiError as error:
        logger.info('Refresh-токен не принят, сессию закрываем', status_code=error.status_code)
        await store.delete(session.sid)
        raise SessionExpiredError from error

    session.access_token = str(tokens.get('access_token') or '')
    # Бот ротирует refresh-токен при обмене; если не прислал — оставляем прежний.
    session.refresh_token = str(tokens.get('refresh_token') or session.refresh_token)
    await store.save(session)

    return await call(session.access_token)


def check_csrf(session: Session, submitted: str | None) -> None:
    """Сверить токен формы с токеном сессии.

    Без этого чужая страница могла бы отправить POST с нашей cookie —
    ``SameSite=Lax`` ловит не все случаи, а на покупках цена ошибки деньги.
    """
    if not submitted or not hmac.compare_digest(session.csrf_token, submitted):
        raise CsrfError


class CsrfError(Exception):
    """Форма пришла без правильного токена: показываем «обновите страницу»."""
