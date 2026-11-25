"""Каталог тарифов и покупка с баланса.

Каталог открыт всем: цены человек смотрит до того, как заведёт аккаунт.
Пока сессии нет, портал берёт публичную ручку бота (``/public/tariffs``) —
там базовые цены без скидок промогруппы. Как только человек вошёл, каталог
строится по ``/subscription/purchase-options``: те же тарифы, но с его
скидками и отметкой текущего. Поэтому цена с витрины — верхняя граница, после
входа она может только упасть.

Дальше всё как прежде: подтверждение — GET (страницу можно обновить и уйти
назад, ничего не списав), списывает только POST с CSRF-токеном. Экран
подтверждения требует сессии, и человек без неё попадает на вход, а оттуда
возвращается ровно на выбранный тариф (см. ``next`` в deps.py).

Цену на всех экранах считает бот. Портал передаёт только выбор: посчитанная
тут сумма разошлась бы со списанием, как только оператор заведёт скидку.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from ..api import CabinetApi, CabinetApiError
from ..deps import check_csrf, get_api, get_session, get_store, require_session, with_fresh_token
from ..sessions import Session, SessionStore
from ..templating import render
from .pages import available_balance


logger = structlog.get_logger(__name__)

router = APIRouter()

#: Бот отвечает этим кодом, когда на балансе не хватает денег.
PAYMENT_REQUIRED = 402


@router.get('/tariffs', response_class=HTMLResponse)
async def tariff_catalog(
    request: Request,
    days: int | None = None,
    session: Session | None = Depends(get_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> HTMLResponse:
    """Каталог: вошедшему — с его ценами, остальным — базовые."""
    if session is None:
        options = await api.public_tariffs()
    else:
        options = await with_fresh_token(session, store, api, api.purchase_options)
        # Заодно освежаем сумму в шапке: человек мог прийти сюда сразу после
        # входа, и в сессии её бы ещё не было.
        await available_balance(session, store, api)

    context = _catalog_context(options)

    if context['sales_mode'] and context['sales_mode'] != 'tariffs':
        # Классический режим продаж (сроки и опции по отдельности) на портале
        # не собран: показываем честную заглушку, а не половину воронки.
        return render(request, 'tariffs_unavailable.html', {}, session=session)

    context.update(_with_chosen_periods(context['tariffs'], days))
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

    price = int(period.get('price_kopeks') or 0)
    # Сравниваем с доступной суммой (деньги + баллы), а не с денежным
    # кошельком из каталога: списывает бот по ней же. См. available_balance.
    available = await available_balance(session, store, api)
    balance = context['balance_kopeks'] if available is None else available
    return render(
        request,
        'tariff_confirm.html',
        {
            'tariff': tariff,
            'period': period,
            'balance_kopeks': balance,
            'enough_balance': balance >= price,
            'missing_kopeks': max(0, price - balance),
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

    Форма ответа у публичной ручки и у ``purchase-options`` одна и та же —
    отличается только наполнение (скидки, отметки «текущий» и «куплен»),
    поэтому разбор общий.

    Из каталога убираем то, чего портал не умеет оформлять: суточные тарифы
    (у них своя ручка), тарифы без готовых сроков (только произвольное число
    дней) и уже купленные в режиме нескольких подписок — их продлевает
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
        'has_subscription': bool(data.get('has_subscription')),
    }


def _with_chosen_periods(tariffs: list[dict[str, Any]], days: int | None) -> dict[str, Any]:
    """Выбранный срок и цена каждого тарифа на этот срок.

    Переключатель собирается из всех сроков каталога разом: у разных тарифов
    наборы сроков могут не совпадать. Тарифу, у которого выбранного срока нет,
    показываем ближайший и отмечаем это — иначе он просто пропал бы из
    каталога, и человек не понял бы, куда.
    """
    all_days = sorted({int(period['days']) for tariff in tariffs for period in tariff['periods']})
    if not all_days:
        return {'periods_offered': [], 'selected_days': None, 'cards': []}

    selected = days if days in all_days else _default_days(tariffs, all_days)

    cards = []
    for tariff in tariffs:
        exact = _find_period(tariff, selected)
        period = exact or min(
            tariff['periods'],
            key=lambda item: (abs(int(item['days']) - selected), int(item['days'])),
        )
        cards.append({**tariff, 'period': period, 'is_exact_period': exact is not None})

    return {'periods_offered': all_days, 'selected_days': selected, 'cards': cards}


def _default_days(tariffs: list[dict[str, Any]], all_days: list[int]) -> int:
    """Срок по умолчанию: отмеченный оператором как выгодный, иначе 30 дней."""
    highlighted = [
        int(period['days']) for tariff in tariffs for period in tariff['periods'] if period.get('is_highlighted')
    ]
    if highlighted:
        return max(set(highlighted), key=highlighted.count)
    return 30 if 30 in all_days else all_days[0]


def _find_tariff(tariffs: list[dict[str, Any]], tariff_id: int) -> dict[str, Any] | None:
    return next((t for t in tariffs if t.get('id') == tariff_id), None)


def _find_period(tariff: dict[str, Any], days: int) -> dict[str, Any] | None:
    return next((p for p in tariff.get('periods') or [] if int(p.get('days') or 0) == days), None)
