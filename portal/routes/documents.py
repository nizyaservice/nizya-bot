"""Документы сервиса: правила, политика конфиденциальности, оферта.

Открыты без входа нарочно. Их спрашивают до покупки — и человек, и платёжный
провайдер при проверке сайта; гейт на этих страницах означал бы, что оферту
нельзя прочитать, не заведя аккаунт.

Текст приходит готовым HTML — его писал оператор в админке, и показываем мы
его как разметку, иначе человек увидит теги. Это безопасно: политика портала
(``script-src 'self'``) не исполняет ни встроенные скрипты, ни обработчики в
атрибутах.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ..api import CabinetApi, CabinetApiError
from ..deps import get_api, get_session
from ..sessions import Session
from ..templating import render


logger = structlog.get_logger(__name__)

router = APIRouter()

#: Путь портала → (ручка бота, заголовок экрана).
DOCUMENTS = {
    'rules': ('rules', 'Правила сервиса'),
    'privacy': ('privacy-policy', 'Политика конфиденциальности'),
    'offer': ('public-offer', 'Публичная оферта'),
}

NOT_FOUND = 404


@router.get('/documents/{slug}', response_class=HTMLResponse)
async def document(
    request: Request,
    slug: str,
    session: Session | None = Depends(get_session),
    api: CabinetApi = Depends(get_api),
) -> HTMLResponse:
    known = DOCUMENTS.get(slug)
    if known is None:
        return render(
            request,
            'error.html',
            {'title': 'Документа нет', 'message': 'Такого документа у сервиса нет.', 'back_url': '/'},
            status_code=404,
            session=session,
        )

    endpoint, title = known
    language = _language_of(session)
    try:
        payload = await api.legal_document(endpoint, language)
    except CabinetApiError as error:
        # Оператор может скрыть документ из веба — это не поломка.
        logger.info('Документ недоступен', slug=slug, status_code=error.status_code)
        return render(
            request,
            'document.html',
            {'title': title, 'content': None},
            status_code=404 if error.status_code == NOT_FOUND else 200,
            session=session,
        )

    data = payload if isinstance(payload, dict) else {}
    return render(
        request,
        'document.html',
        {'title': title, 'content': data.get('content'), 'updated_at': data.get('updated_at')},
        session=session,
    )


def _language_of(session: Session | None) -> str:
    """Язык документа: из профиля вошедшего, иначе русский.

    Переключателя языка на портале нет — он русскоязычный; язык аккаунта
    человек меняет в боте, и документ мы показываем на том же языке.
    """
    language = (session.user or {}).get('language') if session else None
    return language if isinstance(language, str) and language else 'ru'
