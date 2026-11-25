"""Реферальная программа: ссылка для друзей, начисления и кто пришёл.

Ссылок две, и это не дублирование. Бот отдаёт ссылку на себя (``referral_link``)
— она нужна тем, у кого телеграм есть. Портал добавляет свою
(``/login?ref=КОД``), и по ней человек заводит аккаунт прямо на сайте: код
доезжает скрытым полем до ``/auth/email/otp/verify``. Ради одной аудитории
терять вторую незачем, поэтому показываем обе.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ..api import CabinetApi, CabinetApiError
from ..config import PortalSettings
from ..deps import get_api, get_portal_settings, get_store, require_session, with_fresh_token
from ..sessions import Session, SessionStore
from ..templating import render


logger = structlog.get_logger(__name__)

router = APIRouter()


@router.get('/referral', response_class=HTMLResponse)
async def referral(
    request: Request,
    session: Session = Depends(require_session),
    api: CabinetApi = Depends(get_api),
    store: SessionStore = Depends(get_store),
    settings: PortalSettings = Depends(get_portal_settings),
) -> HTMLResponse:
    info = await with_fresh_token(session, store, api, api.referral)
    data = info if isinstance(info, dict) else {}

    invited: list[dict[str, Any]] = []
    try:
        listing = await with_fresh_token(session, store, api, api.referral_list)
        # Ручка отдаёт страницу списком `items` (ReferralListResponse).
        raw = listing.get('items') if isinstance(listing, dict) else None
        invited = [item for item in (raw if isinstance(raw, list) else []) if isinstance(item, dict)]
    except CabinetApiError as error:
        # Список приглашённых — необязательная часть экрана.
        logger.info('Не удалось получить список приглашённых', status_code=error.status_code)

    return render(
        request,
        'referral.html',
        {
            'info': data,
            'portal_link': _portal_referral_link(settings, data.get('referral_code')),
            'invited': invited,
        },
        session=session,
    )


def _portal_referral_link(settings: PortalSettings, code: Any) -> str | None:
    if not isinstance(code, str) or not code or not settings.public_url:
        return None
    return f'{settings.public_url.rstrip("/")}/login?ref={code}'
