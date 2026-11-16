"""Одноразовые коды для входа на портал по почте, без пароля.

Хранятся в Redis, а не в колонке ``users``: код запрашивают и до появления
аккаунта (первый вход = регистрация), а у несуществующего пользователя колонки
нет. Тот же приём уже используется для кодов слияния аккаунтов
(``merge_service``).

В Redis лежит не сам код, а его хеш: дамп Redis не должен раздавать входы в
чужие кабинеты. Счётчик попыток живёт рядом с кодом, поэтому перебор гасится
вместе с истечением кода, без отдельной записи.

Параметры заданы владельцем: 6 цифр, 15 минут, 5 попыток.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog

from app.utils.cache import cache, cache_key


logger = structlog.get_logger(__name__)

LOGIN_OTP_PREFIX = 'login_otp'
LOGIN_OTP_TTL_SECONDS = 900  # 15 минут
LOGIN_OTP_MAX_ATTEMPTS = 5
#: Не чаще одного письма в минуту на адрес — чтобы ящик нельзя было завалить.
LOGIN_OTP_RESEND_AFTER_SECONDS = 60

VerifyResult = Literal['ok', 'invalid', 'missing', 'too_many_attempts']


def normalize_email(email: str | None) -> str:
    return (email or '').strip().lower()


def generate_login_code() -> str:
    """Шестизначный код. Без ведущего нуля — его теряют при копировании."""
    return str(secrets.randbelow(900000) + 100000)


def _code_hash(email: str, code: str) -> str:
    """Хеш кода, привязанный к адресу: код от одного ящика не подойдёт к другому."""
    return hashlib.sha256(f'{normalize_email(email)}:{code}'.encode()).hexdigest()


def _key(email: str) -> str:
    # Адрес в ключе не храним открытым текстом, иначе список ключей Redis
    # становится выгрузкой почты пользователей.
    return cache_key(LOGIN_OTP_PREFIX, hashlib.sha256(normalize_email(email).encode()).hexdigest())


async def store_login_code(email: str, code: str) -> None:
    """Запомнить код для адреса, затерев предыдущий."""
    now = datetime.now(UTC)
    await cache.set(
        _key(email),
        {
            'code_hash': _code_hash(email, code),
            'attempts': 0,
            'created_at': now.isoformat(),
            # Срок держим в записи, а не только в TTL Redis: по нему
            # пересчитывается остаток, когда запись перезаписывается после
            # неудачной попытки — иначе каждая ошибка продлевала бы код.
            'expires_at': (now + timedelta(seconds=LOGIN_OTP_TTL_SECONDS)).isoformat(),
        },
        expire=LOGIN_OTP_TTL_SECONDS,
    )


def _remaining_ttl(record: dict[str, Any]) -> int:
    """Сколько секунд коду осталось жить; минимум 1, иначе Redis сочтёт запись вечной."""
    try:
        expires_at = datetime.fromisoformat(str(record.get('expires_at')))
    except ValueError:
        return LOGIN_OTP_TTL_SECONDS
    return max(1, int((expires_at - datetime.now(UTC)).total_seconds()))


async def peek_login_code(email: str) -> dict[str, Any] | None:
    """Запись о коде без расходования попытки; ``None`` — кода нет или истёк."""
    data: Any = await cache.get(_key(email))
    return data if isinstance(data, dict) else None


async def seconds_until_resend_allowed(email: str) -> int:
    """Сколько ждать до следующего письма; 0 — можно отправлять."""
    record = await peek_login_code(email)
    if not record:
        return 0

    try:
        created_at = datetime.fromisoformat(str(record.get('created_at')))
    except ValueError:
        return 0

    elapsed = (datetime.now(UTC) - created_at).total_seconds()
    remaining = LOGIN_OTP_RESEND_AFTER_SECONDS - int(elapsed)
    return max(0, remaining)


async def verify_login_code(email: str, code: str) -> VerifyResult:
    """Проверить код и израсходовать попытку.

    ``ok`` — код верный и запись удалена (повторно его не примут).
    ``missing`` — кода нет или он истёк. ``too_many_attempts`` — попытки
    кончились, запись удалена: новый код придётся запросить заново.
    """
    record = await peek_login_code(email)
    if not record:
        return 'missing'

    attempts = int(record.get('attempts') or 0)
    if attempts >= LOGIN_OTP_MAX_ATTEMPTS:
        await clear_login_code(email)
        return 'too_many_attempts'

    expected = str(record.get('code_hash') or '')
    if expected and hmac.compare_digest(expected, _code_hash(email, code)):
        await clear_login_code(email)
        return 'ok'

    attempts += 1
    if attempts >= LOGIN_OTP_MAX_ATTEMPTS:
        # Последняя попытка израсходована — запись убираем сразу, чтобы перебор
        # не продолжался до истечения TTL.
        await clear_login_code(email)
        logger.warning('Код входа исчерпал попытки', attempts=attempts)
        return 'too_many_attempts'

    record['attempts'] = attempts
    # Остаток срока считаем сами: продлевать жизнь кода неудачными попытками нельзя.
    await cache.set(_key(email), record, expire=_remaining_ttl(record))
    return 'invalid'


async def clear_login_code(email: str) -> None:
    await cache.getdel(_key(email))
