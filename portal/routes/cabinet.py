"""Остальные разделы кабинета: подключение, документы, язык, рефералка.

Собраны в одном модуле: каждый раздел — это один-два коротких экрана поверх
одной ручки бота, и разносить их по файлам было бы больше шума, чем пользы.

Документы (правила, политика, оферта) приходят готовым HTML — его писал
оператор в админке, и показываем мы его как разметку, иначе человек увидит
теги. Это безопасно: CSP портала (``script-src 'self'``) не исполняет ни
встроенные скрипты, ни обработчики в атрибутах.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from ..api import CabinetApi, CabinetApiError
from ..config import PortalSettings
from ..deps import check_csrf, get_api, get_portal_settings, get_store, require_session, with_fresh_token
from ..sessions import Session, SessionStore
from ..templating import render


logger = structlog.get_logger(__name__)

router = APIRouter()

#: Путь портала → (ручка бота, заголовок экрана).
DOCUMENTS = {
    'rules': ('rules', 'Правила сервиса'),
    'privacy': ('privacy-policy', 'Политика конфиденциальности'),
    'offer': ('offer', 'Публичная оферта'),
}

#: Ручка оферты в API зовётся иначе, чем остальные.
DOCUMENT_ENDPOINTS = {'rules': 'rules', 'privacy-policy': 'privacy-policy', 'offer': 'public-offer'}

NOT_FOUND = 404


@router.get('/connect', response_class=HTMLResponse)
async def connect(
    request: Request,
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> HTMLResponse:
    """Ссылка подписки — то, что человек вставляет в приложение."""
    try:
        payload = await with_fresh_token(session, store, api, api.connection_link)
    except CabinetApiError as error:
        if error.status_code == NOT_FOUND:
            # Подписки нет или ссылка ещё не выдана панелью.
            return render(request, 'connect.html', {'reason': error.detail}, session=session)
        raise

    data = payload if isinstance(payload, dict) else {}
    return render(
        request,
        'connect.html',
        {
            'subscription_url': data.get('subscription_url'),
            'display_link': data.get('display_link'),
            'happ_link': data.get('happ_crypto_link') or data.get('happ_redirect_link'),
            'hide_link': bool(data.get('hide_link')),
        },
        session=session,
    )


@router.get('/documents/{slug}', response_class=HTMLResponse)
async def document(
    request: Request,
    slug: str,
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
) -> HTMLResponse:
    known = DOCUMENTS.get(slug)
    if known is None:
        return render(
            request,
            'error.html',
            {'title': 'Документа нет', 'message': 'Такого документа у сервиса нет.', 'back_url': '/'},
            status_code=404,
            session=session,
        )

    endpoint, title = known
    language = _language_of(session)
    try:
        payload = await api.legal_document(DOCUMENT_ENDPOINTS[endpoint], language)
    except CabinetApiError as error:
        # Оператор может скрыть документ из веба — это не поломка.
        logger.info('Документ недоступен', slug=slug, status_code=error.status_code)
        return render(
            request,
            'document.html',
            {'title': title, 'content': None},
            status_code=404 if error.status_code == NOT_FOUND else 200,
            session=session,
        )

    data = payload if isinstance(payload, dict) else {}
    return render(
        request,
        'document.html',
        {'title': title, 'content': data.get('content'), 'updated_at': data.get('updated_at')},
        session=session,
    )


@router.get('/language', response_class=HTMLResponse)
async def language_form(
    request: Request,
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
) -> HTMLResponse:
    payload = await api.languages()
    data = payload if isinstance(payload, dict) else {}
    raw = data.get('languages')

    return render(
        request,
        'language.html',
        {
            'languages': [item for item in (raw if isinstance(raw, list) else []) if isinstance(item, dict)],
            'current': _language_of(session),
        },
        session=session,
    )


@router.post('/language', response_class=HTMLResponse)
async def language_change(
    request: Request,
    language: str = Form(''),
    csrf_token: str = Form(''),
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> Response:
    check_csrf(session, csrf_token)

    async def call(token: str) -> Any:
        return await api.set_language(token, language.strip())

    try:
        result = await with_fresh_token(session, store, api, call)
    except CabinetApiError as error:
        return render(
            request,
            'error.html',
            {'title': 'Язык не сменился', 'message': error.detail, 'back_url': '/language'},
            status_code=400,
            session=session,
        )

    # Профиль в сессии рисует шапку и выбирает язык документов — обновляем сразу,
    # иначе до следующего открытия главной человек видел бы прежний язык.
    chosen = (result or {}).get('language') if isinstance(result, dict) else None
    if chosen:
        session.user = {**session.user, 'language': chosen}
        await store.save(session)

    return RedirectResponse('/', status_code=303)


@router.get('/referral', response_class=HTMLResponse)
async def referral(
    request: Request,
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
    settings: PortalSettings = Depends(get_portal_settings),
) -> HTMLResponse:
    info = await with_fresh_token(session, store, api, api.referral)
    data = info if isinstance(info, dict) else {}

    invited: list[dict[str, Any]] = []
    try:
        listing = await with_fresh_token(session, store, api, api.referral_list)
        raw = listing.get('referrals') if isinstance(listing, dict) else None
        invited = [item for item in (raw if isinstance(raw, list) else []) if isinstance(item, dict)]
    except CabinetApiError as error:
        # Список приглашённых — необязательная часть экрана.
        logger.info('Не удалось получить список приглашённых', status_code=error.status_code)

    return render(
        request,
        'referral.html',
        {
            'info': data,
            # Бот отдаёт ссылку на себя; на портале пригодится и своя, если она задана.
            'portal_link': _portal_referral_link(settings, data.get('referral_code')),
            'invited': invited,
        },
        session=session,
    )


def _language_of(session: Session) -> str:
    language = (session.user or {}).get('language')
    return language if isinstance(language, str) and language else 'ru'


def _portal_referral_link(settings: PortalSettings, code: Any) -> str | None:
    if not isinstance(code, str) or not code or not settings.public_url:
        return None
    return f'{settings.public_url.rstrip("/")}/login?ref={code}'
