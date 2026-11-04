"""Кнопка в сообщении рассылки открывает экран НОВЫМ сообщением.

До этого нажатие «🎫 Промокод» в рассылке перезаписывало саму рассылку
экраном промокода (а у рассылки с картинкой — удаляло её и присылало экран
заново). Теперь рассылка остаётся в чате, а экран приезжает отдельным
сообщением.

Механика: кнопки рассылки несут метку ``bc:`` в ``callback_data``, outer-мидлвара
снимает её до фильтров и на время обработки апдейта включает ContextVar. Читают
флаг две точки отрисовки — ``edit_or_answer_photo`` и патченный
``Message.edit_text``. Хендлеры разделов не знают об этом ничего, поэтому
остальные флоу обязаны остаться с правкой текущего сообщения — это тут и
проверяется в паре «с флагом / без флага».
"""

from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import CallbackQuery, Chat, InlineKeyboardButton, InlineKeyboardMarkup, User as TgUser

from app.handlers.admin.messages import create_broadcast_keyboard
from app.middlewares.broadcast_buttons import BroadcastButtonMiddleware
from app.utils import message_patch as mp, photo_message as pm, rich_menu as rich
from app.utils.screen_delivery import (
    CALLBACK_DATA_LIMIT,
    force_new_screen,
    force_new_screen_enabled,
    unwrap_broadcast_callback,
    wrap_broadcast_callback,
)


CHAT_ID = 777


# ─── метка в callback_data ───


def test_wrap_and_unwrap_round_trip():
    assert wrap_broadcast_callback('menu_promocode') == 'bc:menu_promocode'
    assert unwrap_broadcast_callback('bc:menu_promocode') == 'menu_promocode'
    # Обычный callback метки не несёт — его мидлвара не трогает.
    assert unwrap_broadcast_callback('menu_promocode') is None


def test_wrap_does_not_stack_the_prefix():
    assert wrap_broadcast_callback('bc:menu_promocode') == 'bc:menu_promocode'


def test_wrap_keeps_a_callback_that_would_not_fit():
    """Лимит Telegram — 64 байта, и кнопка важнее способа доставки экрана."""
    fits = 'x' * (CALLBACK_DATA_LIMIT - len('bc:'))
    assert wrap_broadcast_callback(fits) == f'bc:{fits}'

    over = 'x' * (CALLBACK_DATA_LIMIT - len('bc:') + 1)
    assert wrap_broadcast_callback(over) == over

    # Лимит считается в байтах, а не в символах.
    cyrillic = 'я' * 31
    assert len(cyrillic.encode()) == 62
    assert wrap_broadcast_callback(cyrillic) == cyrillic


def test_flag_is_off_outside_the_context():
    assert force_new_screen_enabled() is False
    with force_new_screen():
        assert force_new_screen_enabled() is True
    assert force_new_screen_enabled() is False


# ─── клавиатура рассылки ───


def test_broadcast_keyboard_marks_its_buttons():
    # «home» берём намеренно: остальные ключи в режиме кабинета превращаются в
    # miniapp-кнопку без callback_data, и кейс проверял бы погоду на Марсе.
    keyboard = create_broadcast_keyboard(['home'], 'ru')

    callbacks = [btn.callback_data for row in keyboard.inline_keyboard for btn in row if btn.callback_data]
    assert callbacks == ['bc:back_to_menu'], callbacks


def test_broadcast_keyboard_marks_custom_callback_buttons_and_leaves_urls():
    keyboard = create_broadcast_keyboard(
        [],
        'ru',
        custom_buttons=[
            {'label': 'Куда-то', 'action_type': 'callback', 'action_value': 'menu_support'},
            {'label': 'Наружу', 'action_type': 'url', 'action_value': 'https://example.com'},
        ],
    )

    flat = [btn for row in keyboard.inline_keyboard for btn in row]
    assert [btn.callback_data for btn in flat if btn.callback_data] == ['bc:menu_support']
    assert [btn.url for btn in flat if btn.url] == ['https://example.com']


# ─── мидлвара ───


def _callback(data: str) -> CallbackQuery:
    return CallbackQuery(
        id='1',
        from_user=TgUser(id=CHAT_ID, is_bot=False, first_name='Кто-то'),
        chat_instance='ci',
        data=data,
    )


@pytest.mark.asyncio
async def test_middleware_strips_the_mark_and_turns_the_flag_on():
    seen = {}

    async def handler(event, data):
        seen['data'] = event.data
        seen['flag'] = force_new_screen_enabled()

    await BroadcastButtonMiddleware()(handler, _callback('bc:menu_promocode'), {'bot': None})

    # Хендлер видит обычный callback — ни одного нового маршрута регистрировать не нужно.
    assert seen['data'] == 'menu_promocode'
    assert seen['flag'] is True
    # Флаг живёт только внутри обработки апдейта.
    assert force_new_screen_enabled() is False


@pytest.mark.asyncio
async def test_middleware_leaves_ordinary_callbacks_alone():
    """Все прочие флоу бота обязаны остаться с правкой текущего сообщения."""
    seen = {}
    event = _callback('menu_promocode')

    async def handler(received, data):
        seen['same_object'] = received is event
        seen['flag'] = force_new_screen_enabled()

    await BroadcastButtonMiddleware()(handler, event, {'bot': None})

    assert seen['same_object'] is True
    assert seen['flag'] is False


@pytest.mark.asyncio
async def test_middleware_passes_through_other_event_types():
    seen = {}

    async def handler(event, data):
        seen['flag'] = force_new_screen_enabled()

    await BroadcastButtonMiddleware()(handler, SimpleNamespace(data='bc:menu_promocode'), {})

    assert seen['flag'] is False, 'не callback — трогать нечего'


# ─── отрисовка экрана ───


def _screen_callback(*, with_photo: bool):
    cb = MagicMock()
    cb.from_user = SimpleNamespace(id=CHAT_ID)
    cb.message = MagicMock()
    cb.message.chat = Chat(id=CHAT_ID, type='private')
    cb.message.photo = [SimpleNamespace(file_id='old-photo')] if with_photo else None
    # Без этого is_qr_message() увидит MagicMock-подпись и сочтёт её QR-сообщением.
    cb.message.caption = None
    cb.message.edit_text = AsyncMock()
    cb.message.edit_media = AsyncMock()
    cb.message.delete = AsyncMock()
    cb.message.answer = AsyncMock()
    cb.bot = MagicMock()
    cb.bot.send_message = AsyncMock()
    cb.bot.send_photo = AsyncMock()
    cb.answer = AsyncMock()
    return cb


@pytest.mark.asyncio
async def test_screen_arrives_as_a_new_message_under_the_flag(monkeypatch):
    monkeypatch.setattr(pm.settings, 'ENABLE_LOGO_MODE', False)
    cb = _screen_callback(with_photo=True)

    with force_new_screen():
        await pm.edit_or_answer_photo(callback=cb, caption='Введите промокод', keyboard=None)

    cb.bot.send_message.assert_awaited_once()
    assert cb.bot.send_message.await_args.kwargs['chat_id'] == CHAT_ID
    assert cb.bot.send_message.await_args.kwargs['text'] == 'Введите промокод'
    # Сообщение рассылки осталось в чате: ни правки, ни удаления.
    cb.message.edit_text.assert_not_awaited()
    cb.message.edit_media.assert_not_awaited()
    cb.message.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_screen_keeps_editing_without_the_flag(monkeypatch):
    """Контрольный кейс: обычный флоу правит текущее сообщение, как раньше."""
    monkeypatch.setattr(pm.settings, 'ENABLE_LOGO_MODE', False)
    cb = _screen_callback(with_photo=False)

    await pm.edit_or_answer_photo(callback=cb, caption='Введите промокод', keyboard=None)

    cb.message.edit_text.assert_awaited_once()
    cb.bot.send_message.assert_not_awaited()
    cb.bot.send_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_banner_survives_the_new_message(monkeypatch):
    """Явный баннер раздела не должен потеряться в пользу автодетекта."""
    monkeypatch.setattr(pm.settings, 'ENABLE_LOGO_MODE', False)
    monkeypatch.setattr(pm, 'remember_file_photo', lambda *args, **kwargs: None)
    monkeypatch.setattr(pm, '_cache_logo_file_id', lambda *args, **kwargs: None)
    cb = _screen_callback(with_photo=True)

    with force_new_screen():
        await pm.edit_or_answer_photo(callback=cb, caption='Введите промокод', keyboard=None, media='promo-banner-id')

    assert cb.bot.send_photo.await_args.kwargs['photo'] == 'promo-banner-id'
    assert cb.bot.send_photo.await_args.kwargs['caption'] == 'Введите промокод'
    cb.message.edit_media.assert_not_awaited()


@pytest.mark.asyncio
async def test_patched_edit_text_answers_instead_of_editing_under_the_flag(monkeypatch):
    """Хендлеры, которые зовут message.edit_text напрямую, тоже должны слушаться."""
    answered = AsyncMock()
    monkeypatch.setattr(mp, '_answer_with_photo', answered)
    message = MagicMock()

    with force_new_screen():
        await mp._edit_with_photo(message, 'Экран')

    answered.assert_awaited_once()
    assert answered.await_args.args[1] == 'Экран'


# ─── rich-меню ───
#
# «🏠 На главную» идёт не через edit_or_answer_photo, а через
# try_edit_rich_main_menu: он правит сообщение rich-блоком, а сообщение с
# фото (рассылка с картинкой) пересоздаёт — то есть УДАЛЯЛ рассылку.


def _rich_patches(monkeypatch, send):
    monkeypatch.setattr(rich, 'is_rich_menu_enabled', lambda: True)
    monkeypatch.setattr(rich, 'build_main_menu_rich_html', AsyncMock(return_value='<p>меню</p>'))
    monkeypatch.setattr(rich, '_send_rich_menu', send)
    monkeypatch.setattr(rich, 'remember_live_menu', AsyncMock())


def _rich_callback(*, with_photo: bool):
    cb = MagicMock()
    cb.message = MagicMock()
    cb.message.chat = Chat(id=CHAT_ID, type='private')
    cb.message.message_id = 10
    cb.message.photo = [SimpleNamespace(file_id='broadcast-photo')] if with_photo else None
    cb.message.text = None if with_photo else 'Текст рассылки'
    cb.message.rich_message = None
    cb.message.delete = AsyncMock()
    cb.bot = AsyncMock()
    return cb


def _rich_user():
    return SimpleNamespace(id=1, language='ru')


def _rich_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text='🎫 Промокод', callback_data='menu_promocode')]]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('with_photo', [True, False])
async def test_rich_main_menu_leaves_the_broadcast_alone_under_the_flag(monkeypatch, with_photo):
    send = AsyncMock(return_value=SimpleNamespace(message_id=11))
    _rich_patches(monkeypatch, send)
    cb = _rich_callback(with_photo=with_photo)

    with force_new_screen():
        handled = await rich.try_edit_rich_main_menu(cb, _rich_user(), MagicMock(), AsyncMock(), _rich_keyboard())

    assert handled is True
    send.assert_awaited_once()
    # Ни удаления рассылки, ни правки её текста.
    cb.message.delete.assert_not_awaited()
    cb.bot.assert_not_awaited()


@pytest.mark.asyncio
async def test_rich_main_menu_still_edits_in_place_without_the_flag(monkeypatch):
    """Контрольный кейс: обычная навигация по меню правит текущее сообщение."""
    send = AsyncMock(return_value=SimpleNamespace(message_id=11))
    _rich_patches(monkeypatch, send)
    cb = _rich_callback(with_photo=False)

    handled = await rich.try_edit_rich_main_menu(cb, _rich_user(), MagicMock(), AsyncMock(), _rich_keyboard())

    assert handled is True
    cb.bot.assert_awaited_once()
    send.assert_not_awaited()
    cb.message.delete.assert_not_awaited()


# ─── инвариант для всех кнопок рассылки ───


BROADCAST_DESTINATIONS = (
    ('app/handlers/balance/main.py', 'show_payment_methods'),
    ('app/handlers/referral.py', 'show_referral_info'),
    ('app/handlers/promocode.py', 'show_promocode_menu'),
    ('app/handlers/subscription/links.py', 'handle_connect_subscription'),
    ('app/handlers/subscription/purchase.py', 'show_subscription_info'),
    ('app/handlers/support.py', 'show_support_info'),
    ('app/handlers/menu.py', 'handle_back_to_menu'),
)


@pytest.mark.parametrize(('path', 'name'), BROADCAST_DESTINATIONS)
def test_destination_handler_never_deletes_the_source_message(path, name):
    """Экраны, куда ведут кнопки рассылки, не вправе удалять сообщение.

    Отправку новым сообщением включает флаг в двух точках отрисовки, но
    собственный `message.delete()` в хендлере обошёл бы их и унёс рассылку.
    Сейчас такого ни в одном нет — кейс держит это свойство.
    """
    tree = ast.parse(pathlib.Path(path).read_text(encoding='utf-8'))
    handler = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == name)

    deletes = [
        ast.unparse(node.func)
        for node in ast.walk(handler)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'delete'
    ]
    assert not deletes, f'{name} удаляет сообщение: {deletes}'
