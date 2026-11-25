"""Зависимости FastAPI: клиент к API, хранилище сессий, текущая сессия.

Тут же обновление access-токена: он живёт 15 минут, а страницы портала
открывают неделями. При 401 портал один раз обменивает refresh-токен и
повторяет запрос; если и это не вышло — сессия мертва, человека ведём на вход.

Сюда же относится правило «смотреть можно, покупать — после входа»: витрина
(тарифы, статус серверов, документы) открыта всем, а всё остальное требует
сессии. Страница, которой не хватило сессии, бросает ``LoginRequiredError`` с
адресом, куда человек шёл, — после входа его возвращают именно туда, а не на
главную, иначе он заново ищет тариф, который уже выбрал.
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

import structlog
from fastapi import Depends, Request

from .api import CabinetApi, CabinetApiError
from .config import PortalSettings, get_settings
from .sessions import Session, SessionStore


logger = structlog.get_logger(__name__)


class LoginRequiredError(Exception):
    """Страница требует входа. Обработчик приложения ведёт на /login."""

    def __init__(self, next_path: str | None = None) -> None:
        super().__init__('login required')
        #: Куда вернуть человека после входа; None — на главную кабинета.
        self.next_path = next_path

    def login_url(self) -> str:
        if not self.next_path:
            return '/login'
        return f'/login?next={quote(self.next_path, safe="")}'


class SessionExpiredError(LoginRequiredError):
    """Refresh-токен не принят: сессию удалили или ей больше недели."""


def safe_next(candidate: str | None) -> str | None:
    """Адрес возврата, если он ведёт на сам портал.

    Принимаем только путь от корня. ``//`` и ``/\\`` браузер читает как
    адрес другого сайта — через такой «возврат» чужая страница увела бы
    человека к себе сразу после успешного входа.
    """
    if not candidate or not candidate.startswith('/'):
        return None
    if candidate.startswith(('//', '/\\')):
        return None
    if any(symbol in candidate for symbol in ('\r', '\n', '\t')):
        return None
    return candidate


def current_path(request: Request) -> str:
    """Путь текущего запроса вместе со строкой параметров."""
    query = request.url.query
    return f'{request.url.path}?{query}' if query else request.url.path


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
    """Сессия по cookie; ``None`` — не вошёл.

    Найденную сессию кладём ещё и на сам запрос: обработчики исключений
    (страница ошибки, протухшая форма) живут вне зависимостей, а шапку
    рисовать надо — иначе вошедший человек увидит на странице ошибки кнопку
    «Войти» и решит, что его выкинуло.
    """
    session = await store.get(request.cookies.get(settings.session_cookie))
    request.state.session = session
    return session


def session_of(request: Request) -> Session | None:
    """Сессия, уже найденная зависимостью этого запроса.

    ``None`` и тогда, когда до зависимостей дело не дошло — например, когда
    ни один маршрут не совпал и страница сразу стала 404.
    """
    return getattr(request.state, 'session', None)


async def require_session(request: Request, session: Session | None = Depends(get_session)) -> Session:
    if session is None:
        raise LoginRequiredError(next_path=current_path(request))
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
    if not submitted:
        raise CsrfError

    # Сравниваем байты: ``compare_digest`` на строках с не-ASCII символами
    # бросает TypeError, и подделанный токен с кириллицей получал бы 500
    # вместо отказа.
    if not hmac.compare_digest(session.csrf_token.encode(), submitted.encode()):
        raise CsrfError


class CsrfError(Exception):
    """Форма пришла без правильного токена: показываем «обновите страницу»."""
