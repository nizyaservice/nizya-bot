"""Шаблоны Jinja: один экземпляр на процесс и общий контекст страниц.

Общий контекст (бренд, адрес поддержки, признак «вошёл») собирается здесь,
чтобы каждая страница не перечисляла его заново и не забывала половину.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .sessions import Session


TEMPLATES_DIR = Path(__file__).resolve().parent / 'templates'

_templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


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
