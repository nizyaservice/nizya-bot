"""Экран «Устройства»: тап по устройству открывает карточку, удаление спрашивает.

До этого тап по кнопке устройства сбрасывал его молча и без подтверждения, а
имя в кнопке резалось на 22 символах, потому что кнопка делила ряд с
карандашом переименования. Теперь:

* кнопка устройства занимает весь ряд и несёт имя целиком (Telegram сам
  переносит длинный лейбл на несколько строк);
* тап открывает карточку устройства с «Переименовать» / «Удалить» / «Назад»;
* «Удалить» ведёт на подтверждение «Точно удалить устройство X?», и только
  его кнопка «Удалить» реально дёргает панель;
* «Сбросить все устройства» тоже спрашивает — «Сбросить все» / «Отмена»;
* «назад»-кнопки ведут на предыдущий экран: карточка → страница списка,
  подтверждение → карточка, отмена переименования → карточка, отмена сброса
  всех → та же страница списка.
"""

import inspect
from contextlib import ExitStack, asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.handlers.subscription.devices as devices_mod
from app.keyboards.inline import (
    get_device_card_keyboard,
    get_device_delete_confirm_keyboard,
    get_devices_management_keyboard,
    get_devices_reset_all_confirm_keyboard,
)
from app.utils.device_labels import device_display_name, shorten_device_label


PANEL_USER_ID = 4242

LONG_NAME = 'iPhone 15 Pro Max рабочий телефон Марины'  # 40 символов — больше старого лимита 22


def _pagination(page: int = 1, total_pages: int = 1):
    return SimpleNamespace(page=page, total_pages=total_pages, has_prev=False, has_next=False)


def _callbacks(keyboard) -> list[str]:
    return [button.callback_data for row in keyboard.inline_keyboard for button in row]


def _labels(keyboard) -> list[str]:
    return [button.text for row in keyboard.inline_keyboard for button in row]


# ─── клавиатуры ───


def test_device_button_takes_whole_row_and_opens_card():
    devices = [{'hwid': 'A', 'platform': 'iOS', 'deviceModel': 'iPhone'}]

    keyboard = get_devices_management_keyboard(devices, _pagination(), 'ru')

    device_row = keyboard.inline_keyboard[0]
    assert len(device_row) == 1, 'кнопка устройства должна занимать весь ряд'
    assert device_row[0].callback_data == 'device_card_0_1'
    # Тап по устройству больше не удаляет его напрямую.
    assert not any(cb.startswith('reset_device_') for cb in _callbacks(keyboard))


def test_device_button_keeps_long_name_whole():
    devices = [{'hwid': 'A', 'local_name': LONG_NAME}]

    keyboard = get_devices_management_keyboard(devices, _pagination(), 'ru')

    assert LONG_NAME in keyboard.inline_keyboard[0][0].text
    assert '...' not in keyboard.inline_keyboard[0][0].text


def test_card_keyboard_offers_rename_delete_and_back_to_the_same_page():
    keyboard = get_device_card_keyboard(2, 3, 'ru')

    assert _callbacks(keyboard) == ['device_rename_2_3', 'device_remove_2_3', 'devices_page_3']


def test_delete_confirm_keyboard_removes_only_on_confirm():
    keyboard = get_device_delete_confirm_keyboard(2, 3, 'ru')

    # «Удалить» — исторический callback удаления; «Отмена» — назад в карточку.
    assert _callbacks(keyboard) == ['reset_device_2_3', 'device_card_2_3']
    labels = _labels(keyboard)
    assert '🗑' in labels[0] and '✖️' in labels[1]


# ─── имена устройств ───


def test_local_name_wins_over_platform_string():
    assert device_display_name({'local_name': 'Телефон', 'platform': 'iOS', 'deviceModel': 'iPhone'}) == 'Телефон'
    assert device_display_name({'platform': 'iOS', 'deviceModel': 'iPhone 15'}) == 'iOS - iPhone 15'
    assert device_display_name({}) == 'Unknown - Unknown'


def test_shorten_only_past_the_limit():
    assert shorten_device_label('короткое', limit=20) == 'короткое'
    assert shorten_device_label('a' * 30, limit=20).endswith('…')
    assert len(shorten_device_label('a' * 30, limit=20)) == 20


# ─── хендлеры ───


def _make_callback(data: str):
    cb = MagicMock()
    cb.data = data
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()
    return cb


def _make_user():
    user = MagicMock()
    user.id = 1
    user.language = 'ru'
    user.remnawave_id = PANEL_USER_ID
    return user


def _service_returning(devices):
    api = AsyncMock()
    api.get_user_devices = AsyncMock(return_value={'total': len(devices), 'devices': list(devices)})
    api.remove_device = AsyncMock(return_value=True)

    @asynccontextmanager
    async def _cm():
        yield api

    svc = MagicMock()
    svc.get_api_client = MagicMock(side_effect=lambda: _cm())
    svc.api_double = api
    return svc


def _screen_patches(service):
    """Общие заглушки: панель, алиасы, баннер и отправка экрана."""
    return [
        patch.object(devices_mod, 'RemnaWaveService', return_value=service),
        patch.object(
            devices_mod,
            '_resolve_subscription',
            new=AsyncMock(return_value=(SimpleNamespace(id=7, remnawave_id=PANEL_USER_ID), 7)),
        ),
        patch.object(devices_mod, '_enrich_devices_with_aliases', new=AsyncMock(side_effect=lambda lst, uid: lst)),
        patch.object(devices_mod, 'get_section_media_kwargs', new=AsyncMock(return_value={})),
        patch.object(devices_mod, 'edit_or_answer_photo', new=AsyncMock()),
    ]


@pytest.mark.anyio('asyncio')
async def test_card_screen_shows_device_details():
    device = {
        'hwid': 'AA',
        'local_name': LONG_NAME,
        'platform': 'iOS',
        'deviceModel': 'iPhone 15 Pro Max',
        'osVersion': '18.1',
    }
    service = _service_returning([device])
    cb, user, db = _make_callback('device_card_0_1'), _make_user(), MagicMock()

    with ExitStack() as stack:
        for p in _screen_patches(service):
            stack.enter_context(p)
        send = devices_mod.edit_or_answer_photo
        await devices_mod.handle_device_card(cb, user, db, None)

    send.assert_awaited_once()
    caption = send.await_args.kwargs['caption']
    assert LONG_NAME in caption
    assert 'iPhone 15 Pro Max' in caption and '18.1' in caption
    assert _callbacks(send.await_args.kwargs['keyboard']) == [
        'device_rename_0_1',
        'device_remove_0_1',
        'devices_page_1',
    ]
    service.api_double.remove_device.assert_not_awaited()


@pytest.mark.anyio('asyncio')
async def test_delete_request_asks_before_touching_the_panel():
    device = {'hwid': 'AA', 'local_name': 'Телефон Марины', 'platform': 'iOS', 'deviceModel': 'iPhone'}
    service = _service_returning([device])
    cb, user, db = _make_callback('device_remove_0_1'), _make_user(), MagicMock()

    with ExitStack() as stack:
        for p in _screen_patches(service):
            stack.enter_context(p)
        send = devices_mod.edit_or_answer_photo
        await devices_mod.handle_device_delete_request(cb, user, db, None)

    send.assert_awaited_once()
    caption = send.await_args.kwargs['caption']
    assert 'Телефон Марины' in caption
    assert 'удалить' in caption.lower()
    # Подтверждение ничего не удаляет — только спрашивает.
    service.api_double.remove_device.assert_not_awaited()
    assert _callbacks(send.await_args.kwargs['keyboard']) == ['reset_device_0_1', 'device_card_0_1']


@pytest.mark.anyio('asyncio')
async def test_missing_device_index_reports_instead_of_rendering():
    service = _service_returning([])
    cb, user, db = _make_callback('device_card_4_1'), _make_user(), MagicMock()

    with ExitStack() as stack:
        for p in _screen_patches(service):
            stack.enter_context(p)
        send = devices_mod.edit_or_answer_photo
        await devices_mod.handle_device_card(cb, user, db, None)

    send.assert_not_awaited()
    assert cb.answer.await_args.kwargs.get('show_alert') is True


@pytest.mark.anyio('asyncio')
async def test_confirmed_delete_removes_and_names_the_local_alias():
    device = {'hwid': 'AA', 'platform': 'iOS', 'deviceModel': 'iPhone'}
    service = _service_returning([device])
    cb, user, db = _make_callback('reset_device_0_1'), _make_user(), MagicMock()

    def _attach(devices, _user_id):
        for item in devices:
            item['local_name'] = 'Телефон Марины'
        return devices

    with ExitStack() as stack:
        # handle_single_device_reset делает локальный импорт сервиса.
        stack.enter_context(patch('app.services.remnawave_service.RemnaWaveService', return_value=service))
        stack.enter_context(
            patch.object(
                devices_mod,
                '_resolve_subscription',
                new=AsyncMock(return_value=(SimpleNamespace(id=7, remnawave_id=PANEL_USER_ID), 7)),
            )
        )
        stack.enter_context(
            patch.object(devices_mod, '_enrich_devices_with_aliases', new=AsyncMock(side_effect=_attach))
        )
        stack.enter_context(patch.object(devices_mod, 'show_devices_page', new=AsyncMock()))
        await devices_mod.handle_single_device_reset(cb, user, db, None)

    service.api_double.remove_device.assert_awaited_once_with(PANEL_USER_ID, 'AA')
    alert = cb.answer.await_args.args[0]
    assert 'Телефон Марины' in alert, 'в алерте должно стоять имя, которое человек видел в карточке'


@pytest.mark.anyio('asyncio')
async def test_rename_cancel_returns_to_the_card_it_came_from():
    device = {'hwid': 'AA', 'platform': 'iOS', 'deviceModel': 'iPhone'}
    service = _service_returning([device])
    cb, user = _make_callback('device_rename_cancel'), _make_user()

    db = MagicMock()
    res = MagicMock()
    res.scalar_one_or_none = MagicMock(return_value=SimpleNamespace(id=7, remnawave_id=PANEL_USER_ID))
    db.execute = AsyncMock(return_value=res)

    state = MagicMock()
    state.get_data = AsyncMock(return_value={'rename_page': 1, 'rename_sub_id': 7, 'rename_index': 0})
    state.clear = AsyncMock()

    with (
        patch.object(devices_mod, 'RemnaWaveService', return_value=service),
        patch.object(devices_mod, '_enrich_devices_with_aliases', new=AsyncMock(side_effect=lambda lst, uid: lst)),
        patch.object(devices_mod, 'show_device_card', new=AsyncMock()) as card,
        patch.object(devices_mod, 'show_devices_page', new=AsyncMock()) as listing,
    ):
        await devices_mod.cancel_device_rename(cb, user, db, state)

    card.assert_awaited_once()
    listing.assert_not_awaited()
    assert card.await_args.args[4] == 0 and card.await_args.args[5] == 1


def test_rename_prompt_remembers_the_card_index():
    """start_device_rename обязан сохранить индекс — иначе «Отмена» не найдёт карточку."""
    source = inspect.getsource(devices_mod.start_device_rename)
    assert 'rename_index=device_index' in source


# ─── сброс всех устройств ───


def test_reset_all_button_asks_before_resetting():
    devices = [{'hwid': 'A'}, {'hwid': 'B'}]

    keyboard = get_devices_management_keyboard(devices, _pagination(page=2, total_pages=3), 'ru')

    callbacks = _callbacks(keyboard)
    assert 'reset_all_ask_2' in callbacks
    # Сам сброс из списка больше не достигается.
    assert 'reset_all_devices' not in callbacks


def test_reset_all_confirm_keyboard_cancels_back_to_the_same_page():
    keyboard = get_devices_reset_all_confirm_keyboard(3, 'ru')

    assert _callbacks(keyboard) == ['reset_all_devices', 'devices_page_3']


@pytest.mark.anyio('asyncio')
async def test_reset_all_request_shows_the_count_and_waits():
    service = _service_returning([{'hwid': 'A'}, {'hwid': 'B'}, {'hwid': 'C'}])
    cb, user, db = _make_callback('reset_all_ask_2'), _make_user(), MagicMock()

    with ExitStack() as stack:
        for p in _screen_patches(service):
            stack.enter_context(p)
        send = devices_mod.edit_or_answer_photo
        await devices_mod.handle_all_devices_reset_request(cb, user, db, None)

    send.assert_awaited_once()
    caption = send.await_args.kwargs['caption']
    assert '3' in caption
    assert _callbacks(send.await_args.kwargs['keyboard']) == ['reset_all_devices', 'devices_page_2']
    # Вопрос ничего не сбрасывает.
    service.api_double.remove_device.assert_not_awaited()


@pytest.mark.anyio('asyncio')
async def test_reset_all_request_without_devices_reports_instead_of_asking():
    service = _service_returning([])
    cb, user, db = _make_callback('reset_all_ask_1'), _make_user(), MagicMock()

    with ExitStack() as stack:
        for p in _screen_patches(service):
            stack.enter_context(p)
        send = devices_mod.edit_or_answer_photo
        await devices_mod.handle_all_devices_reset_request(cb, user, db, None)

    send.assert_not_awaited()
    assert cb.answer.await_args.kwargs.get('show_alert') is True
    service.api_double.remove_device.assert_not_awaited()
