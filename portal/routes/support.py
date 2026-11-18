"""Помощь: обращения в поддержку.

Тикеты можно выключить на стороне бота (режим поддержки «контакт») — тогда
ручки отвечают 403, и портал показывает, куда писать вместо них. Поэтому раздел
всегда начинается с ``/info/support-config``: сначала узнаём, что доступно, и
только потом рисуем список.

Вложений нет намеренно: в API они передаются как Telegram ``file_id``, то есть
файл сперва должен попасть в телеграм. Для портала это бессмысленно — он и
нужен тем, у кого телеграма нет. Текст обращения работает полностью.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from ..api import CabinetApi, CabinetApiError
from ..deps import check_csrf, get_api, get_store, require_session, with_fresh_token
from ..sessions import Session, SessionStore
from ..templating import render


logger = structlog.get_logger(__name__)

router = APIRouter()

#: Бот отвечает этим кодом, когда тикеты выключены.
FORBIDDEN = 403

TITLE_MIN_LENGTH = 3


@router.get('/support', response_class=HTMLResponse)
async def support_page(
    request: Request,
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> HTMLResponse:
    config = await _config(api)

    if not config.get('tickets_enabled'):
        return render(request, 'support.html', {'config': config, 'tickets': None}, session=session)

    tickets: list[dict[str, Any]] = []
    try:
        payload = await with_fresh_token(session, store, api, api.tickets)
        raw = payload.get('items') if isinstance(payload, dict) else None
        tickets = [item for item in (raw if isinstance(raw, list) else []) if isinstance(item, dict)]
    except CabinetApiError as error:
        if error.status_code != FORBIDDEN:
            raise
        # Настройку поменяли между двумя запросами — ведём себя как при выключенных.
        return render(request, 'support.html', {'config': config, 'tickets': None}, session=session)

    return render(request, 'support.html', {'config': config, 'tickets': tickets}, session=session)


@router.post('/support', response_class=HTMLResponse)
async def support_create(
    request: Request,
    title: str = Form(''),
    message: str = Form(''),
    csrf_token: str = Form(''),
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> Response:
    check_csrf(session, csrf_token)

    cleaned_title = title.strip()
    cleaned_message = message.strip()
    if len(cleaned_title) < TITLE_MIN_LENGTH or not cleaned_message:
        config = await _config(api)
        return render(
            request,
            'support.html',
            {
                'config': config,
                'tickets': [],
                'error': 'Нужны тема (от трёх символов) и текст обращения.',
                'title_value': cleaned_title,
                'message_value': cleaned_message,
            },
            status_code=400,
            session=session,
        )

    async def call(token: str) -> Any:
        return await api.create_ticket(token, cleaned_title, cleaned_message)

    try:
        created = await with_fresh_token(session, store, api, call)
    except CabinetApiError as error:
        return render(
            request,
            'error.html',
            {'title': 'Обращение не создано', 'message': error.detail, 'back_url': '/support'},
            status_code=400,
            session=session,
        )

    ticket_id = created.get('id') if isinstance(created, dict) else None
    return RedirectResponse(f'/support/{ticket_id}' if ticket_id else '/support', status_code=303)


@router.get('/support/{ticket_id}', response_class=HTMLResponse)
async def ticket_page(
    request: Request,
    ticket_id: int,
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> HTMLResponse:
    async def call(token: str) -> Any:
        return await api.ticket(token, ticket_id)

    try:
        payload = await with_fresh_token(session, store, api, call)
    except CabinetApiError as error:
        return render(
            request,
            'error.html',
            {'title': 'Обращение недоступно', 'message': error.detail, 'back_url': '/support'},
            status_code=404,
            session=session,
        )

    data = payload if isinstance(payload, dict) else {}
    raw = data.get('messages')
    return render(
        request,
        'ticket.html',
        {
            'ticket': data,
            'messages': [item for item in (raw if isinstance(raw, list) else []) if isinstance(item, dict)],
        },
        session=session,
    )


@router.post('/support/{ticket_id}', response_class=HTMLResponse)
async def ticket_reply(
    request: Request,
    ticket_id: int,
    message: str = Form(''),
    csrf_token: str = Form(''),
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> Response:
    check_csrf(session, csrf_token)

    cleaned = message.strip()
    if not cleaned:
        return RedirectResponse(f'/support/{ticket_id}', status_code=303)

    async def call(token: str) -> Any:
        return await api.reply_to_ticket(token, ticket_id, cleaned)

    try:
        await with_fresh_token(session, store, api, call)
    except CabinetApiError as error:
        return render(
            request,
            'error.html',
            {'title': 'Сообщение не отправлено', 'message': error.detail, 'back_url': f'/support/{ticket_id}'},
            status_code=400,
            session=session,
        )

    return RedirectResponse(f'/support/{ticket_id}', status_code=303)


async def _config(api: CabinetApi) -> dict[str, Any]:
    """Настройки поддержки; отказ не должен закрывать раздел целиком."""
    try:
        payload = await api.support_config()
    except CabinetApiError as error:
        logger.info('Не удалось получить настройки поддержки', status_code=error.status_code)
        return {'tickets_enabled': False, 'support_type': 'unknown'}

    return payload if isinstance(payload, dict) else {'tickets_enabled': False, 'support_type': 'unknown'}
