"""Шаг ввода текста рассылки: подпись к фото и форматирование автора.

Админ прислал текст с эмодзи и monospace одним сообщением с картинкой — шаг
упал на ``len(None)``: у сообщения с фото ``text`` пустой, текст живёт в
``caption``. Второе: форматирование приезжает entities, а не тегами, поэтому
даже без падения моноширинный текст терялся бы.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, MessageEntity, PhotoSize

from app.handlers.admin import messages as admin_messages


CHAT_ID = 8923755672
PHOTO = PhotoSize(file_id='photo-file-id', file_unique_id='uniq', width=100, height=100)


def _handler():
    """Хендлер без декораторов: admin_required ждёт настоящий types.Message."""
    func = admin_messages.process_broadcast_message
    while hasattr(func, '__wrapped__'):
        func = func.__wrapped__
    return func


def _message(*, text=None, caption=None, entities=None, photo=None) -> SimpleNamespace:
    """Двойник сообщения, у которого html_text посчитан настоящим aiogram."""
    real = Message(
        chat=Chat(id=CHAT_ID, type='private'),
        message_id=1,
        date=datetime.now(UTC),
        text=text,
        caption=caption,
        entities=entities if text else None,
        caption_entities=entities if caption else None,
        photo=photo,
    )
    html_text = None
    try:
        html_text = real.html_text
    except TypeError:
        pass
    return SimpleNamespace(
        html_text=html_text,
        text=text,
        caption=caption,
        photo=photo,
        video=None,
        document=None,
        answer=AsyncMock(),
        chat=real.chat,
    )


def _state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=CHAT_ID, user_id=CHAT_ID))


def _admin():
    return SimpleNamespace(id=1, telegram_id=CHAT_ID, language='ru')


@pytest.mark.asyncio
async def test_photo_with_caption_keeps_text_and_picks_up_the_photo(monkeypatch):
    preview = AsyncMock()
    monkeypatch.setattr(admin_messages, 'show_media_preview', preview)
    message = _message(
        caption='Привет 👋 код',
        # Смещения Telegram считаются в кодовых единицах UTF-16: эмодзи занимает две.
        entities=[MessageEntity(type='code', offset=10, length=3)],
        photo=[PHOTO],
    )
    state = _state()

    await _handler()(message, _admin(), state, None)

    data = await state.get_data()
    assert '<code>код</code>' in data['broadcast_message'], 'моноширинный текст обязан дойти до получателей'
    assert '👋' in data['broadcast_message']
    assert data['has_media'] is True
    assert data['media_type'] == 'photo'
    assert data['media_file_id'] == PHOTO.file_id
    # Подпись отдельно не храним: broadcast_service возьмёт текст рассылки.
    assert data['media_caption'] is None
    preview.assert_awaited_once()


@pytest.mark.asyncio
async def test_plain_text_keeps_formatting_and_asks_for_media():
    message = _message(text='Важно', entities=[MessageEntity(type='bold', offset=0, length=5)])
    state = _state()

    await _handler()(message, _admin(), state, None)

    data = await state.get_data()
    assert data['broadcast_message'] == '<b>Важно</b>'
    assert 'has_media' not in data
    assert 'Добавление медиафайла' in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_photo_without_caption_asks_for_text():
    message = _message(photo=[PHOTO])
    state = _state()

    await _handler()(message, _admin(), state, None)

    assert 'Нужен текст рассылки' in message.answer.await_args.args[0]
    assert await state.get_data() == {}


@pytest.mark.asyncio
async def test_too_long_caption_is_refused_before_sending(monkeypatch):
    """С картинкой Telegram ограничивает подпись 1024 символами."""
    preview = AsyncMock()
    monkeypatch.setattr(admin_messages, 'show_media_preview', preview)
    message = _message(caption='я' * 1100, photo=[PHOTO])
    state = _state()

    await _handler()(message, _admin(), state, None)

    assert '1024' in message.answer.await_args.args[0]
    data = await state.get_data()
    assert 'has_media' not in data
    preview.assert_not_awaited()


# --- одна линейка для подписи на входе, в предпросмотре и в отправке ---
# Рассылка с картинкой приходила двумя сообщениями: фото без подписи, а текст
# отдельно. Отправка мерила сырой len() вместе с HTML-тегами и видела перебор
# там, где Telegram считает только разобранный текст.


def test_caption_rule_ignores_markup():
    # 1020 разобранных символов влезают в подпись, а с тегами сырая длина уже за лимитом.
    plain = 'я' * 1020
    with_markup = f'{plain[:500]}<code>{plain[500:]}</code>'

    assert len(with_markup) > 1024, 'разметка обязана завышать сырую длину — иначе тест ничего не проверяет'
    assert admin_messages._caption_fits(with_markup) is True


def test_caption_rule_refuses_long_text():
    assert admin_messages._caption_fits('я' * 1100) is False


def test_caption_rule_counts_emoji_as_two_units():
    """Telegram считает длину в кодовых единицах UTF-16."""
    assert admin_messages._caption_fits('👋' * 512) is True
    assert admin_messages._caption_fits('👋' * 600) is False


def test_module_has_no_raw_caption_length_checks():
    """Сырой len() против 1024 — та самая причина двух сообщений."""
    source = Path(admin_messages.__file__).read_text(encoding='utf-8')

    assert re.search(r'len\([^)]*\)\s*<=\s*1024', source) is None
    # вход текста, предпросмотр у админа и отправка получателям
    assert source.count('_caption_fits(') >= 4
