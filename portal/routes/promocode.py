"""Активация промокода.

Бот отвечает на отказ машинным кодом (``{"code": ..., "message": ...}``), а
текст в нём английский — поэтому фразу выбирает портал по коду. Незнакомый код
не превращаем в «ошибку сервера»: показываем общий текст и пишем код в лог,
чтобы новый случай был виден.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from ..api import CabinetApi, CabinetApiError
from ..deps import check_csrf, get_api, get_store, require_session, with_fresh_token
from ..sessions import Session, SessionStore
from ..templating import render


logger = structlog.get_logger(__name__)

router = APIRouter()

#: Отказы активации: код бота → фраза человеку.
REFUSALS = {
    'not_found': 'Такого промокода нет. Проверьте, нет ли опечатки.',
    'expired': 'Срок действия промокода истёк.',
    'inactive': 'Промокод отключён.',
    'not_yet_valid': 'Промокод ещё не начал действовать.',
    'used': 'Промокод уже израсходован.',
    'already_used_by_user': 'Вы уже активировали этот промокод.',
    'active_discount_exists': 'У вас уже есть активная скидка — сначала нужно использовать её.',
    'no_subscription_for_days': 'Этот промокод продлевает подписку, а её у вас пока нет.',
    'subscription_not_found': 'Подписка не найдена.',
    'not_first_purchase': 'Промокод только для первой покупки.',
    'daily_limit': 'Слишком много активаций за сегодня. Попробуйте завтра.',
    'trial_subscription_exists': 'Промокод даёт пробный период, а подписка у вас уже есть.',
    'trial_provisioning_failed': 'Не получилось выдать пробный период. Попробуйте позже.',
    'traffic_not_applicable': 'Промокод добавляет трафик, а у вас он без ограничений.',
    'select_subscription': 'У вас несколько подписок — выберите нужную в боте.',
    'server_error': 'Что-то пошло не так. Попробуйте позже.',
}

GENERIC_REFUSAL = 'Промокод не применился. Проверьте код и попробуйте снова.'


@router.get('/promocode', response_class=HTMLResponse)
async def promocode_form(
    request: Request,
    session: Session = Depends(require_session),
) -> HTMLResponse:
    return render(request, 'promocode.html', {}, session=session)


@router.post('/promocode', response_class=HTMLResponse)
async def promocode_activate(
    request: Request,
    code: str = Form(''),
    csrf_token: str = Form(''),
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> HTMLResponse:
    check_csrf(session, csrf_token)

    cleaned = code.strip()
    if not cleaned:
        return render(
            request,
            'promocode.html',
            {'error': 'Введите промокод.'},
            status_code=400,
            session=session,
        )

    async def call(token: str) -> Any:
        return await api.activate_promocode(token, cleaned)

    try:
        result = await with_fresh_token(session, store, api, call)
    except CabinetApiError as error:
        return render(
            request,
            'promocode.html',
            {'error': _refusal(error), 'code': cleaned},
            status_code=400,
            session=session,
        )

    payload = result if isinstance(result, dict) else {}
    # Бот умеет ответить 200 с success=false — когда просит выбрать подписку.
    if payload.get('success') is False:
        return render(
            request,
            'promocode.html',
            {'error': REFUSALS.get(str(payload.get('error')), GENERIC_REFUSAL), 'code': cleaned},
            status_code=400,
            session=session,
        )

    return render(request, 'promocode_done.html', {'result': payload, 'code': cleaned}, session=session)


def _refusal(error: CabinetApiError) -> str:
    if error.code and error.code in REFUSALS:
        return REFUSALS[error.code]

    logger.info('Промокод отклонён кодом, которого портал не знает', code=error.code, status_code=error.status_code)
    return GENERIC_REFUSAL
