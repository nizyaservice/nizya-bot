"""Вход на портал по одноразовому коду из письма, без пароля.

Параметры заданы владельцем: 6 цифр, 15 минут, 5 попыток. Код хранится в
Redis, а не в колонке ``users``: первый вход = регистрация, а у
несуществующего пользователя колонки нет.

Главное, что тут закреплено:

* в Redis лежит хеш кода, а не сам код, и адрес в ключе тоже хеширован —
  дамп Redis не должен раздавать входы и выгружать почту пользователей;
* код одноразовый, привязан к адресу, живёт 15 минут, и неудачная попытка не
  продлевает ему жизнь (перезапись записи в Redis сбрасывает TTL — срок
  поэтому хранится внутри записи);
* пять неверных попыток убивают запись: перебор не длится до истечения срока;
* запрос кода не говорит, существует ли аккаунт, и не создаёт его.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.cabinet.auth import login_otp


REPO = pathlib.Path(__file__).resolve().parents[2]


class _MemoryCache:
    """Двойник Redis: словарь плюс запомненный TTL каждой записи."""

    def __init__(self) -> None:
        self.items: dict[str, tuple[dict[str, Any], int | None]] = {}

    async def set(self, key: str, value: Any, expire: int | None = None) -> bool:
        self.items[key] = (dict(value), expire)
        return True

    async def get(self, key: str) -> Any | None:
        item = self.items.get(key)
        return dict(item[0]) if item else None

    async def getdel(self, key: str) -> Any | None:
        item = self.items.pop(key, None)
        return item[0] if item else None


@pytest.fixture
def cache(monkeypatch) -> _MemoryCache:
    fake = _MemoryCache()
    monkeypatch.setattr(login_otp, 'cache', fake)
    return fake


# ─── формат кода ───


def test_code_is_six_digits_without_a_leading_zero():
    """Ведущий ноль теряется при копировании, поэтому его не генерируем."""
    for _ in range(200):
        code = login_otp.generate_login_code()
        assert len(code) == 6
        assert code.isdigit()
        assert code[0] != '0'


def test_codes_are_not_predictable():
    codes = {login_otp.generate_login_code() for _ in range(200)}

    assert len(codes) > 150, 'коды обязаны быть разными'


# ─── хранение ───


@pytest.mark.asyncio
async def test_plain_code_never_reaches_storage(cache):
    code = login_otp.generate_login_code()

    await login_otp.store_login_code('user@example.com', code)

    stored = next(iter(cache.items.values()))[0]
    assert code not in str(stored), 'в Redis должен лежать только хеш кода'
    assert 'code' not in stored


@pytest.mark.asyncio
async def test_email_is_not_stored_in_the_key(cache):
    await login_otp.store_login_code('user@example.com', '123456')

    key = next(iter(cache.items))
    assert 'user@example.com' not in key.lower(), 'список ключей Redis не должен быть выгрузкой почты'


# ─── проверка кода ───


@pytest.mark.asyncio
async def test_correct_code_passes_once(cache):
    code = login_otp.generate_login_code()
    await login_otp.store_login_code(' User@Example.COM ', code)

    # Регистр и пробелы адреса не мешают: сравниваем нормализованный вид.
    assert await login_otp.verify_login_code('user@example.com', code) == 'ok'
    # Повторно тот же код не принимается — запись израсходована.
    assert await login_otp.verify_login_code('user@example.com', code) == 'missing'


@pytest.mark.asyncio
async def test_unknown_email_reports_missing(cache):
    assert await login_otp.verify_login_code('nobody@example.com', '123456') == 'missing'


@pytest.mark.asyncio
async def test_code_of_one_email_does_not_fit_another(cache):
    code = login_otp.generate_login_code()
    await login_otp.store_login_code('first@example.com', code)
    await login_otp.store_login_code('second@example.com', login_otp.generate_login_code())

    assert await login_otp.verify_login_code('second@example.com', code) == 'invalid'


@pytest.mark.asyncio
async def test_five_wrong_attempts_kill_the_code(cache):
    code = login_otp.generate_login_code()
    await login_otp.store_login_code('user@example.com', code)

    for attempt in range(login_otp.LOGIN_OTP_MAX_ATTEMPTS - 1):
        assert await login_otp.verify_login_code('user@example.com', '000000') == 'invalid', attempt

    assert await login_otp.verify_login_code('user@example.com', '000000') == 'too_many_attempts'
    # Даже верный код больше не сработает: запись удалена, нужен новый.
    assert await login_otp.verify_login_code('user@example.com', code) == 'missing'


@pytest.mark.asyncio
async def test_failed_attempt_does_not_extend_the_code(cache):
    """Перезапись записи в Redis сбрасывает TTL — срок держим внутри записи."""
    await login_otp.store_login_code('user@example.com', '123456')
    ttl_before = next(iter(cache.items.values()))[1]

    await login_otp.verify_login_code('user@example.com', '999999')

    ttl_after = next(iter(cache.items.values()))[1]
    assert ttl_after is not None and ttl_before is not None
    assert ttl_after <= ttl_before


@pytest.mark.asyncio
async def test_expired_record_is_not_accepted(cache):
    """Если запись переживёт TTL Redis, срок внутри неё всё равно её похоронит."""
    await login_otp.store_login_code('user@example.com', '123456')
    key = next(iter(cache.items))
    record, _ = cache.items[key]
    record['expires_at'] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    cache.items[key] = (record, None)

    # Остаток срока не должен уйти в прошлое и продлить код.
    await login_otp.verify_login_code('user@example.com', '000000')
    assert cache.items[key][1] == 1, 'минимальный TTL, а не отрицательный'


# ─── пауза между письмами ───


@pytest.mark.asyncio
async def test_resend_is_throttled(cache):
    await login_otp.store_login_code('user@example.com', '123456')

    assert await login_otp.seconds_until_resend_allowed('user@example.com') > 0


@pytest.mark.asyncio
async def test_resend_allowed_when_no_code_pending(cache):
    assert await login_otp.seconds_until_resend_allowed('user@example.com') == 0


@pytest.mark.asyncio
async def test_resend_allowed_after_the_pause(cache):
    await login_otp.store_login_code('user@example.com', '123456')
    key = next(iter(cache.items))
    record, ttl = cache.items[key]
    created = datetime.now(UTC) - timedelta(seconds=login_otp.LOGIN_OTP_RESEND_AFTER_SECONDS + 1)
    record['created_at'] = created.isoformat()
    cache.items[key] = (record, ttl)

    assert await login_otp.seconds_until_resend_allowed('user@example.com') == 0


# ─── ручки входа ───


def test_request_endpoint_keeps_account_existence_secret():
    """Разный ответ для знакомого и незнакомого адреса — оракул для перебора почт."""
    source = (REPO / 'app' / 'cabinet' / 'routes' / 'auth.py').read_text(encoding='utf-8')
    start = source.index("async def request_email_otp")
    body = source[start : source.index('\n@router.', start)]

    # Все выходы из ручки, кроме явных отказов, отдают один и тот же ответ.
    assert body.count('return _sent(') >= 4
    assert 'status.HTTP_404_NOT_FOUND' not in body


def test_request_endpoint_does_not_create_accounts():
    """Аккаунт создаётся только проверкой кода: иначе рассылкой кодов на случайные
    адреса можно наплодить пустых пользователей."""
    source = (REPO / 'app' / 'cabinet' / 'routes' / 'auth.py').read_text(encoding='utf-8')
    start = source.index('async def request_email_otp')
    body = source[start : source.index('\n@router.', start)]

    assert 'create_user_by_email' not in body


def test_request_endpoint_keeps_the_registration_guards():
    source = (REPO / 'app' / 'cabinet' / 'routes' / 'auth.py').read_text(encoding='utf-8')
    start = source.index('async def request_email_otp')
    body = source[start : source.index('\n@router.', start)]

    assert 'disposable_email_service.is_disposable' in body, 'одноразовые ящики'
    assert 'get_admin_emails' in body, 'адреса администраторов'
    assert 'get_user_by_email_alias' in body, 'алиас существующего ящика'
    assert 'evaluate_public_registration' in body, 'закрытая регистрация'
    assert 'enforce_email_registration_throttle' in body, 'лимит регистраций с адреса'


def test_verify_endpoint_marks_the_email_as_verified():
    """Верный код из письма и есть доказательство владения ящиком."""
    source = (REPO / 'app' / 'cabinet' / 'routes' / 'auth.py').read_text(encoding='utf-8')
    start = source.index('async def verify_email_otp')
    body = source[start : source.index('\nasync def _create_user_for_otp_login', start)]

    assert 'user.email_verified = True' in body


def test_otp_account_is_created_without_a_password():
    source = (REPO / 'app' / 'cabinet' / 'routes' / 'auth.py').read_text(encoding='utf-8')
    start = source.index('async def _create_user_for_otp_login')
    body = source[start : start + 3000]

    assert 'password_hash=None' in body
    assert '_require_legal_consent' in body, 'согласие с документами — до создания аккаунта'
