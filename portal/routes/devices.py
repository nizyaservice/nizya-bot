"""Управление устройствами.

Список устройств, переименование и удаление — то же, что во вкладке «Мои
устройства» бота. Устройства живут в панели Remnawave, локальные имена — в базе
бота; портал ничего из этого не кэширует и спрашивает бота на каждом открытии.

Удаление необратимо (человеку придётся подключать устройство заново), поэтому
оба удаления идут через отдельный экран подтверждения: GET показывает вопрос,
списывает только POST с CSRF-токеном. Тот же порядок, что у покупки.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import structlog
from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from ..api import CabinetApi, CabinetApiError
from ..deps import check_csrf, get_api, get_store, require_session, with_fresh_token
from ..sessions import Session, SessionStore
from ..templating import render


logger = structlog.get_logger(__name__)

router = APIRouter()

#: Бот отвечает этим кодом, когда подписки у человека нет вовсе.
NOT_FOUND = 404


@router.get('/devices', response_class=HTMLResponse)
async def device_list(
    request: Request,
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> HTMLResponse:
    try:
        payload = await with_fresh_token(session, store, api, api.devices)
    except CabinetApiError as error:
        if error.status_code == NOT_FOUND:
            # Подписки нет — устройствам неоткуда взяться.
            return render(request, 'devices.html', _context(None), session=session)
        raise

    return render(request, 'devices.html', _context(payload), session=session)


@router.get('/devices/{hwid}', response_class=HTMLResponse)
async def device_card(
    request: Request,
    hwid: str,
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> HTMLResponse:
    """Карточка одного устройства: переименование и удаление."""
    payload = await with_fresh_token(session, store, api, api.devices)
    device = _find_device(_context(payload)['devices'], hwid)
    if device is None:
        return _gone(request, session)

    return render(request, 'device_card.html', {'device': device}, session=session)


@router.post('/devices/{hwid}/rename', response_class=HTMLResponse)
async def device_rename(
    request: Request,
    hwid: str,
    name: str = Form(''),
    csrf_token: str = Form(''),
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> Response:
    check_csrf(session, csrf_token)

    # Пустое имя — это снятие своего названия, а не ошибка: бот вернётся к
    # «платформа — модель», как в боте.
    cleaned = name.strip() or None

    async def call(token: str) -> Any:
        return await api.rename_device(token, hwid, cleaned)

    try:
        await with_fresh_token(session, store, api, call)
    except CabinetApiError as error:
        if error.status_code == NOT_FOUND:
            return _gone(request, session)
        return render(
            request,
            'error.html',
            {'title': 'Не переименовалось', 'message': error.detail, 'back_url': '/devices'},
            status_code=400,
            session=session,
        )

    return RedirectResponse(f'/devices/{quote(hwid, safe="")}', status_code=303)


@router.get('/devices/{hwid}/remove', response_class=HTMLResponse)
async def device_remove_confirm(
    request: Request,
    hwid: str,
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> HTMLResponse:
    payload = await with_fresh_token(session, store, api, api.devices)
    device = _find_device(_context(payload)['devices'], hwid)
    if device is None:
        return _gone(request, session)

    return render(request, 'device_remove.html', {'device': device}, session=session)


@router.post('/devices/{hwid}/remove', response_class=HTMLResponse)
async def device_remove(
    request: Request,
    hwid: str,
    csrf_token: str = Form(''),
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> Response:
    check_csrf(session, csrf_token)

    async def call(token: str) -> Any:
        return await api.delete_device(token, hwid)

    try:
        await with_fresh_token(session, store, api, call)
    except CabinetApiError as error:
        if error.status_code == NOT_FOUND:
            # Устройство уже отключили — цель достигнута, ругаться не на что.
            return RedirectResponse('/devices', status_code=303)
        logger.info('Не удалось удалить устройство', status_code=error.status_code)
        return render(
            request,
            'error.html',
            {'title': 'Не удалось отключить', 'message': error.detail, 'back_url': '/devices'},
            status_code=400,
            session=session,
        )

    return RedirectResponse('/devices', status_code=303)


@router.get('/devices-reset', response_class=HTMLResponse)
async def devices_reset_confirm(
    request: Request,
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> HTMLResponse:
    payload = await with_fresh_token(session, store, api, api.devices)
    return render(request, 'devices_reset.html', _context(payload), session=session)


@router.post('/devices-reset', response_class=HTMLResponse)
async def devices_reset(
    request: Request,
    csrf_token: str = Form(''),
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
) -> Response:
    check_csrf(session, csrf_token)

    try:
        await with_fresh_token(session, store, api, api.delete_all_devices)
    except CabinetApiError as error:
        return render(
            request,
            'error.html',
            {'title': 'Не удалось сбросить', 'message': error.detail, 'back_url': '/devices'},
            status_code=400,
            session=session,
        )

    return RedirectResponse('/devices', status_code=303)


def _context(payload: Any) -> dict[str, Any]:
    """Разобрать ответ бота и дать каждому устройству готовое имя."""
    data = payload if isinstance(payload, dict) else {}
    raw = data.get('devices')
    devices = [_device(item) for item in (raw if isinstance(raw, list) else []) if isinstance(item, dict)]

    return {
        'devices': devices,
        'total': int(data.get('total') or len(devices)),
        'device_limit': int(data.get('device_limit') or 0),
    }


def _device(item: dict[str, Any]) -> dict[str, Any]:
    """Имя как в боте: своё название, иначе «платформа — модель».

    Пустую строку алиаса считаем отсутствием названия: бот именно так его и
    снимает, а показывать пустую кнопку незачем.
    """
    local = (item.get('local_name') or '').strip()
    platform = (item.get('platform') or '').strip()
    model = (item.get('device_model') or '').strip()
    fallback = ' — '.join(part for part in (platform, model) if part) or 'Устройство'

    return {
        **item,
        'hwid': item.get('hwid') or '',
        'title': local or fallback,
        'local_name': local,
        'default_title': fallback,
    }


def _find_device(devices: list[dict[str, Any]], hwid: str) -> dict[str, Any] | None:
    return next((device for device in devices if device['hwid'] == hwid), None)


def _gone(request: Request, session: Session) -> HTMLResponse:
    return render(
        request,
        'error.html',
        {
            'title': 'Устройства больше нет',
            'message': 'Похоже, оно уже отключено. Откройте список заново.',
            'back_url': '/devices',
        },
        status_code=404,
        session=session,
    )
