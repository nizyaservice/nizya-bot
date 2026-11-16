"""Покупка тарифа с баланса.

Три экрана: каталог → подтверждение → результат. Цену на всех трёх даёт бот
(``/subscription/purchase-options``), портал её не считает: скидки группы и
промо-предложения живут на стороне бота, и посчитанная тут цена расходилась бы
со списанием.

Подтверждение — GET: страницу можно обновить и вернуться назад, ничего не
списав. Списывает только POST с CSRF-токеном.
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

#: Бот отвечает этим кодом, когда на балансе не хватает денег.
PAYMENT_REQUIRED = 402


@router.get('/tariffs', response_class=HTMLResponse)
async def tariff_catalog(
    request: Request,
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> HTMLResponse:
    options = await with_fresh_token(session, store, api, api.purchase_options)
    context = _catalog_context(options)

    if context['sales_mode'] != 'tariffs':
        # Классический режим продаж (сроки и опции по отдельности) на портале
        # не собран: показываем честную заглушку, а не половину воронки.
        return render(
            request,
            'tariffs_unavailable.html',
            {},
            session=session,
        )

    return render(request, 'tariffs.html', context, session=session)


@router.get('/tariffs/{tariff_id}', response_class=HTMLResponse)
async def tariff_confirm(
    request: Request,
    tariff_id: int,
    days: int,
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> HTMLResponse:
    options = await with_fresh_token(session, store, api, api.purchase_options)
    context = _catalog_context(options)
    tariff = _find_tariff(context['tariffs'], tariff_id)
    period = _find_period(tariff, days) if tariff else None

    if tariff is None or period is None:
        # Срок убрали из тарифа между открытием каталога и подтверждением.
        return render(
            request,
            'error.html',
            {
                'title': 'Тариф недоступен',
                'message': 'Этот срок больше не продаётся. Выберите тариф заново.',
                'back_url': '/tariffs',
            },
            status_code=404,
            session=session,
        )

    enough = context['balance_kopeks'] >= int(period.get('price_kopeks') or 0)
    return render(
        request,
        'tariff_confirm.html',
        {
            'tariff': tariff,
            'period': period,
            'balance_label': context['balance_label'],
            'enough_balance': enough,
            'is_renewal': bool(tariff.get('is_current')),
        },
        session=session,
    )


@router.post('/tariffs/{tariff_id}/buy', response_class=HTMLResponse)
async def tariff_buy(
    request: Request,
    tariff_id: int,
    days: int = Form(...),
    csrf_token: str = Form(''),
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> HTMLResponse:
    check_csrf(session, csrf_token)

    async def call(token: str) -> Any:
        return await api.purchase_tariff(token, tariff_id, days)

    try:
        result = await with_fresh_token(session, store, api, call)
    except CabinetApiError as error:
        if error.status_code == PAYMENT_REQUIRED:
            return render(
                request,
                'tariff_no_money.html',
                {'message': error.detail},
                status_code=402,
                session=session,
            )
        logger.info(
            'Покупка тарифа отклонена',
            tariff_id=tariff_id,
            days=days,
            status_code=error.status_code,
        )
        return render(
            request,
            'error.html',
            {'title': 'Покупка не прошла', 'message': error.detail, 'back_url': '/tariffs'},
            status_code=400,
            session=session,
        )

    payload = result if isinstance(result, dict) else {}
    return render(request, 'tariff_done.html', {'result': payload}, session=session)


def _catalog_context(options: Any) -> dict[str, Any]:
    """Разобрать ответ бота в то, что нужно страницам.

    Из каталога убираем то, чего портал пока не умеет оформлять: суточные
    тарифы (у них своя ручка), тарифы без готовых сроков (только произвольное
    число дней) и уже купленные в режиме нескольких подписок — их продлевает
    отдельная воронка с ``subscription_id``. Если что-то скрыли, страница
    скажет об этом, а не промолчит.
    """
    data = options if isinstance(options, dict) else {}
    raw = data.get('tariffs')
    tariffs: list[dict[str, Any]] = []
    hidden = 0

    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        if not item.get('is_available', True):
            continue
        periods = [p for p in item.get('periods') or [] if isinstance(p, dict) and p.get('days')]
        if item.get('is_daily') or item.get('is_purchased') or not periods:
            hidden += 1
            continue
        tariffs.append({**item, 'periods': periods})

    return {
        'sales_mode': data.get('sales_mode'),
        'tariffs': tariffs,
        'hidden_count': hidden,
        'balance_kopeks': int(data.get('balance_kopeks') or 0),
        'balance_label': data.get('balance_label') or '0 ₽',
        'has_subscription': bool(data.get('has_subscription')),
    }


def _find_tariff(tariffs: list[dict[str, Any]], tariff_id: int) -> dict[str, Any] | None:
    return next((t for t in tariffs if t.get('id') == tariff_id), None)


def _find_period(tariff: dict[str, Any], days: int) -> dict[str, Any] | None:
    return next((p for p in tariff.get('periods') or [] if int(p.get('days') or 0) == days), None)
