"""Экран ошибки создания платежа Platega не должен быть тупиком.

Отказ приходит от конкретного метода — например, картой не собрался каскад, —
а сообщение приходило вовсе без кнопок: человек оставался в чате с «попробуйте
позже» и без единого способа вернуться к выбору способа оплаты, хотя соседний
способ прошёл бы.
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


INTERNATIONAL_CARD = 12  # метод, на котором споткнулся владелец


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    for key, value in {
        'PLATEGA_ENABLED': True,
        'PLATEGA_MERCHANT_ID': 'merchant',
        'PLATEGA_SECRET': 'secret',
        'PLATEGA_ACTIVE_METHODS': str(INTERNATIONAL_CARD),
        'PLATEGA_MIN_AMOUNT_KOPEKS': 100,
        'PLATEGA_MAX_AMOUNT_KOPEKS': 100000000,
    }.items():
        monkeypatch.setattr(settings, key, value, raising=False)


def _message():
    message = MagicMock()
    message.answer = AsyncMock()
    message.bot = SimpleNamespace()
    return message


def _user():
    return SimpleNamespace(id=7, telegram_id=7, language='ru', restriction_topup=False)


async def _state() -> FSMContext:
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=7, user_id=7))
    await state.update_data(payment_method='platega', platega_method=INTERNATIONAL_CARD)
    return state


def _callbacks(markup) -> list[str]:
    if markup is None:
        return []
    return [button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data]


async def _run_failing_payment(monkeypatch, *, result) -> MagicMock:
    service = SimpleNamespace(create_platega_payment=AsyncMock(**result))
    monkeypatch.setattr(platega_handlers, 'PaymentService', lambda bot: service)

    message = _message()
    await platega_handlers.process_platega_payment_amount(message, _user(), MagicMock(), 50000, await _state())
    return message


@pytest.mark.asyncio
async def test_error_screen_offers_the_way_back(monkeypatch):
    """Провайдер ответил без ссылки на оплату."""
    message = await _run_failing_payment(monkeypatch, result={'return_value': None})

    message.answer.assert_awaited_once()
    assert 'balance_topup' in _callbacks(message.answer.await_args.kwargs.get('reply_markup'))


@pytest.mark.asyncio
async def test_exception_screen_offers_the_way_back(monkeypatch):
    """Запрос к провайдеру упал с исключением — экран тот же."""
    message = await _run_failing_payment(monkeypatch, result={'side_effect': RuntimeError('platega is down')})

    message.answer.assert_awaited_once()
    assert 'balance_topup' in _callbacks(message.answer.await_args.kwargs.get('reply_markup'))


@pytest.mark.asyncio
async def test_error_text_is_kept(monkeypatch):
    """Кнопка добавлена к прежнему тексту, а не вместо него."""
    message = await _run_failing_payment(monkeypatch, result={'return_value': {}})

    (text,) = message.answer.await_args.args
    assert 'Platega' in text
