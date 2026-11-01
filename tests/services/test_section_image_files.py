"""Баннеры разделов читаются с диска, когда в админку ничего не загружали.

До этого картинка бралась только из system_settings: новый набор в репозитории
ничего не менял, пока оператор не зальёт каждую из тридцати штук руками.
Теперь файл из menu_images — штатный фолбэк, а загруженная вручную картинка
по-прежнему главнее: оператор должен уметь переопределить репозиторий.

Отдельно важен кеш file_id. Файл на полмегабайта уходил бы в Telegram заново
на каждый переход между экранами, поэтому после первой отправки запоминается
file_id, а сбрасывается он по времени изменения файла.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import FSInputFile

import app.services.section_image_service as svc


@pytest.fixture(autouse=True)
def _clean_cache():
    svc.reset_file_id_cache()
    yield
    svc.reset_file_id_cache()


# ---- раскладка файлов ----


@pytest.mark.parametrize(
    ('section_key', 'expected'),
    [
        ('main_menu', 'main_menu.png'),
        ('balance', 'balance/_main.png'),
        ('balance.topup', 'balance/topup.png'),
        ('subscription.devices', 'subscription/devices.png'),
        ('my_subscriptions', 'my_subscriptions/_main.png'),
    ],
)
def test_section_key_maps_to_a_file(section_key, expected) -> None:
    path = svc.get_section_image_path(section_key)

    assert path is not None, f'нет файла для {section_key}'
    assert path == svc.BANNERS_DIR / expected


def test_english_variant_wins_for_en_users() -> None:
    assert svc.get_section_image_path('balance.topup', 'en').name == 'topup_en.png'
    assert svc.get_section_image_path('balance.topup', 'ru').name == 'topup.png'


def test_missing_section_has_no_file() -> None:
    assert svc.get_section_image_path('такого-раздела-нет') is None
    assert svc.get_section_image_path('') is None


def test_every_banner_file_is_a_real_png() -> None:
    """Расширение .png должно означать PNG: Telegram-то содержимое распознает,
    но генератор и golden-тест на подменённый формат спотыкаются."""
    wrong = [
        str(path.relative_to(svc.BANNERS_DIR))
        for path in sorted(svc.BANNERS_DIR.rglob('*.png'))
        if path.read_bytes()[:4] != b'\x89PNG'
    ]

    assert not wrong, f'не PNG, несмотря на расширение: {wrong}'


# ---- приоритет и кеш ----


async def test_uploaded_image_wins_over_the_file(monkeypatch) -> None:
    monkeypatch.setattr(svc, 'get_setting_value', AsyncMock(return_value='uploaded-file-id'))

    assert await svc.get_section_media_kwargs(MagicMock(), 'balance') == {'media': 'uploaded-file-id'}


async def test_file_is_used_when_nothing_uploaded(monkeypatch) -> None:
    monkeypatch.setattr(svc, 'get_setting_value', AsyncMock(return_value=None))

    media = (await svc.get_section_media_kwargs(MagicMock(), 'balance'))['media']

    assert isinstance(media, FSInputFile)
    assert Path(media.path).name == '_main.png'


async def test_no_media_when_there_is_neither(monkeypatch) -> None:
    monkeypatch.setattr(svc, 'get_setting_value', AsyncMock(return_value=None))

    assert await svc.get_section_media_kwargs(MagicMock(), 'такого-раздела-нет') == {}


async def test_db_failure_leaves_the_screen_with_the_repository_file(monkeypatch) -> None:
    """Сбой чтения настроек не должен уносить экран: баннер — украшение.

    ``get_section_media_kwargs`` стоит в 120+ хендлерах, и исключение из неё
    превращало весь экран в «Ошибка обработки запроса». Теперь загруженная
    картинка пропускается, а файл из репозитория (БД ему не нужна) остаётся.
    """
    monkeypatch.setattr(svc, 'get_setting_value', AsyncMock(side_effect=RuntimeError('нет соединения с БД')))

    media = (await svc.get_section_media_kwargs(MagicMock(), 'balance'))['media']

    assert isinstance(media, FSInputFile)
    assert Path(media.path).name == '_main.png'


async def test_db_failure_without_a_file_leaves_no_media(monkeypatch) -> None:
    """Сбой БД и нет файла — пустые kwargs, а не исключение: экран уйдёт текстом."""
    monkeypatch.setattr(svc, 'get_setting_value', AsyncMock(side_effect=RuntimeError('нет соединения с БД')))
    monkeypatch.setattr(svc, 'get_section_image_path', lambda *args, **kwargs: None)

    assert await svc.get_section_media_kwargs(MagicMock(), 'balance') == {}


async def test_admin_screen_still_sees_the_db_failure(monkeypatch) -> None:
    """Админке «Фото разделов» ошибку чтения глушить нельзя — она её предмет."""
    monkeypatch.setattr(svc, 'get_setting_value', AsyncMock(side_effect=RuntimeError('нет соединения с БД')))

    with pytest.raises(RuntimeError):
        await svc.get_section_image_file_id(MagicMock(), 'balance')


def test_file_id_is_remembered_after_the_first_send() -> None:
    path = svc.get_section_image_path('balance')
    first = svc.section_file_media(path)
    assert isinstance(first, FSInputFile)

    sent = SimpleNamespace(photo=[SimpleNamespace(file_id='remembered-id')])
    svc.remember_file_photo(first, sent)

    assert svc.section_file_media(path) == 'remembered-id'


def test_replacing_the_file_invalidates_the_cache(tmp_path) -> None:
    """Иначе после подмены картинки бот показывал бы старую до перезапуска."""
    path = tmp_path / 'banner.png'
    path.write_bytes(b'\x89PNG first')
    svc.remember_file_photo(FSInputFile(path), SimpleNamespace(photo=[SimpleNamespace(file_id='old-id')]))
    assert svc.section_file_media(path) == 'old-id'

    import os

    path.write_bytes(b'\x89PNG second')
    os.utime(path, (0, 0))

    assert isinstance(svc.section_file_media(path), FSInputFile)


def test_remember_ignores_anything_that_is_not_a_file() -> None:
    """В media часто приходит готовый file_id — функция не должна на нём падать."""
    svc.remember_file_photo('some-file-id', SimpleNamespace(photo=[SimpleNamespace(file_id='x')]))
    svc.remember_file_photo(FSInputFile(svc.BANNERS_DIR / 'main_menu.png'), SimpleNamespace(photo=None))


# ---- автодетект ----


async def test_autodetect_falls_back_to_the_section_file(monkeypatch) -> None:
    """Подпункт без своей картинки берёт баннер раздела — и из файлов тоже."""
    monkeypatch.setattr(svc, 'resolve_cached_section_file_id', AsyncMock(return_value=None))

    media = await svc.resolve_section_media('balance.нет-такого')

    assert isinstance(media, FSInputFile)
    assert Path(media.path) == svc.BANNERS_DIR / 'balance' / '_main.png'


async def test_autodetect_prefers_uploaded(monkeypatch) -> None:
    monkeypatch.setattr(svc, 'resolve_cached_section_file_id', AsyncMock(return_value='uploaded'))

    assert await svc.resolve_section_media('balance') == 'uploaded'
