"""Синхронизация username бота и устойчивость старта к сбою сети.

Regression test for Telegram bug #650370.

A migrated bot (new token) kept generating gift-claim links pointing at the OLD bot because
those links read settings.BOT_USERNAME (a manually-maintained env) while the username is
actually derived from the token by Telegram. sync_bot_username() now overrides
settings.BOT_USERNAME from the live get_me() at startup, so every get_bot_username() caller
(gift links included) follows the real bot and a token swap self-heals.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramNetworkError

from app.config import settings
from app.utils.bot_identity import GET_ME_RETRY_DELAYS, sync_bot_username


@pytest.mark.asyncio
async def test_sync_overrides_stale_username(monkeypatch):
    monkeypatch.setattr(settings, 'BOT_USERNAME', 'old_bot')
    fake_bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username='new_bot')))

    await sync_bot_username(fake_bot)

    assert settings.BOT_USERNAME == 'new_bot'


@pytest.mark.asyncio
async def test_sync_keeps_config_on_get_me_failure(monkeypatch):
    monkeypatch.setattr(settings, 'BOT_USERNAME', 'old_bot')
    fake_bot = SimpleNamespace(get_me=AsyncMock(side_effect=RuntimeError('network down')))

    await sync_bot_username(fake_bot)

    assert settings.BOT_USERNAME == 'old_bot'


@pytest.mark.asyncio
async def test_sync_noop_when_already_correct(monkeypatch):
    monkeypatch.setattr(settings, 'BOT_USERNAME', 'same_bot')
    fake_bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username='same_bot')))

    await sync_bot_username(fake_bot)

    assert settings.BOT_USERNAME == 'same_bot'


# ─── сбой сети на старте ───


@pytest.fixture
def no_sleep(monkeypatch):
    """Паузы между попытками в тестах не выжидаем."""
    slept: list[float] = []

    async def fake_sleep(delay):
        slept.append(delay)

    monkeypatch.setattr('app.utils.bot_identity.asyncio.sleep', fake_sleep)
    return slept


@pytest.mark.asyncio
async def test_network_blip_is_retried(monkeypatch, no_sleep):
    """Одна заминка сети не должна оставлять username пустым.

    В прод-`.env` BOT_USERNAME не задан вовсе, поэтому промах get_me() означал бы
    битые ссылки на бота (подарки, рефералка) до следующего перезапуска.
    """
    monkeypatch.setattr(settings, 'BOT_USERNAME', '')
    answers = [TelegramNetworkError(method=None, message='timeout'), SimpleNamespace(username='nizya_bot')]

    async def get_me():
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    await sync_bot_username(SimpleNamespace(get_me=get_me))

    assert settings.BOT_USERNAME == 'nizya_bot'
    assert no_sleep == [GET_ME_RETRY_DELAYS[0]]


@pytest.mark.asyncio
async def test_startup_survives_a_long_outage(monkeypatch, no_sleep):
    """Сеть недоступна все попытки — старт продолжается, а не падает."""
    monkeypatch.setattr(settings, 'BOT_USERNAME', 'from_config')
    get_me = AsyncMock(side_effect=TelegramNetworkError(method=None, message='timeout'))

    await sync_bot_username(SimpleNamespace(get_me=get_me))

    assert settings.BOT_USERNAME == 'from_config'
    assert get_me.await_count == len(GET_ME_RETRY_DELAYS) + 1
    assert no_sleep == list(GET_ME_RETRY_DELAYS)


@pytest.mark.asyncio
async def test_rejection_by_telegram_is_not_retried(monkeypatch, no_sleep):
    """Неверный токен повторять незачем: ответ не изменится."""
    monkeypatch.setattr(settings, 'BOT_USERNAME', 'from_config')
    get_me = AsyncMock(side_effect=RuntimeError('Unauthorized'))

    await sync_bot_username(SimpleNamespace(get_me=get_me))

    assert get_me.await_count == 1
    assert no_sleep == []


def test_startup_has_no_unprotected_get_me():
    """Старт обязан переживать недоступность Telegram.

    Второй, незащищённый `await bot.get_me()` в main.py ронял весь запуск по
    таймауту к api.telegram.org, хотя username уже взят в setup_bot().
    """
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[1] / 'main.py').read_text(encoding='utf-8')

    assert 'await bot.get_me()' not in source
