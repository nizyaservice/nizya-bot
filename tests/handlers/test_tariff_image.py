"""Своя картинка тарифа на пути покупки.

Все экраны воронки показывали один баннер «Покупка подписки», и тарифы в ней
выглядели одинаково. Теперь у тарифа может быть своя картинка; пусто —
прежний общий баннер.

Экраны воронки рисуются двумя разными способами: часть зовёт
get_section_media_kwargs напрямую, часть уходит в обычный message.edit_text,
который подбирает баннер автодетектом по клавиатуре и о тарифе не знает.
Поэтому картинка живёт в ContextVar, который читают обе ветки, — и проверять
надо обе, иначе половина воронки молча останется с общим баннером.
"""

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.handlers.admin.tariff_panel_settings as admin_mod
import app.services.section_image_service as svc
from app.states import AdminStates


FILE_ID = 'tariff-image-file-id'


@pytest.fixture(autouse=True)
def _clean_context():
    svc.reset_tariff_image_context()
    yield
    svc.reset_tariff_image_context()


def _tariff(**overrides):
    values = {'id': 7, 'name': 'Nizya Standard', 'image_file_id': FILE_ID}
    values.update(overrides)
    return SimpleNamespace(**values)


def _unwrap(fn):
    while hasattr(fn, '__wrapped__'):
        fn = fn.__wrapped__
    return fn


# ---- контекст и приоритет ----


async def test_tariff_image_wins_over_the_section_banner(monkeypatch) -> None:
    monkeypatch.setattr(svc, 'get_setting_value', AsyncMock(return_value='section-banner'))
    svc.set_tariff_image_context(_tariff())

    assert await svc.get_section_media_kwargs(MagicMock(), 'subscription_purchase') == {'media': FILE_ID}


async def test_section_banner_used_when_tariff_has_no_image(monkeypatch) -> None:
    monkeypatch.setattr(svc, 'get_setting_value', AsyncMock(return_value='section-banner'))
    svc.set_tariff_image_context(_tariff(image_file_id=None))

    assert await svc.get_section_media_kwargs(MagicMock(), 'subscription_purchase') == {'media': 'section-banner'}


async def test_autodetect_branch_also_sees_the_tariff_image(monkeypatch) -> None:
    """Вторая ветка отрисовки: экран выбранного тарифа идёт через edit_text."""
    monkeypatch.setattr(svc, 'resolve_cached_section_file_id', AsyncMock(return_value='section-banner'))
    svc.set_tariff_image_context(_tariff())

    assert await svc.resolve_section_media('subscription_purchase') == FILE_ID


async def test_context_is_empty_by_default(monkeypatch) -> None:
    """Без выбранного тарифа ничего не меняется — списки и прочие экраны целы."""
    monkeypatch.setattr(svc, 'get_setting_value', AsyncMock(return_value='section-banner'))

    assert svc.get_tariff_image_context() is None
    assert await svc.get_section_media_kwargs(MagicMock(), 'subscription_purchase') == {'media': 'section-banner'}


def test_context_does_not_leak_between_tasks() -> None:
    """ContextVar выбран ради изоляции апдейтов — проверяем, что она есть."""
    import asyncio

    async def sets_context():
        svc.set_tariff_image_context(_tariff())
        return svc.get_tariff_image_context()

    async def reads_context():
        return svc.get_tariff_image_context()

    async def run():
        assert await asyncio.create_task(sets_context()) == FILE_ID
        return await asyncio.create_task(reads_context())

    assert asyncio.run(run()) is None


# ---- воронка покупки ----


def test_every_tariff_load_sets_the_context() -> None:
    """Каждый хендлер, загрузивший выбранный тариф, обязан положить его в контекст.

    Пропуск одного места не ломает ничего заметно: экран просто покажет общий
    баннер, и заметить это можно только глазами. Поэтому проверяем механически.
    """
    source = Path(__file__).resolve().parents[2] / 'app' / 'handlers' / 'subscription' / 'tariff_purchase.py'
    lines = source.read_text(encoding='utf-8').split('\n')
    load = re.compile(r'    tariff = await get_tariff_by_id\(db, (?:tariff_id|subscription\.tariff_id)\)$')

    missing = [
        i + 1
        for i, line in enumerate(lines)
        if load.match(line) and 'set_tariff_image_context(tariff)' not in lines[i + 1]
    ]

    assert not missing, f'контекст не выставлен после загрузки тарифа в строках: {missing}'


# ---- админка ----


async def test_admin_saves_the_sent_photo() -> None:
    tariff = _tariff(image_file_id=None)
    updates: list = []

    async def fake_update(db, target, **kwargs):
        updates.append(kwargs)
        return target

    message = MagicMock()
    message.photo = [SimpleNamespace(file_id='small'), SimpleNamespace(file_id='biggest')]
    message.answer = AsyncMock()
    state = MagicMock()
    state.get_data = AsyncMock(return_value={'tariff_id': 7})
    state.clear = AsyncMock()

    import app.handlers.admin.tariff_panel_settings as mod

    original_get, original_update = mod.get_tariff_by_id, mod.update_tariff
    mod.get_tariff_by_id = AsyncMock(return_value=tariff)
    mod.update_tariff = fake_update
    try:
        await _unwrap(mod.process_tariff_image)(
            message, SimpleNamespace(language='ru', telegram_id=1), MagicMock(), state
        )
    finally:
        mod.get_tariff_by_id, mod.update_tariff = original_get, original_update

    # Берём самый крупный размер из лесенки превью Telegram.
    assert updates == [{'image_file_id': 'biggest'}]
    state.clear.assert_awaited_once()


async def test_admin_rejects_a_document(monkeypatch) -> None:
    """Картинку, присланную файлом, Telegram не кладёт в message.photo."""
    update = AsyncMock()
    monkeypatch.setattr(admin_mod, 'get_tariff_by_id', AsyncMock(return_value=_tariff()))
    monkeypatch.setattr(admin_mod, 'update_tariff', update)

    message = MagicMock()
    message.photo = None
    message.answer = AsyncMock()
    state = MagicMock()
    state.get_data = AsyncMock(return_value={'tariff_id': 7})
    state.clear = AsyncMock()

    await _unwrap(admin_mod.process_tariff_image)(
        message, SimpleNamespace(language='ru', telegram_id=1), MagicMock(), state
    )

    update.assert_not_awaited()
    state.clear.assert_not_awaited()
    assert 'фотографией' in message.answer.await_args.args[0]


async def test_delete_clears_the_image(monkeypatch) -> None:
    updates: list = []

    async def fake_update(db, target, **kwargs):
        updates.append(kwargs)
        return target

    monkeypatch.setattr(admin_mod, 'get_tariff_by_id', AsyncMock(return_value=_tariff()))
    monkeypatch.setattr(admin_mod, 'update_tariff', fake_update)
    monkeypatch.setattr(admin_mod, '_show', AsyncMock())

    callback = MagicMock()
    callback.data = 'trf_img_del:7'
    callback.answer = AsyncMock()
    state = MagicMock()
    state.clear = AsyncMock()

    await _unwrap(admin_mod.delete_tariff_image)(
        callback, SimpleNamespace(language='ru', telegram_id=1), MagicMock(), state
    )

    assert updates == [{'image_file_id': None}]


async def test_upload_screen_sets_the_state(monkeypatch) -> None:
    monkeypatch.setattr(admin_mod, 'get_tariff_by_id', AsyncMock(return_value=_tariff()))

    callback = MagicMock()
    callback.data = 'trf_img:7'
    callback.answer = AsyncMock()
    callback.message.edit_text = AsyncMock()
    state = MagicMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()

    await _unwrap(admin_mod.start_edit_tariff_image)(
        callback, SimpleNamespace(language='ru', telegram_id=1), MagicMock(), state
    )

    state.set_state.assert_awaited_once_with(AdminStates.editing_tariff_image)
    state.update_data.assert_awaited_once_with(tariff_id=7)


def test_delete_filter_matches_only_the_delete_callback() -> None:
    """Префиксы кнопок не должны пересекаться.

    'trf_img_del:' и 'trf_img:' различаются двоеточием, поэтому порядок
    регистрации не важен. Стоит кому-то убрать двоеточие из фильтра — экран
    картинки начнёт перехватывать «Убрать», и удаление молча перестанет
    работать; тест держит это различие.
    """
    filters: dict = {}
    dispatcher = MagicMock()
    dispatcher.callback_query.register = lambda handler, *args, **kwargs: filters.setdefault(handler.__name__, args[0])
    dispatcher.message.register = lambda *args, **kwargs: None
    admin_mod.register_panel_settings_handlers(dispatcher)

    screen = filters['start_edit_tariff_image']
    delete = filters['delete_tariff_image']

    assert delete.resolve(SimpleNamespace(data='trf_img_del:7'))
    assert not delete.resolve(SimpleNamespace(data='trf_img:7'))
    assert screen.resolve(SimpleNamespace(data='trf_img:7'))


def test_real_update_tariff_accepts_the_image_field() -> None:
    """Настоящая update_tariff обязана принимать image_file_id.

    У неё перечислены все параметры поимённо, **kwargs нет. Тесты выше
    подменяют её заглушкой с **kwargs и потому пропустили бы TypeError —
    а он случился бы при первом же сохранении картинки в проде.
    """
    import inspect

    from app.database.crud.tariff import update_tariff

    parameters = inspect.signature(update_tariff).parameters

    assert 'image_file_id' in parameters, 'поле не доедет до базы'
    # ... = «не передан», чтобы отличать «не трогай» от «убери картинку».
    assert parameters['image_file_id'].default is Ellipsis


async def test_real_update_tariff_sets_and_clears_the_image() -> None:
    from app.database.crud.tariff import update_tariff

    db = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    tariff = _tariff(image_file_id=None)

    await update_tariff(db, tariff, image_file_id='new-id')
    assert tariff.image_file_id == 'new-id'

    await update_tariff(db, tariff, image_file_id=None)
    assert tariff.image_file_id is None

    # Не передали — трогать нельзя.
    tariff.image_file_id = 'kept'
    await update_tariff(db, tariff, name='Другое имя')
    assert tariff.image_file_id == 'kept'


# ---- регресс: картинка отставала на один экран ----


async def test_patched_edit_text_branch_sees_the_tariff_image(monkeypatch) -> None:
    """Экран выбранного тарифа рисуется патченным edit_text — и он тоже обязан
    читать контекст.

    Регресс: в message_patch была своя копия автодетекта поверх
    resolve_cached_section_file_id, мимо которой проезжала и картинка тарифа,
    и файлы из menu_images. На экране выбранного тарифа оставался баннер
    списка.
    """
    import app.utils.message_patch as patch_mod

    monkeypatch.setattr(svc, 'resolve_cached_section_file_id', AsyncMock(return_value='section-banner'))
    monkeypatch.setattr('app.utils.menu_section_detect.detect_section_key', lambda markup: 'subscription_purchase')
    svc.set_tariff_image_context(_tariff())

    assert await patch_mod._auto_section_media(MagicMock(), None) == FILE_ID


async def test_both_render_branches_agree(monkeypatch) -> None:
    """Обе ветки отрисовки обязаны показывать одно и то же.

    Разошлись они именно потому, что автодетект был скопирован в два файла.
    """
    import app.utils.message_patch as patch_mod
    import app.utils.photo_message as photo_mod

    monkeypatch.setattr(svc, 'resolve_cached_section_file_id', AsyncMock(return_value='section-banner'))
    monkeypatch.setattr('app.utils.menu_section_detect.detect_section_key', lambda markup: 'subscription_purchase')
    svc.set_tariff_image_context(_tariff())

    assert await patch_mod._auto_section_media(MagicMock(), None) == await photo_mod._auto_section_media(MagicMock())


async def test_middleware_clears_the_context_before_each_update() -> None:
    """Иначе выбранный тариф «прилипает» к следующему экрану.

    Ровно это и ломалось: после выбора тарифа кнопка «назад» показывала список
    тарифов с картинкой выбранного.
    """
    from app.middlewares.tariff_image_context import TariffImageContextMiddleware

    svc.set_tariff_image_context(_tariff())
    seen: list = []

    async def handler(event, data):
        seen.append(svc.get_tariff_image_context())

    await TariffImageContextMiddleware()(handler, MagicMock(), {})

    assert seen == [None], 'картинка предыдущего апдейта дожила до следующего'


def test_middleware_is_registered_for_messages_and_callbacks() -> None:
    """Апдейты приходят и сообщениями, и нажатиями — сбрасывать надо оба.

    Читаем исходник, а не импортируем app.bot: модуль тянет за собой пол-бота
    и в тестовом окружении не поднимается.
    """
    source = (Path(__file__).resolve().parents[2] / 'app' / 'bot.py').read_text(encoding='utf-8')
    name = 'TariffImageContextMiddleware'

    assert f'from app.middlewares.tariff_image_context import {name}' in source
    assert f'dp.message.middleware({name}())' in source
    assert f'dp.callback_query.middleware({name}())' in source
