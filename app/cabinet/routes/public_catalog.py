"""Публичные ручки витрины: каталог тарифов и статус серверов.

Нарочно без авторизации. Портал (``portal/``) показывает тарифы и статус
серверов тому, кто ещё не вошёл: решение «брать или нет» человек принимает до
регистрации, и требовать почту ради прайс-листа — лишний рубеж на входе.

Что здесь НЕ отдаётся: ничего, что зависит от конкретного человека. Цены —
базовые, без скидок промогруппы; признаков «куплен» и «текущий» нет. Как
только человек вошёл, портал переходит на ``/subscription/purchase-options``,
и цены там уже с его скидками. Поэтому цена с витрины — верхняя граница: она
может только уменьшиться после входа, но не вырасти.

Статус серверов кэшируется на ``STATUS_CACHE_SECONDS``: ручка открыта всем, а
за ней живой запрос к метрикам узла — без кэша любой обход страницы превращался
бы в нагрузку на него.
"""

from __future__ import annotations

import time
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.cabinet.dependencies import get_cabinet_db
from app.config import settings
from app.database.crud.tariff import get_tariffs_for_user
from app.services.server_status_service import ServerStatusError, ServerStatusService


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/public', tags=['Cabinet:Public'])

#: Насколько живёт общий ответ о статусе серверов.
STATUS_CACHE_SECONDS = 20.0

_status_service = ServerStatusService()
_status_cache: tuple[float, dict[str, Any]] | None = None


@router.get('/tariffs', summary='Каталог тарифов без авторизации')
async def public_tariffs(
    db: AsyncSession = Depends(get_cabinet_db),
    language: str = 'ru',
) -> dict[str, Any]:
    """Базовый прайс-лист: тарифы без ограничений по промогруппам.

    Тарифы, доступные только отдельным промогруппам, сюда не попадают —
    ``get_tariffs_for_user(db, None)`` их отсеивает. Показать их анониму
    значило бы пообещать цену, которую он не сможет оформить.
    """
    if not settings.is_tariffs_mode():
        # Классический режим продаж (сроки и опции по отдельности) витриной не
        # оформляется: пустой список честнее половины воронки.
        return {'sales_mode': settings.get_sales_mode(), 'tariffs': []}

    # Импорт внутри функции: модуль покупки тянет пол-бота, а эта ручка
    # вызывается редко — на старте приложения платить за это незачем.
    from app.cabinet.routes.subscription_modules.purchase import _build_tariff_response

    tariffs = await get_tariffs_for_user(db, None)

    items: list[dict[str, Any]] = []
    for tariff in tariffs:
        if getattr(tariff, 'is_daily', False):
            # Суточные тарифы оформляются своей воронкой, на витрине их нет.
            continue
        built = await _build_tariff_response(db, tariff, None, language, None, None)
        items.append(built)

    return {'sales_mode': 'tariffs', 'tariffs': items}


@router.get('/server-status', summary='Статус серверов без авторизации')
async def public_server_status() -> dict[str, Any]:
    """Доступность узлов по метрикам Xray.

    ``enabled=false`` — интеграция не настроена оператором; это не ошибка, и
    портал просто не рисует раздел. Отказ самих метрик — 503: страница скажет,
    что данных сейчас нет, вместо того чтобы показать пустой список как «всё
    легло».
    """
    # Кэш общий на процесс: без него открытая ручка била бы по метрикам узла.
    global _status_cache

    if settings.get_server_status_mode() != 'xray':
        return {'enabled': False, 'servers': [], 'total': 0, 'online': 0, 'offline': 0}

    now = time.monotonic()
    if _status_cache is not None and now - _status_cache[0] < STATUS_CACHE_SECONDS:
        return _status_cache[1]

    try:
        servers = await _status_service.get_servers()
    except ServerStatusError as error:
        logger.info('Публичный статус серверов недоступен', error=str(error))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Server status is temporarily unavailable',
        ) from error

    online = [server for server in servers if server.is_online]
    payload: dict[str, Any] = {
        'enabled': True,
        'total': len(servers),
        'online': len(online),
        'offline': len(servers) - len(online),
        'servers': [
            {
                'name': server.name,
                'display_name': server.display_name or server.name,
                'flag': server.flag or None,
                'latency_ms': server.latency_ms,
                'is_online': server.is_online,
            }
            for server in servers
        ],
    }

    _status_cache = (now, payload)
    return payload
