"""Витрина и личный кабинет.

Два разных экрана под двумя адресами, и это нарочно. ``/`` — витрина: её
открывает человек, который ещё ничего не купил, и ей нужно рассказать про
сервис и цены. ``/dashboard`` — кабинет: подписка, ссылка подключения,
быстрые действия. Вошедшего с витрины уводим в кабинет, чтобы он не искал
свою подписку среди рекламных блоков.

Витрина спрашивает у бота каталог и статус серверов публичными ручками
(``/cabinet/public/*``) — без токена и без персональных цен. Отказ любой из
них убирает блок со страницы, но не ломает её: витрина должна открываться,
даже когда бот недоступен.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ..api import CabinetApi, CabinetApiError
from ..deps import get_api, get_session, get_store, require_session, with_fresh_token
from ..sessions import Session, SessionStore
from ..templating import render


logger = structlog.get_logger(__name__)

router = APIRouter()

#: Сколько тарифов показываем на витрине: три карточки читаются целиком,
#: за остальными человек идёт в каталог.
SHOWCASE_LIMIT = 3


@router.get('/', response_class=HTMLResponse)
async def landing(
    request: Request,
    session: Session | None = Depends(get_session),
    api: CabinetApi = Depends(get_api),
) -> Response:
    """Витрина. Вошедшему здесь делать нечего — он идёт в кабинет."""
    if session is not None:
        return RedirectResponse('/dashboard', status_code=303)

    tariffs = _showcase(await _public_tariffs(api))
    status = await _server_status(api)

    return render(
        request,
        'landing.html',
        {
            'tariffs': tariffs,
            'cheapest': _cheapest(tariffs),
            'status': status,
        },
    )


@router.get('/dashboard', response_class=HTMLResponse)
async def dashboard(
    request: Request,
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> HTMLResponse:
    profile = await with_fresh_token(session, store, api, api.me)

    if isinstance(profile, dict):
        # Профиль в сессии рисует шапку; обновляем на каждом открытии кабинета,
        # чтобы сумма в шапке не отставала от страницы.
        session.user = profile
        await store.save(session)

    status = await _optional(api, session, store, api.subscription, 'подписку')
    balance = await _optional(api, session, store, api.balance, 'баланс')
    await remember_available(session, store, balance)

    # Ручка отдаёт обёртку {has_subscription, subscription}: разворачиваем здесь,
    # чтобы шаблон не знал про форму ответа API.
    subscription = status.get('subscription') if isinstance(status, dict) else None

    # Ссылку подключения спрашиваем только при живой подписке: без неё ручка
    # отвечает 404, и это ожидаемо, а не поломка.
    connection = None
    if subscription:
        connection = await _optional(api, session, store, api.connection_link, 'ссылку подключения')

    return render(
        request,
        'dashboard.html',
        {
            'profile': profile if isinstance(profile, dict) else {},
            'subscription': subscription,
            'balance': balance if isinstance(balance, dict) else {},
            'connection': connection if isinstance(connection, dict) else {},
            'traffic_percent': _traffic_percent(subscription),
        },
        session=session,
    )


@router.get('/healthz')
async def healthz() -> dict[str, str]:
    """Проверка живости для Caddy и docker healthcheck.

    Нарочно не трогает ни Redis, ни бот: это проверка «процесс поднялся», а не
    «вся цепочка здорова» — иначе падение бота перезапускало бы портал.
    """
    return {'status': 'ok'}


async def available_balance(session: Session, store: SessionStore, api: CabinetApi) -> int | None:
    """Сумма, которой человек может заплатить: деньги плюс бонусные баллы.

    Правило репозитория: «хватает ли» считается по ``available_balance_kopeks``
    везде, где списывает ``subtract_user_balance`` (см. CLAUDE.md). В каталоге
    покупки (``/subscription/purchase-options``) бот присылает только денежный
    кошелёк, поэтому сумму берём из ``/balance`` — иначе человеку с баллами
    отказали бы в покупке, которая прошла бы.

    ``None`` — бот не ответил; вызывающий возвращается к денежному кошельку.
    """
    try:
        payload = await with_fresh_token(session, store, api, api.balance)
    except CabinetApiError as error:
        logger.info('Не удалось узнать доступную сумму', status_code=error.status_code)
        return None

    if not isinstance(payload, dict):
        return None

    await remember_available(session, store, payload)
    amount = payload.get('available_kopeks')
    return int(amount) if isinstance(amount, int) else None


async def remember_available(session: Session, store: SessionStore, balance: Any) -> None:
    """Положить в сессию сумму, доступную к оплате, — её показывает шапка.

    ``/auth/me`` отдаёт только денежный кошелёк, а платить человек может
    деньгами и бонусными баллами разом (``available_balance_kopeks`` в боте).
    Показать в шапке меньше — значит отговорить от покупки, которая прошла бы.
    """
    if not isinstance(balance, dict) or balance.get('available_kopeks') is None:
        return

    session.user = {**(session.user or {}), 'available_kopeks': balance['available_kopeks']}
    await store.save(session)


async def _public_tariffs(api: CabinetApi) -> list[dict[str, Any]]:
    """Каталог для витрины; отказ бота просто убирает блок цен."""
    try:
        payload = await api.public_tariffs()
    except CabinetApiError as error:
        logger.info('Витрина без каталога', status_code=error.status_code)
        return []

    raw = payload.get('tariffs') if isinstance(payload, dict) else None
    return [item for item in (raw if isinstance(raw, list) else []) if isinstance(item, dict)]


async def _server_status(api: CabinetApi) -> dict[str, Any] | None:
    try:
        payload = await api.server_status()
    except CabinetApiError:
        # Метрики недоступны или интеграция выключена — строку о серверах
        # просто не рисуем, обещать «всё работает» мы не можем.
        return None

    return payload if isinstance(payload, dict) and payload.get('enabled') else None


def _showcase(tariffs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Тарифы для витрины: только продающиеся, с готовыми сроками.

    У каждого оставляем один срок — отмеченный оператором как выгодный, иначе
    самый длинный: на витрине важна не вся сетка цен, а порядок суммы.
    """
    showcase: list[dict[str, Any]] = []

    for tariff in tariffs:
        if not tariff.get('is_available', True):
            continue
        periods = [p for p in tariff.get('periods') or [] if isinstance(p, dict) and p.get('days')]
        if not periods:
            continue
        chosen = next(
            (period for period in periods if period.get('is_highlighted')),
            max(periods, key=lambda period: int(period.get('days') or 0)),
        )
        showcase.append({**tariff, 'period': chosen})

    showcase.sort(key=lambda item: int(item['period'].get('price_kopeks') or 0))
    return showcase[:SHOWCASE_LIMIT]


def _cheapest(tariffs: list[dict[str, Any]]) -> int | None:
    """Самая низкая цена за месяц среди витринных тарифов — для строки «от…»."""
    prices = [
        int(tariff['period'].get('price_per_month_kopeks') or 0)
        for tariff in tariffs
        if int(tariff['period'].get('price_per_month_kopeks') or 0) > 0
    ]
    return min(prices) if prices else None


def _traffic_percent(subscription: Any) -> int | None:
    """Доля израсходованного трафика; None — лимита нет, полосу не рисуем."""
    if not isinstance(subscription, dict):
        return None

    try:
        limit = float(subscription.get('traffic_limit_gb') or 0)
        used = float(subscription.get('traffic_used_gb') or 0)
    except (TypeError, ValueError):
        return None

    if limit <= 0:
        return None
    return min(100, max(0, round(used / limit * 100)))


async def _optional(
    api: CabinetApi,
    session: Session,
    store: SessionStore,
    call,
    what: str,
):
    """Необязательный блок страницы: его отказ не должен ронять весь кабинет."""
    try:
        return await with_fresh_token(session, store, api, call)
    except CabinetApiError as error:
        logger.info('Не удалось получить данные для страницы', what=what, status_code=error.status_code)
        return None
