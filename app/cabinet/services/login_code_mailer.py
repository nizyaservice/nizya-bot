"""Письмо с одноразовым кодом для входа на портал.

Отдельный маленький модуль, а не метод ``EmailService``: у остальных писем
сервиса есть редактируемые в админке шаблоны (``NotificationType`` + запись в
БД), и чтобы завести такой же для кода входа, пришлось бы тянуть новый тип
через реестр уведомлений, редактор шаблонов и его настройки. Тут хватает текста
из локалей — он тоже переводится и правится, только в `locales/*.json`.

Код стоит в теле письма, а не в заголовке: тему видно на заблокированном
экране в уведомлении, и код входа лучше туда не выносить.
"""

from __future__ import annotations

import asyncio
import html

import structlog

from app.localization.texts import get_texts

from ..auth.login_otp import LOGIN_OTP_TTL_SECONDS
from .email_service import email_service


logger = structlog.get_logger(__name__)

_DEFAULT_SUBJECT = 'Код для входа в личный кабинет'
_DEFAULT_BODY = (
    '<p>Ваш код для входа: <b style="font-size:20px;letter-spacing:2px">{code}</b></p>'
    '<p>Код действует {minutes} минут и подходит для одного входа.</p>'
    '<p>Если вход запрашивали не вы — просто не вводите код, ничего делать не нужно.</p>'
)


async def send_login_code(email: str, code: str, language: str | None = None) -> bool:
    """Отправить код входа. ``False`` — письмо не ушло (звать повторно решает вызывающий)."""
    texts = get_texts(language or 'ru')
    minutes = LOGIN_OTP_TTL_SECONDS // 60

    subject = texts.t('CABINET_LOGIN_CODE_SUBJECT', _DEFAULT_SUBJECT)
    body_html = texts.t('CABINET_LOGIN_CODE_BODY', _DEFAULT_BODY).format(
        code=html.escape(code),
        minutes=minutes,
    )

    # send_email синхронный (SMTP), поэтому уводим в поток — как остальные
    # письма кабинета.
    sent = await asyncio.to_thread(
        email_service.send_email,
        to_email=email,
        subject=subject,
        body_html=body_html,
    )
    if not sent:
        logger.warning('Письмо с кодом входа не отправлено')
    return bool(sent)
