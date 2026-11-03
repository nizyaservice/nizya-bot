"""Регистрация не должна срываться, если сообщение с кнопкой уже недоступно.

В колбэке вместо сообщения приезжает ``InaccessibleMessage`` — бот это
сообщение больше не видит (человек его удалил). У модели нет ни ``edit_text``,
ни ``answer``, поэтому шаг «правила → политика» падал с AttributeError
(``'InaccessibleMessage' object has no attribute 'edit_text'``) мимо всех
обработчиков ошибок Telegram, и человек застревал в регистрации без политики.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, InaccessibleMessage, Message

from app.handlers import start as start_handlers
from app.utils.long_messages import sendable_message


CHAT_ID = 474467678


def _inaccessible() -> InaccessibleMessage:
    return InaccessibleMessage(chat=Chat(id=CHAT_ID, type='private'), message_id=4, date=datetime.fromtimestamp(0, UTC))


def _accessible() -> Message:
    return Message(chat=Chat(id=CHAT_ID, type='private'), message_id=4, date=datetime.now(UTC))


def _state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=CHAT_ID, user_id=CHAT_ID))


def test_inaccessible_message_is_not_sendable():
    assert sendable_message(_inaccessible()) is None
    assert sendable_message(None) is None
    assert sendable_message(_accessible()) is not None


@pytest.mark.asyncio
async def test_policy_is_sent_as_a_new_message_when_the_button_message_is_gone(monkeypatch):
    monkeypatch.setattr(
        start_handlers.PrivacyPolicyService,
        'get_policy',
        AsyncMock(return_value=SimpleNamespace(is_enabled=True, content='<p>Политика</p>')),
    )
    bot = SimpleNamespace(send_message=AsyncMock(return_value=_accessible()))
    callback = SimpleNamespace(message=_inaccessible(), from_user=SimpleNamespace(id=CHAT_ID), bot=bot)
    state = _state()

    shown = await start_handlers._show_privacy_policy_after_rules(callback, state, None, 'ru')

    assert shown is True, 'шаг регистрации обязан состояться'
    bot.send_message.assert_awaited()
    assert bot.send_message.await_args.kwargs['chat_id'] == CHAT_ID
    assert await state.get_state() == start_handlers.RegistrationStates.waiting_for_privacy_policy_accept.state


@pytest.mark.asyncio
async def test_policy_still_edits_the_message_when_it_is_accessible(monkeypatch):
    """Обычный путь не меняется: политика правит то же сообщение."""
    monkeypatch.setattr(
        start_handlers.PrivacyPolicyService,
        'get_policy',
        AsyncMock(return_value=SimpleNamespace(is_enabled=True, content='<p>Политика</p>')),
    )
    edit_long_text = AsyncMock(return_value=_accessible())
    monkeypatch.setattr(start_handlers, 'edit_long_text', edit_long_text)
    bot = SimpleNamespace(send_message=AsyncMock())
    callback = SimpleNamespace(message=_accessible(), from_user=SimpleNamespace(id=CHAT_ID), bot=bot)

    shown = await start_handlers._show_privacy_policy_after_rules(callback, _state(), None, 'ru')

    assert shown is True
    edit_long_text.assert_awaited_once()
    bot.send_message.assert_not_awaited()
