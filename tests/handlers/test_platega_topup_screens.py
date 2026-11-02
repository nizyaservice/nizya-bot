"""Экраны пополнения через Platega: имя шлюза наружу не выносим.

Человек выбирает способ оплаты, а не провайдера: на экранах оставлено только
название способа («🏦 СБП», «💳 Карты (RUB)»). Отдельно закреплено, куда ведёт
«Назад» с экрана ввода суммы: к списку способов оплаты, а не в главное меню —
оттуда человек и пришёл, и чаще всего хочет сменить способ.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import settings
from app.handlers.balance import platega as platega_handlers
from app.utils.payment_utils import get_payment_methods_text


SBP = 2
RUB_CARD = 11


@pytest.fixture(autouse=True)
def _platega_on(monkeypatch):
    for key, value in {
        'PLATEGA_ENABLED': True,
        'PLATEGA_MERCHANT_ID': 'merchant',
        'PLATEGA_SECRET': 'secret',
        'PLATEGA_ACTIVE_METHODS': f'{SBP},{RUB_CARD}',
        'PLATEGA_MIN_AMOUNT_KOPEKS': 100,
        'PLATEGA_MAX_AMOUNT_KOPEKS': 10_000_000,
    }.items():
        monkeypatch.setattr(settings, key, value, raising=False)


def _message():
    message = MagicMock()
    message.edit_text = AsyncMock()
    message.message_id = 10
    message.chat = SimpleNamespace(id=7)
    return message


def _user():
    return SimpleNamespace(id=7, telegram_id=7, language='ru', restriction_topup=False)


async def _prompt(method_code: int):
    message = _message()
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=7, user_id=7))

    await platega_handlers._prompt_amount(message, _user(), state, method_code)

    text = message.edit_text.await_args.args[0]
    markup = message.edit_text.await_args.kwargs['reply_markup']
    return text, markup


def test_sbp_method_is_named_without_the_qr_suffix():
    assert settings.get_platega_method_display_title(SBP) == '🏦 СБП'
    assert settings.get_platega_method_display_name(SBP) == 'СБП'


@pytest.mark.asyncio
async def test_amount_prompt_shows_the_method_not_the_gateway():
    text, _ = await _prompt(RUB_CARD)

    assert text.startswith('<b>💳 Карты (RUB)</b>')
    assert 'Platega' not in text
    assert 'Введите сумму для пополнения' in text


@pytest.mark.asyncio
async def test_amount_prompt_back_returns_to_the_payment_methods():
    _, markup = await _prompt(SBP)

    back_callbacks = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data in ('balance_topup', 'back_to_menu', 'menu_balance')
    ]
    assert back_callbacks == ['balance_topup']


def test_payment_methods_screen_ends_with_the_prompt():
    """Ниже приглашения был список способов — то же, что на кнопках."""
    text = get_payment_methods_text('ru')

    assert text.rstrip().endswith('Выберите удобный для вас способ оплаты:')
    assert 'Выберите способ пополнения' not in text
