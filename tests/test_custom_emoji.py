"""Подмена эмодзи кастомными: текст сообщения и подписи кнопок."""

import json

import pytest
from aiogram.methods import SendMessage, SendRichMessage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputRichMessage

from app.middlewares.custom_emoji import CustomEmojiMiddleware
from app.utils import custom_emoji


@pytest.fixture(autouse=True)
def _map(monkeypatch):
    """Фиксированная карта вместо файла проекта."""
    monkeypatch.setattr(
        custom_emoji,
        'get_custom_emoji_map',
        lambda: {'✅': '111', '⚠️': '222', '⚠': '222', '💰': '333'},
    )
    custom_emoji._emoji_pattern.cache_clear()
    yield
    custom_emoji._emoji_pattern.cache_clear()


def test_known_emoji_wrapped_in_tag():
    assert custom_emoji.apply_to_html('Готово ✅') == 'Готово <tg-emoji emoji-id="111">✅</tg-emoji>'


def test_unknown_emoji_untouched():
    assert custom_emoji.apply_to_html('Пока 🐢') == 'Пока 🐢'


def test_variation_selector_variants_both_match():
    assert '222' in custom_emoji.apply_to_html('⚠️ внимание')
    assert '222' in custom_emoji.apply_to_html('⚠ внимание')


def test_tag_attributes_are_not_touched():
    source = '<a href="https://example.com/?q=✅">Баланс 💰</a>'
    result = custom_emoji.apply_to_html(source)
    assert 'href="https://example.com/?q=✅"' in result
    assert '<tg-emoji emoji-id="333">💰</tg-emoji>' in result


def test_applying_twice_changes_nothing():
    once = custom_emoji.apply_to_html('Оплата ✅ прошла')
    assert custom_emoji.apply_to_html(once) == once


def test_button_leading_emoji_moves_to_icon():
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text='💰 Баланс', callback_data='menu_balance')]]
    )
    button = custom_emoji.apply_to_markup(markup).inline_keyboard[0][0]
    assert button.text == 'Баланс'
    assert button.icon_custom_emoji_id == '333'
    assert button.callback_data == 'menu_balance'


def test_button_without_known_emoji_untouched():
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🐢 Медленно', callback_data='x')]])
    assert custom_emoji.apply_to_markup(markup) is markup


def test_button_with_own_icon_untouched():
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text='💰 Баланс', callback_data='x', icon_custom_emoji_id='999')]]
    )
    button = custom_emoji.apply_to_markup(markup).inline_keyboard[0][0]
    assert button.icon_custom_emoji_id == '999'
    assert button.text == '💰 Баланс'


def test_emoji_only_button_keeps_its_label():
    """Иконка вместо текста оставила бы кнопку без подписи — не трогаем."""
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='✅', callback_data='x')]])
    assert custom_emoji.apply_to_markup(markup) is markup


def test_real_project_map_is_valid():
    """Карта в корне проекта на месте, из неё читаются числовые id."""
    raw = json.loads(custom_emoji.CUSTOM_EMOJI_MAP_PATH.read_text(encoding='utf-8'))
    assert raw, 'custom_emoji_ids.json пуст'
    assert all(isinstance(value, str) and value.isdigit() for value in raw.values())
    assert '❌' in raw


class _StubDefault:
    parse_mode = 'HTML'


class _StubBot:
    """Боту от middleware нужен только parse_mode по умолчанию."""

    default = _StubDefault()


def test_rich_message_html_is_processed():
    """Главное меню уходит через sendRichMessage: текст лежит в rich_message.html."""
    method = SendRichMessage(
        chat_id=1,
        rich_message=InputRichMessage(html='📱 <b>Подписка:</b> активна ✅', skip_entity_detection=True),
    )
    CustomEmojiMiddleware._decorate(_StubBot(), method)
    assert method.rich_message.html == '📱 <b>Подписка:</b> активна <tg-emoji emoji-id="111">✅</tg-emoji>'
    # Остальные поля rich-сообщения переживают подмену.
    assert method.rich_message.skip_entity_detection is True


def test_plain_message_text_is_processed():
    method = SendMessage(chat_id=1, text='Оплата ✅')
    CustomEmojiMiddleware._decorate(_StubBot(), method)
    assert method.text == 'Оплата <tg-emoji emoji-id="111">✅</tg-emoji>'


def test_edit_message_media_caption_is_processed():
    """Типы aiogram заморожены: подпись меняется копией, иначе ValidationError."""
    from aiogram.methods import EditMessageMedia
    from aiogram.types import InputMediaPhoto

    method = EditMessageMedia(
        chat_id=1,
        message_id=2,
        media=InputMediaPhoto(media='file_id', caption='Панель управления ✅'),
    )
    CustomEmojiMiddleware._decorate(_StubBot(), method)
    assert method.media.caption == 'Панель управления <tg-emoji emoji-id="111">✅</tg-emoji>'
    assert method.media.media == 'file_id'


@pytest.mark.asyncio
async def test_decoration_failure_does_not_break_request(monkeypatch):
    """Сбой подстановки не должен превращаться в «Произошла ошибка» у пользователя."""
    from aiogram.methods import SendMessage

    def boom(*_args, **_kwargs):
        raise RuntimeError('сломалось')

    monkeypatch.setattr(CustomEmojiMiddleware, '_decorate', staticmethod(boom))

    sent = []

    async def make_request(bot, method):
        sent.append(method)
        return 'ok'

    method = SendMessage(chat_id=1, text='Оплата ✅')
    assert await CustomEmojiMiddleware()(make_request, _StubBot(), method) == 'ok'
    assert sent == [method]
