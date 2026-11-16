"""Шаблоны Jinja: один экземпляр на процесс и общий контекст страниц.

Общий контекст (бренд, адрес поддержки, признак «вошёл») собирается здесь,
чтобы каждая страница не перечисляла его заново и не забывала половину.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .sessions import Session


TEMPLATES_DIR = Path(__file__).resolve().parent / 'templates'

#: Коды статусов подписки (SubscriptionStatus в боте) по-русски: в API они
#: приходят как есть, а показывать человеку 'expired' незачем.
STATUS_LABELS = {
    'active': 'активна',
    'trial': 'пробная',
    'expired': 'истекла',
    'disabled': 'отключена',
    'limited': 'ограничена',
    'pending': 'оформляется',
}


def format_moment(value: Any) -> str:
    """Дата из API (ISO-строка) в привычном виде.

    Время не показываем: до минут тут никому нет дела, а часовой пояс ответа
    от пояса человека может отличаться.
    """
    if not isinstance(value, str) or not value:
        return '—'
    try:
        return datetime.fromisoformat(value).strftime('%d.%m.%Y')
    except ValueError:
        return value


def status_label(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return '—'
    return STATUS_LABELS.get(value, value)


_templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
_templates.env.filters['moment'] = format_moment
_templates.env.filters['status'] = status_label


def render(
    request: Request,
    template: str,
    context: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
    session: Session | None = None,
) -> HTMLResponse:
    """Отрендерить страницу с общим контекстом.

    ``session`` передаётся там, где страница уже достала её из зависимости:
    по ней рисуется шапка и берётся CSRF-токен для форм.
    """
    settings = get_settings()
    payload: dict[str, Any] = {
        'brand_name': settings.brand_name,
        'support_url': settings.support_url,
        'session': session,
        'user': session.user if session else None,
        'csrf_token': session.csrf_token if session else '',
    }
    payload.update(context or {})
    return _templates.TemplateResponse(request, template, payload, status_code=status_code)
