"""Баланс и пополнение: полный цикл оплаты на сайте.

Смысл раздела — человек платит и пользуется подпиской, даже когда телеграм ему
недоступен. Поэтому оплата идёт целиком через портал: выбор способа и суммы,
переход к провайдеру, возврат и проверка платежа.

Деньги зачисляет бот по вебхуку от провайдера — портал в этом не участник и
«успешной оплатой» ничего не считает. На экране возврата он спрашивает бота о
судьбе платежа (``/balance/pending-payments/.../check``) и показывает то, что
ответил провайдер. Поэтому возврат с оплаты безопасен: подделанный заход на
страницу возврата не начислит ничего.

Суммы приходят и уходят в копейках, человек видит и вводит рубли.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from ..api import CabinetApi, CabinetApiError
from ..deps import check_csrf, get_api, get_store, require_session, with_fresh_token
from ..sessions import Session, SessionStore
from ..templating import render
from .pages import remember_available


logger = structlog.get_logger(__name__)

router = APIRouter()

#: Сколько операций показываем в истории.
HISTORY_LIMIT = 20

#: Подсказки сумм под полем ввода, в рублях. Это ссылки, а не скрипт: по ним
#: страница открывается заново с подставленной суммой.
AMOUNT_PRESETS = (300, 500, 1000, 2000)


@router.get('/balance', response_class=HTMLResponse)
async def balance_page(
    request: Request,
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> HTMLResponse:
    balance = await with_fresh_token(session, store, api, api.balance)
    # Сумма в шапке берётся из сессии: обновляем её здесь, как и в кабинете.
    await remember_available(session, store, balance)

    async def history(token: str) -> Any:
        return await api.transactions(token, HISTORY_LIMIT)

    transactions: list[dict[str, Any]] = []
    try:
        payload = await with_fresh_token(session, store, api, history)
        raw = payload.get('transactions') if isinstance(payload, dict) else None
        transactions = [item for item in (raw if isinstance(raw, list) else []) if isinstance(item, dict)]
    except CabinetApiError as error:
        # История — необязательная часть экрана, баланс важнее.
        logger.info('Не удалось получить историю операций', status_code=error.status_code)

    pending: list[dict[str, Any]] = []
    try:
        payload = await with_fresh_token(session, store, api, api.pending_payments)
        raw = payload.get('payments') if isinstance(payload, dict) else None
        pending = [item for item in (raw if isinstance(raw, list) else []) if isinstance(item, dict)]
    except CabinetApiError as error:
        logger.info('Не удалось получить незавершённые платежи', status_code=error.status_code)

    return render(
        request,
        'balance.html',
        {
            'balance': balance if isinstance(balance, dict) else {},
            'transactions': transactions,
            'pending': pending,
        },
        session=session,
    )


@router.get('/balance/topup', response_class=HTMLResponse)
async def topup_form(
    request: Request,
    amount: str = '',
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> HTMLResponse:
    methods = await _methods(session, store, api)
    return render(
        request,
        'balance_topup.html',
        {'methods': methods, 'presets': AMOUNT_PRESETS, 'amount': _preset(amount)},
        session=session,
    )


@router.post('/balance/topup', response_class=HTMLResponse)
async def topup_start(
    request: Request,
    method: str = Form(''),
    amount: str = Form(''),
    option: str = Form(''),
    csrf_token: str = Form(''),
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> HTMLResponse:
    check_csrf(session, csrf_token)

    methods = await _methods(session, store, api)
    chosen = next((item for item in methods if item.get('id') == method), None)
    if chosen is None:
        return render(
            request,
            'balance_topup.html',
            {'methods': methods, 'presets': AMOUNT_PRESETS, 'error': 'Выберите способ оплаты.', 'amount': amount},
            status_code=400,
            session=session,
        )

    amount_kopeks = _kopeks(amount)
    if amount_kopeks is None:
        return render(
            request,
            'balance_topup.html',
            {
                'methods': methods,
                'presets': AMOUNT_PRESETS,
                'error': 'Введите сумму в рублях, например 300.',
                'method': method,
                'amount': amount,
            },
            status_code=400,
            session=session,
        )

    # Границы берём у бота: у каждого способа свои, и отказ после перехода к
    # провайдеру человеку объяснить уже нечем.
    limit_error = _out_of_limits(chosen, amount_kopeks)
    if limit_error:
        return render(
            request,
            'balance_topup.html',
            {'methods': methods, 'presets': AMOUNT_PRESETS, 'error': limit_error, 'method': method, 'amount': amount},
            status_code=400,
            session=session,
        )

    async def call(token: str) -> Any:
        return await api.topup(token, amount_kopeks, method, option.strip() or None)

    try:
        payment = await with_fresh_token(session, store, api, call)
    except CabinetApiError as error:
        logger.info('Платёж не создан', method=method, status_code=error.status_code)
        return render(
            request,
            'balance_topup.html',
            {'methods': methods, 'presets': AMOUNT_PRESETS, 'error': error.detail, 'method': method, 'amount': amount},
            status_code=400,
            session=session,
        )

    data = payment if isinstance(payment, dict) else {}
    return render(
        request,
        'balance_pay.html',
        {
            'payment': data,
            'method': method,
            'method_name': chosen.get('name') or method,
        },
        session=session,
    )


@router.get('/balance/top-up/result', response_class=HTMLResponse)
async def topup_result(
    request: Request,
    method: str = '',
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> HTMLResponse:
    """Возврат от платёжной системы.

    Адрес задан ботом: он строит его из ``CABINET_URL`` и в самом платеже знает
    только способ, без идентификатора. Поэтому идентификатор спрашиваем у бота —
    последний платёж этим способом — и дальше идём обычной проверкой статуса.

    Параметр ``status`` из адреса сознательно игнорируем: это слово из браузера,
    а не подтверждение оплаты.
    """

    async def call(token: str) -> Any:
        return await api.latest_payment(token, method)

    try:
        latest = await with_fresh_token(session, store, api, call)
    except CabinetApiError as error:
        logger.info('Возврат с оплаты: платёж не найден', method=method, status_code=error.status_code)
        return render(
            request,
            'balance_status.html',
            {'message': 'Платёж не найден. Проверьте баланс — возможно, он уже зачислен.', 'payment': None},
            session=session,
        )

    data = latest if isinstance(latest, dict) else {}
    identifier = data.get('identifier')
    if not identifier:
        return render(request, 'balance_status.html', {'message': None, 'payment': data}, session=session)

    return await payment_status(
        request,
        method=method,
        payment_id=str(identifier),
        session=session,
        api=api,
        store=store,
    )


@router.get('/balance/payment/{method}/{payment_id}', response_class=HTMLResponse)
async def payment_status(
    request: Request,
    method: str,
    payment_id: str,
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> HTMLResponse:
    """Экран возврата с оплаты: спрашиваем провайдера, что с платежом.

    Ничего не начисляет: зачисление делает бот по вебхуку. Здесь только запрос
    статуса — он же подталкивает бота проверить платёж, если вебхук не дошёл.
    """

    async def call(token: str) -> Any:
        return await api.check_payment(token, method, payment_id)

    try:
        result = await with_fresh_token(session, store, api, call)
    except CabinetApiError as error:
        logger.info('Проверка платежа не удалась', method=method, status_code=error.status_code)
        return render(
            request,
            'balance_status.html',
            {'message': error.detail, 'payment': None},
            session=session,
        )

    data = result if isinstance(result, dict) else {}
    return render(
        request,
        'balance_status.html',
        {
            'message': data.get('message'),
            'payment': data.get('payment') if isinstance(data.get('payment'), dict) else None,
            'status_changed': bool(data.get('status_changed')),
        },
        session=session,
    )


async def _methods(session: Session, store: SessionStore, api: CabinetApi) -> list[dict[str, Any]]:
    payload = await with_fresh_token(session, store, api, api.payment_methods)
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict) and item.get('id')]


def _preset(raw: str) -> str:
    """Сумма из адреса в поле ввода: принимаем только целые рубли.

    Значение приходит из ссылки, то есть снаружи; всё, что не похоже на сумму,
    выбрасываем, а не подставляем в форму.
    """
    cleaned = (raw or '').strip()
    return cleaned if cleaned.isdigit() and len(cleaned) <= 7 else ''


def _kopeks(raw: str) -> int | None:
    """Рубли из поля ввода в копейки. Запятая как разделитель — нормально."""
    cleaned = (raw or '').strip().replace(',', '.').replace(' ', '')
    if not cleaned:
        return None
    try:
        rubles = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    if rubles <= 0:
        return None
    return int(rubles * 100)


def _out_of_limits(method: dict[str, Any], amount_kopeks: int) -> str | None:
    minimum = int(method.get('min_amount_kopeks') or 0)
    maximum = int(method.get('max_amount_kopeks') or 0)
    if minimum and amount_kopeks < minimum:
        return f'Минимальная сумма для этого способа — {minimum // 100} ₽.'
    if maximum and amount_kopeks > maximum:
        return f'Максимальная сумма для этого способа — {maximum // 100} ₽.'
    return None
