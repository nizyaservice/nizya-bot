"""Тестовая отправка рассылки админам с экрана предпросмотра.

Флоу: админ выбирает аудиторию, составляет сообщение, на предпросмотре жмёт
«🧪 Тест админам» — то же самое сообщение уходит админам в личку, экран
предпросмотра возвращается последним сообщением, и остаётся нажать
«Отправить» или «Отмена».

Главное, что проверяем: тест уходит тем же доставщиком, что и настоящая
рассылка (иначе «тест» показывал бы не то, что получат люди), не пишет
историю, не трогает выбранную аудиторию и выживает при заблокировавшем бота
админе.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings
from app.handlers.admin import messages as admin_messages


CHAT_ID = 500
ACTING_ADMIN = 777


def _unwrap(func):
    """Хендлер без декораторов: admin_required ждёт настоящий CallbackQuery."""
    while hasattr(func, '__wrapped__'):
        func = func.__wrapped__
    return func


def _callback():
    cb = MagicMock()
    cb.message = MagicMock()
    cb.message.chat = SimpleNamespace(id=CHAT_ID)
    cb.message.edit_text = AsyncMock()
    cb.message.delete = AsyncMock()
    cb.answer = AsyncMock()
    cb.bot = MagicMock()
    cb.bot.send_message = AsyncMock()
    cb.bot.send_photo = AsyncMock()
    cb.bot.send_video = AsyncMock()
    cb.bot.send_document = AsyncMock()
    return cb


def _admin():
    return SimpleNamespace(id=1, telegram_id=ACTING_ADMIN, language='ru', full_name='Админ')


def _state(**data):
    payload = {'broadcast_target': 'all', 'broadcast_message': '<b>Привет</b>', 'selected_buttons': []}
    payload.update(data)
    state = MagicMock()
    state.get_data = AsyncMock(return_value=payload)
    state.update_data = AsyncMock()
    state.clear = AsyncMock()
    return state


@pytest.fixture(autouse=True)
def _no_db_counts(monkeypatch):
    """Предпросмотр считает получателей в БД — здесь двойник сессии."""
    monkeypatch.setattr(admin_messages, 'get_target_users_count', AsyncMock(return_value=42))
    monkeypatch.setattr(admin_messages, 'get_custom_users_count', AsyncMock(return_value=42))


def _callbacks(markup) -> list[str]:
    if markup is None:
        return []
    return [btn.callback_data for row in markup.inline_keyboard for btn in row if btn.callback_data]


# ─── экран предпросмотра ───


@pytest.mark.asyncio
async def test_preview_offers_the_admin_test_button():
    cb, state = _callback(), _state()

    await admin_messages._show_broadcast_preview(cb, _admin(), state, AsyncMock())

    callbacks = _callbacks(cb.message.edit_text.await_args.kwargs['reply_markup'])
    assert 'admin_broadcast_test' in callbacks
    # Кнопка добавлена рядом с отправкой и отменой, а не вместо них.
    assert 'admin_confirm_broadcast' in callbacks
    assert 'admin_messages' in callbacks


@pytest.mark.asyncio
async def test_preview_comes_back_as_a_new_message_after_the_test():
    """Тестовые сообщения ложатся поверх экрана — он должен стать последним."""
    cb, state = _callback(), _state()

    await admin_messages._show_broadcast_preview(cb, _admin(), state, AsyncMock(), force_new=True)

    cb.message.delete.assert_awaited_once()
    cb.message.edit_text.assert_not_awaited()
    assert 'Предварительный просмотр' in cb.bot.send_message.await_args.kwargs['text']


# ─── сама тестовая отправка ───


@pytest.mark.asyncio
async def test_test_send_reaches_every_admin_plus_the_initiator(monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_IDS', '11,22,11', raising=False)
    cb, state = _callback(), _state()

    await _unwrap(admin_messages.send_broadcast_test)(cb, _admin(), state, AsyncMock())

    # Дубль в ADMIN_IDS не удваивает отправку, инициатор добавлен.
    recipients = [call.kwargs['chat_id'] for call in cb.bot.send_message.await_args_list]
    assert recipients[:3] == [11, 22, ACTING_ADMIN]
    assert cb.bot.send_message.await_args_list[0].kwargs['text'] == '<b>Привет</b>'


@pytest.mark.asyncio
async def test_test_send_uses_the_real_broadcast_keyboard(monkeypatch):
    """Админ должен увидеть ровно то, что получат люди, — без админских кнопок."""
    monkeypatch.setattr(settings, 'ADMIN_IDS', '11', raising=False)
    cb, state = _callback(), _state(selected_buttons=['balance'])

    await _unwrap(admin_messages.send_broadcast_test)(cb, _admin(), state, AsyncMock())

    first = cb.bot.send_message.await_args_list[0]
    callbacks = _callbacks(first.kwargs['reply_markup'])
    assert 'admin_confirm_broadcast' not in callbacks
    assert 'admin_broadcast_test' not in callbacks


@pytest.mark.asyncio
async def test_test_send_keeps_the_audience_and_writes_no_history(monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_IDS', '11', raising=False)
    cb, state = _callback(), _state()
    db = AsyncMock()
    db.add = MagicMock()

    await _unwrap(admin_messages.send_broadcast_test)(cb, _admin(), state, db)

    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    # Выбор получателей и текст остаются в FSM — отправлять по-настоящему ещё можно.
    state.clear.assert_not_awaited()
    state.update_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_test_send_survives_an_admin_who_blocked_the_bot(monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_IDS', '11,22', raising=False)
    cb, state = _callback(), _state()

    async def _send(chat_id, **kwargs):
        if chat_id == 11:
            raise RuntimeError('bot was blocked by the user')
        return SimpleNamespace()

    cb.bot.send_message = AsyncMock(side_effect=_send)

    await _unwrap(admin_messages.send_broadcast_test)(cb, _admin(), state, AsyncMock())

    # Экран предпросмотра всё равно вернулся, а итог честно назван.
    cb.message.delete.assert_awaited_once()
    assert 'не дошло' in cb.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_test_send_reports_when_nobody_got_it(monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_IDS', '11', raising=False)
    cb, state = _callback(), _state()

    async def _send(chat_id, **kwargs):
        # Экран предпросмотра (в чат админа) должен уйти — падают только адресаты теста.
        if chat_id != CHAT_ID:
            raise RuntimeError('chat not found')
        return SimpleNamespace()

    cb.bot.send_message = AsyncMock(side_effect=_send)

    await _unwrap(admin_messages.send_broadcast_test)(cb, _admin(), state, AsyncMock())

    assert 'не дошёл' in cb.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_test_send_refuses_without_a_message(monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_IDS', '11', raising=False)
    cb, state = _callback(), _state(broadcast_message=None)

    await _unwrap(admin_messages.send_broadcast_test)(cb, _admin(), state, AsyncMock())

    cb.bot.send_message.assert_not_awaited()
    assert cb.answer.await_args.kwargs.get('show_alert') is True


# ─── общий доставщик ───


@pytest.mark.asyncio
async def test_deliverer_sends_a_photo_with_the_text_as_caption():
    bot = MagicMock()
    bot.send_photo = AsyncMock()
    bot.send_message = AsyncMock()

    await admin_messages.deliver_broadcast_message(
        bot, 42, '<b>Привет</b>', has_media=True, media_type='photo', media_file_id='file-1', reply_markup=None
    )

    assert bot.send_photo.await_args.kwargs['caption'] == '<b>Привет</b>'
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliverer_splits_a_caption_over_the_limit():
    bot = MagicMock()
    bot.send_photo = AsyncMock()
    bot.send_message = AsyncMock()

    await admin_messages.deliver_broadcast_message(
        bot, 42, 'я' * 1100, has_media=True, media_type='photo', media_file_id='file-1', reply_markup=None
    )

    assert 'caption' not in bot.send_photo.await_args.kwargs
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_deliverer_falls_back_to_text_for_an_unknown_media_type():
    bot = MagicMock()
    bot.send_photo = AsyncMock()
    bot.send_message = AsyncMock()

    await admin_messages.deliver_broadcast_message(
        bot, 42, 'текст', has_media=True, media_type='sticker', media_file_id='file-1', reply_markup=None
    )

    bot.send_photo.assert_not_awaited()
    bot.send_message.assert_awaited_once()


def test_broadcast_and_test_share_one_deliverer():
    """Разъехавшись, «тест» перестал бы показывать то, что получат люди."""
    source = Path(admin_messages.__file__).read_text(encoding='utf-8')

    assert source.count('deliver_broadcast_message(') >= 3, 'определение + рассылка + тест'
    # Прежняя развилка «медиа или текст» внутри send_single_broadcast ушла в доставщик.
    assert re.search(r"'photo': callback\.bot\.send_photo", source) is None
