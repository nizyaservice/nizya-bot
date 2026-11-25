"""Статус серверов — публичная страница.

Открыта без входа нарочно: «работает ли сервис прямо сейчас» — первый вопрос
и у того, кто выбирает VPN, и у того, у кого он перестал подключаться. Просить
ради ответа почту незачем.

Данные берутся у бота (``/public/server-status``), он же их кэширует — страницу
можно обновлять сколько угодно, до метрик узла каждое обновление не доходит.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ..api import CabinetApi, CabinetApiError
from ..deps import get_api, get_session
from ..sessions import Session
from ..templating import render


logger = structlog.get_logger(__name__)

router = APIRouter()


@router.get('/status', response_class=HTMLResponse)
async def server_status(
    request: Request,
    session: Session | None = Depends(get_session),
    api: CabinetApi = Depends(get_api),
) -> HTMLResponse:
    try:
        payload = await api.server_status()
    except CabinetApiError as error:
        # Метрики не ответили. Пустой список показывать нельзя: он читается как
        # «всё легло», а мы просто не знаем.
        logger.info('Статус серверов недоступен', status_code=error.status_code)
        return render(request, 'status.html', {'unknown': True}, session=session)

    data = payload if isinstance(payload, dict) else {}
    if not data.get('enabled'):
        # Оператор не подключал метрики — раздела у сервиса попросту нет.
        return render(request, 'status.html', {'disabled': True}, session=session)

    raw = data.get('servers')
    servers = [item for item in (raw if isinstance(raw, list) else []) if isinstance(item, dict)]

    return render(
        request,
        'status.html',
        {
            'total': int(data.get('total') or len(servers)),
            'online_count': int(data.get('online') or 0),
            'offline_count': int(data.get('offline') or 0),
            # Недоступные — наверх: именно они и есть новость на этой странице.
            'servers': sorted(servers, key=_order),
        },
        session=session,
    )


def _order(server: dict[str, Any]) -> tuple[int, int, str]:
    """Сначала упавшие, затем быстрые, затем по имени."""
    latency = server.get('latency_ms')
    return (
        0 if not server.get('is_online') else 1,
        int(latency) if isinstance(latency, int) and latency > 0 else 10**6,
        str(server.get('display_name') or server.get('name') or ''),
    )
