"""Экран доезжает, даже если сообщение с кнопкой боту больше не видно.

В колбэке вместо сообщения приходит ``InaccessibleMessage`` — человек удалил
сообщение или очистил чат. Методов отправки у этой модели нет, а ветка для неё
вызывала именно их: исключение гасилось, и человек не получал вообще ничего.
Теперь отправляем новым сообщением через ``bot`` по ``chat_id``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Chat, InaccessibleMessage

from app.utils import photo_message as pm


CHAT_ID = 474467678


def _inaccessible() -> InaccessibleMessage:
    # date у InaccessibleMessage — литеральный 0, так его описывает Telegram.
    return InaccessibleMessage(chat=Chat(id=CHAT_ID, type='private'), message_id=4, date=0)


def _callback(bot) -> SimpleNamespace:
    return SimpleNamespace(
        message=_inaccessible(),
        from_user=SimpleNamespace(id=CHAT_ID, language_code='ru'),
        bot=bot,
        answer=AsyncMock(),
    )


def _bot(*, photo_error: Exception | None = None):
    return SimpleNamespace(
        send_photo=AsyncMock(side_effect=photo_error, return_value=SimpleNamespace(photo=None)),
        send_message=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_screen_with_banner_is_sent_as_a_new_photo(monkeypatch):
    monkeypatch.setattr(pm.settings, 'ENABLE_LOGO_MODE', True)
    bot = _bot()
    callback = _callback(bot)

    await pm.edit_or_answer_photo(callback=callback, caption='экран', keyboard=None, media='banner_file_id')

    bot.send_photo.assert_awaited_once()
    assert bot.send_photo.await_args.kwargs['chat_id'] == CHAT_ID
    assert bot.send_photo.await_args.kwargs['photo'] == 'banner_file_id'
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_photo_failure_falls_back_to_text(monkeypatch):
    monkeypatch.setattr(pm.settings, 'ENABLE_LOGO_MODE', True)
    bot = _bot(photo_error=TelegramBadRequest(method=None, message='Bad Request: wrong file identifier'))
    callback = _callback(bot)

    await pm.edit_or_answer_photo(callback=callback, caption='экран', keyboard=None, media='banner_file_id')

    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs['chat_id'] == CHAT_ID
    assert bot.send_message.await_args.kwargs['text'] == 'экран'


@pytest.mark.asyncio
async def test_without_logo_mode_the_screen_goes_as_text(monkeypatch):
    monkeypatch.setattr(pm.settings, 'ENABLE_LOGO_MODE', False)
    monkeypatch.setattr(pm, 'get_logo_media', lambda: None)
    bot = _bot()
    callback = _callback(bot)

    await pm.edit_or_answer_photo(callback=callback, caption='экран', keyboard=None)

    bot.send_message.assert_awaited_once()
    bot.send_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_screen_is_resent_when_the_message_is_gone(monkeypatch):
    """``safe_edit_or_resend`` зовут с callback.message в тикетах — тот же случай."""
    bot = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr(pm, '_bot_of', lambda event: bot)

    await pm.safe_edit_or_resend(_inaccessible(), 'страница тикета')

    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs['chat_id'] == CHAT_ID


def test_bot_of_returns_none_without_context():
    """У отвязанной модели обращение к .bot бросает исключение, а не отдаёт None."""

    class Detached:
        @property
        def bot(self):
            raise RuntimeError('контекст бота не проставлен')

    assert pm._bot_of(Detached()) is None
    assert pm._bot_of(SimpleNamespace(bot='сам бот')) == 'сам бот'
