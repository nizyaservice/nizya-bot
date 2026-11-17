"""Смена языка из меню работает сама по себе, без шагов регистрации.

Обработчик `language_select:` был в двух местах: в регистрации (состояние
`waiting_for_language`) и в меню (`StateFilter(None)`). Регистрационный удалён
вместе с шагами, и остаться без рабочей смены языка тут проще всего — поэтому
проверяется именно то, что нажатие доезжает до базы и перерисовывает меню.
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.handlers import menu


@pytest.fixture
def enabled(monkeypatch):
    """Фича включена, доступны ru и en — независимо от окружения прогона.

    Подменяем поля, а не методы: ``Settings`` — модель pydantic, присваивание
    чего-то, кроме поля, она запрещает. Зато так проверяются и сами геттеры.
    """
    monkeypatch.setattr(menu.settings, 'LANGUAGE_SELECTION_ENABLED', True)
    monkeypatch.setattr(menu.settings, 'AVAILABLE_LANGUAGES', 'ru,en')


def _callback(data: str) -> SimpleNamespace:
    return SimpleNamespace(data=data, answer=AsyncMock(), message=SimpleNamespace(), from_user=SimpleNamespace(id=1))


def _user(language: str = 'ru') -> SimpleNamespace:
    return SimpleNamespace(id=1, telegram_id=1, language=language)


@pytest.mark.asyncio
async def test_selected_language_reaches_the_database(enabled, monkeypatch):
    update_user = AsyncMock(return_value=_user('en'))
    monkeypatch.setattr(menu, 'update_user', update_user)
    show_main_menu = AsyncMock()
    monkeypatch.setattr(menu, 'show_main_menu', show_main_menu)
    callback = _callback('language_select:en')

    await menu.process_language_change(callback, db_user=_user('ru'), db=AsyncMock())

    update_user.assert_awaited_once()
    assert update_user.await_args.kwargs['language'] == 'en', 'язык обязан сохраниться у пользователя'
    # Меню перерисовывается уже на новом языке.
    show_main_menu.assert_awaited_once()
    callback.answer.assert_awaited()


@pytest.mark.asyncio
async def test_same_language_is_not_written_again(enabled, monkeypatch):
    update_user = AsyncMock()
    monkeypatch.setattr(menu, 'update_user', update_user)
    monkeypatch.setattr(menu, 'show_main_menu', AsyncMock())

    await menu.process_language_change(_callback('language_select:ru'), db_user=_user('ru'), db=AsyncMock())

    update_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_language_outside_the_available_list_is_refused(enabled, monkeypatch):
    update_user = AsyncMock()
    monkeypatch.setattr(menu, 'update_user', update_user)
    callback = _callback('language_select:zh')

    await menu.process_language_change(callback, db_user=_user('ru'), db=AsyncMock())

    update_user.assert_not_awaited()
    assert callback.answer.await_args.kwargs.get('show_alert') is True


def test_the_callback_is_still_registered_in_the_menu():
    """Единственный оставшийся обработчик нажатия — меню, состояние пустое."""
    source = pathlib.Path(menu.__file__).read_text(encoding='utf-8')

    assert "F.data.startswith('language_select:'), StateFilter(None)" in source
