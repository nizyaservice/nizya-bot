"""Вход на портал: почта → код из письма → сессия.

Пароля нет вовсе. Экран кода рисуется ответом на POST, а не редиректом с
адресом в строке: почта не должна оседать в истории браузера, логах прокси и
реферерах.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..api import CabinetApi, CabinetApiError
from ..config import PortalSettings
from ..deps import (
    CsrfError,
    check_csrf,
    get_api,
    get_portal_settings,
    get_session,
    get_store,
)
from ..sessions import Session, SessionStore
from ..templating import render


logger = structlog.get_logger(__name__)

router = APIRouter()


@router.get('/login', response_class=HTMLResponse)
async def login_form(
    request: Request,
    session: Session | None = Depends(get_session),
) -> HTMLResponse:
    if session is not None:
        return RedirectResponse('/', status_code=303)
    return render(request, 'login.html', {})


@router.post('/login', response_class=HTMLResponse)
async def request_code(
    request: Request,
    email: Annotated[str, Form()],
    api: CabinetApi = Depends(get_api),
) -> HTMLResponse:
    """Отправить код и сразу показать экран ввода.

    Бот отвечает одинаково и на знакомый адрес, и на незнакомый, так что
    экран кода показываем всегда: портал не должен подсказывать, есть ли
    аккаунт.
    """
    email = (email or '').strip()
    if not email:
        return render(request, 'login.html', {'error': 'Введите адрес почты'}, status_code=400)

    try:
        await api.request_login_code(email)
    except CabinetApiError as error:
        # 400/403 — отказ по существу (одноразовый ящик, запрещённый адрес):
        # его показываем, иначе человек не поймёт, почему письма нет.
        logger.info('Код входа не отправлен', status_code=error.status_code)
        return render(request, 'login.html', {'error': error.detail, 'email': email}, status_code=400)

    return render(request, 'login_code.html', {'email': email})


@router.post('/login/code', response_class=HTMLResponse)
async def submit_code(
    request: Request,
    email: Annotated[str, Form()],
    code: Annotated[str, Form()],
    accepted: Annotated[list[str] | None, Form()] = None,
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
    settings: PortalSettings = Depends(get_portal_settings),
) -> HTMLResponse:
    email = (email or '').strip()
    code = (code or '').strip()

    try:
        tokens = await api.verify_login_code(
            email,
            code,
            language=settings.default_language,
            accepted_legal_documents=accepted,
        )
    except CabinetApiError as error:
        if error.status_code == 428:
            # Бот требует согласия с документами — рисуем галочки и повторяем.
            documents = await _legal_documents(api)
            return render(
                request,
                'login_code.html',
                {'email': email, 'documents': documents, 'error': error.detail},
                status_code=428,
            )
        return render(
            request,
            'login_code.html',
            {'email': email, 'error': error.detail},
            status_code=400,
        )

    session = await store.create(
        access_token=str(tokens.get('access_token') or ''),
        refresh_token=str(tokens.get('refresh_token') or ''),
        user=tokens.get('user') if isinstance(tokens.get('user'), dict) else {},
    )

    response = RedirectResponse('/', status_code=303)
    response.set_cookie(
        settings.session_cookie,
        session.sid,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite='lax',
        path='/',
    )
    return response


@router.post('/logout')
async def logout(
    request: Request,
    csrf_token: Annotated[str | None, Form()] = None,
    session: Session | None = Depends(get_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
    settings: PortalSettings = Depends(get_portal_settings),
) -> RedirectResponse:
    if session is not None:
        try:
            check_csrf(session, csrf_token)
        except CsrfError:
            # Выход по чужой ссылке — не катастрофа, но и выполнять не станем.
            return RedirectResponse('/', status_code=303)

        try:
            await api.logout(session.refresh_token)
        except CabinetApiError as error:
            # Чужой сервис мог не ответить — локальную сессию закрываем всё равно.
            logger.info('Бот не подтвердил выход', status_code=error.status_code)
        await store.delete(session.sid)

    response = RedirectResponse('/login', status_code=303)
    response.delete_cookie(settings.session_cookie, path='/')
    return response


async def _legal_documents(api: CabinetApi) -> list[dict[str, str]]:
    """Документы для галочек на первом входе; пустой список — гейт выключен."""
    try:
        config = await api.legal_consent_config()
    except CabinetApiError:
        return []

    if not isinstance(config, dict):
        return []
    documents = config.get('documents')
    return [item for item in documents if isinstance(item, dict)] if isinstance(documents, list) else []
