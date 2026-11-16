"""Главная портала: подписка и баланс.

Покупка тарифов и остальные разделы кабинета добавляются следующими шагами —
каркас для них тут: сессия, обновление токена, общий шаблон.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ..api import CabinetApi, CabinetApiError
from ..deps import get_api, get_store, require_session, with_fresh_token
from ..sessions import Session, SessionStore
from ..templating import render


logger = structlog.get_logger(__name__)

router = APIRouter()


@router.get('/', response_class=HTMLResponse)
async def home(
    request: Request,
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> HTMLResponse:
    profile = await with_fresh_token(session, store, api, api.me)

    if isinstance(profile, dict):
        # Профиль в сессии — для шапки; обновляем на каждом открытии главной,
        # чтобы баланс в шапке не отставал от страницы.
        session.user = profile
        await store.save(session)

    status = await _safe(api, session, store, api.subscription, 'подписку')
    balance = await _safe(api, session, store, api.balance, 'баланс')

    # Ручка отдаёт обёртку {has_subscription, subscription}: разворачиваем здесь,
    # чтобы шаблон не знал про форму ответа API.
    subscription = status.get('subscription') if isinstance(status, dict) else None

    return render(
        request,
        'home.html',
        {'profile': profile, 'subscription': subscription, 'balance': balance},
        session=session,
    )


@router.get('/healthz')
async def healthz() -> dict[str, str]:
    """Проверка живости для Caddy и docker healthcheck.

    Нарочно не трогает ни Redis, ни бот: это проверка «процесс поднялся», а не
    «вся цепочка здорова» — иначе падение бота перезапускало бы портал.
    """
    return {'status': 'ok'}


async def _safe(
    api: CabinetApi,
    session: Session,
    store: SessionStore,
    call,
    what: str,
):
    """Необязательный блок страницы: его отказ не должен ронять всю главную."""
    try:
        return await with_fresh_token(session, store, api, call)
    except CabinetApiError as error:
        logger.info('Не удалось получить данные для страницы', what=what, status_code=error.status_code)
        return None
