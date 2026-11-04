"""Выключатель кнопки «Автопродление через СБП» (Настройки → Продление через СБП).

Видимость живёт в ``system_settings`` (``sbp_recurring.button_visible``), а не в
``PLATEGA_RECURRENT_ENABLED``: значение из окружения перебивает БД, и такой
переключатель из админки молча не применялся бы.

Проверяем ровно то, что обещает админский экран:

* выключатель прячет вход в подключение — кнопки в меню автоплатежа нет,
  `sbp_recurring_menu` и `sbp_recurring_enable` отвечают «недоступно»;
* у кого привязка жива — кнопка и экран остаются, иначе человек остался бы со
  списаниями и без «отменить» (тот же принцип, что у строки Cashera);
* ключа в БД нет = кнопка показана: включение фичи не должно требовать ещё и
  отдельного «да» в админке.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.handlers.admin.sbp_recurring as admin_sbp
import app.handlers.subscription.autopay as autopay_mod
import app.services.sbp_recurring_settings as sbp_settings
from app.config import settings


@pytest.fixture(autouse=True)
def _screens_without_banners(without_section_banners):
    """Кейсы смотрят текст и кнопки — баннер раздела тут только мешает."""


def _unwrap(fn):
    """Снимает @admin_required/@error_handler: двойник не проходит is_admin."""
    while hasattr(fn, '__wrapped__'):
        fn = fn.__wrapped__
    return fn


def _configure_gate(monkeypatch, enabled: bool) -> None:
    monkeypatch.setattr(settings, 'PLATEGA_ENABLED', enabled, raising=False)
    monkeypatch.setattr(settings, 'PLATEGA_MERCHANT_ID', ('m' if enabled else None), raising=False)
    monkeypatch.setattr(settings, 'PLATEGA_SECRET', ('s' if enabled else None), raising=False)
    monkeypatch.setattr(settings, 'PLATEGA_RECURRENT_ENABLED', enabled, raising=False)


def _make_callback():
    cb = MagicMock()
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.message.photo = None
    cb.answer = AsyncMock()
    return cb


def _make_user():
    user = MagicMock()
    user.id = 1
    user.language = 'ru'
    return user


def _keyboard_callbacks(markup) -> list[str]:
    return [btn.callback_data for row in markup.inline_keyboard for btn in row if btn.callback_data]


def _keyboard_labels(markup) -> list[str]:
    return [btn.text for row in markup.inline_keyboard for btn in row]


def _live_record(monkeypatch, record) -> None:
    monkeypatch.setattr(
        'app.database.crud.platega_subscription.get_active_platega_subscription_by_subscription',
        AsyncMock(return_value=record),
    )


# ─── сам выключатель ───


async def test_missing_setting_means_visible(monkeypatch):
    monkeypatch.setattr(sbp_settings, 'get_setting_value', AsyncMock(return_value=None))

    assert await sbp_settings.is_button_visible(AsyncMock()) is True


@pytest.mark.parametrize('raw', ['false', 'FALSE', ' off ', '0', 'no'])
async def test_falsey_values_hide_the_button(monkeypatch, raw):
    monkeypatch.setattr(sbp_settings, 'get_setting_value', AsyncMock(return_value=raw))

    assert await sbp_settings.is_button_visible(AsyncMock()) is False


@pytest.mark.parametrize('raw', ['true', 'ON', '1', 'yes', 'кто это написал'])
async def test_truthy_and_garbage_keep_the_button(monkeypatch, raw):
    """Нечитаемое значение не должно прятать кнопку молча."""
    monkeypatch.setattr(sbp_settings, 'get_setting_value', AsyncMock(return_value=raw))

    assert await sbp_settings.is_button_visible(AsyncMock()) is True


async def test_unreadable_setting_keeps_the_button(monkeypatch):
    """Сорвавшееся чтение настройки не должно уносить экран автоплатежа."""
    monkeypatch.setattr(sbp_settings, 'get_setting_value', AsyncMock(side_effect=RuntimeError('db down')))

    assert await sbp_settings.is_button_visible(AsyncMock()) is True


async def test_setter_writes_and_commits(monkeypatch):
    upsert = AsyncMock()
    monkeypatch.setattr(sbp_settings, 'upsert_system_setting', upsert)
    db = AsyncMock()

    await sbp_settings.set_button_visible(db, False)

    assert upsert.await_args.args[1] == sbp_settings.BUTTON_VISIBLE_SETTING_KEY
    assert upsert.await_args.args[2] == 'false'
    db.commit.assert_awaited_once()


# ─── меню автоплатежа ───


async def test_hidden_switch_removes_the_button(monkeypatch):
    _configure_gate(monkeypatch, True)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()
    subscription = SimpleNamespace(id=10, tariff=SimpleNamespace(is_daily=True))

    monkeypatch.setattr(autopay_mod, '_resolve_subscription', AsyncMock(return_value=(subscription, 10)))
    monkeypatch.setattr(autopay_mod, 'is_sbp_button_visible', AsyncMock(return_value=False))
    _live_record(monkeypatch, None)

    await autopay_mod.handle_autopay_menu(cb, user, db)

    _, kwargs = cb.message.edit_text.call_args
    assert 'sbp_recurring_menu' not in _keyboard_callbacks(kwargs['reply_markup'])


async def test_hidden_switch_keeps_the_button_for_a_live_mandate(monkeypatch):
    """Иначе списания продолжатся, а отменить их будет негде."""
    _configure_gate(monkeypatch, True)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()
    subscription = SimpleNamespace(id=10, tariff=SimpleNamespace(is_daily=True))

    monkeypatch.setattr(autopay_mod, '_resolve_subscription', AsyncMock(return_value=(subscription, 10)))
    monkeypatch.setattr(autopay_mod, 'is_sbp_button_visible', AsyncMock(return_value=False))
    _live_record(monkeypatch, SimpleNamespace(status='ACTIVE', next_charge_at=None))

    await autopay_mod.handle_autopay_menu(cb, user, db)

    _, kwargs = cb.message.edit_text.call_args
    assert 'sbp_recurring_menu' in _keyboard_callbacks(kwargs['reply_markup'])


async def test_visible_switch_keeps_the_button(monkeypatch):
    _configure_gate(monkeypatch, True)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()
    subscription = SimpleNamespace(id=10, tariff=SimpleNamespace(is_daily=True))

    monkeypatch.setattr(autopay_mod, '_resolve_subscription', AsyncMock(return_value=(subscription, 10)))
    monkeypatch.setattr(autopay_mod, 'is_sbp_button_visible', AsyncMock(return_value=True))

    await autopay_mod.handle_autopay_menu(cb, user, db)

    _, kwargs = cb.message.edit_text.call_args
    assert 'sbp_recurring_menu' in _keyboard_callbacks(kwargs['reply_markup'])


# ─── экраны СБП-автопродления ───


async def test_menu_refuses_when_hidden_and_nothing_to_cancel(monkeypatch):
    _configure_gate(monkeypatch, True)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()

    monkeypatch.setattr(autopay_mod, '_resolve_subscription', AsyncMock(return_value=(SimpleNamespace(id=10), 10)))
    monkeypatch.setattr(autopay_mod, 'is_sbp_button_visible', AsyncMock(return_value=False))
    _live_record(monkeypatch, None)

    await autopay_mod.handle_sbp_recurring_menu(cb, user, db)

    assert cb.answer.await_args.kwargs.get('show_alert') is True
    cb.message.edit_text.assert_not_awaited()


async def test_menu_opens_when_hidden_but_mandate_is_live(monkeypatch):
    _configure_gate(monkeypatch, True)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()

    monkeypatch.setattr(autopay_mod, '_resolve_subscription', AsyncMock(return_value=(SimpleNamespace(id=10), 10)))
    monkeypatch.setattr(autopay_mod, 'is_sbp_button_visible', AsyncMock(return_value=False))
    _live_record(monkeypatch, SimpleNamespace(status='ACTIVE', next_charge_at=None))

    await autopay_mod.handle_sbp_recurring_menu(cb, user, db)

    cb.message.edit_text.assert_awaited_once()
    _, kwargs = cb.message.edit_text.call_args
    assert 'sbp_recurring_cancel' in _keyboard_callbacks(kwargs['reply_markup'])


async def test_enable_refuses_when_hidden(monkeypatch):
    """Старое сообщение в чате не должно подключать скрытую фичу."""
    _configure_gate(monkeypatch, True)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()

    monkeypatch.setattr(autopay_mod, 'is_sbp_button_visible', AsyncMock(return_value=False))
    mock_enable = AsyncMock()
    monkeypatch.setattr('app.services.payment.platega.enable_platega_sbp_recurring', mock_enable)

    await autopay_mod.handle_sbp_recurring_enable(cb, user, db)

    mock_enable.assert_not_awaited()
    assert cb.answer.await_args.kwargs.get('show_alert') is True


async def test_cancel_works_when_hidden_and_does_not_reopen_the_menu(monkeypatch):
    """После отмены привязки нет — перерисованное меню выдало бы «недоступно»."""
    _configure_gate(monkeypatch, True)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()

    monkeypatch.setattr(autopay_mod, '_resolve_subscription', AsyncMock(return_value=(SimpleNamespace(id=10), 10)))
    monkeypatch.setattr(autopay_mod, 'is_sbp_button_visible', AsyncMock(return_value=False))
    mock_cancel = AsyncMock()
    monkeypatch.setattr('app.services.payment.platega.cancel_platega_recurring_for_subscription_safe', mock_cancel)

    await autopay_mod.handle_sbp_recurring_cancel(cb, user, db)

    mock_cancel.assert_awaited_once_with(db, 10)
    cb.message.edit_text.assert_not_awaited()


# ─── админский экран ───


async def test_admin_panel_offers_to_hide_a_visible_button(monkeypatch):
    _configure_gate(monkeypatch, True)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()
    monkeypatch.setattr(admin_sbp, 'is_button_visible', AsyncMock(return_value=True))

    await _unwrap(admin_sbp.show_sbp_recurring_panel)(cb, user, db)

    _, kwargs = cb.message.edit_text.call_args
    labels = ' '.join(_keyboard_labels(kwargs['reply_markup']))
    assert 'Скрыть' in labels
    assert 'admin_sbp_recurring_toggle' in _keyboard_callbacks(kwargs['reply_markup'])


async def test_admin_panel_offers_to_show_a_hidden_button(monkeypatch):
    _configure_gate(monkeypatch, True)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()
    monkeypatch.setattr(admin_sbp, 'is_button_visible', AsyncMock(return_value=False))

    await _unwrap(admin_sbp.show_sbp_recurring_panel)(cb, user, db)

    _, kwargs = cb.message.edit_text.call_args
    assert 'Показать' in ' '.join(_keyboard_labels(kwargs['reply_markup']))


async def test_admin_panel_names_the_gateway_when_it_is_off(monkeypatch):
    """Выключен шлюз — экран обязан это сказать, иначе «кнопка показана» врёт."""
    _configure_gate(monkeypatch, False)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()
    monkeypatch.setattr(admin_sbp, 'is_button_visible', AsyncMock(return_value=True))

    await _unwrap(admin_sbp.show_sbp_recurring_panel)(cb, user, db)

    text = cb.message.edit_text.call_args.args[0]
    assert 'PLATEGA_RECURRENT_ENABLED' in text


async def test_admin_toggle_flips_and_saves(monkeypatch):
    _configure_gate(monkeypatch, True)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()
    monkeypatch.setattr(admin_sbp, 'is_button_visible', AsyncMock(return_value=True))
    saver = AsyncMock()
    monkeypatch.setattr(admin_sbp, 'set_button_visible', saver)

    await _unwrap(admin_sbp.toggle_sbp_recurring_button)(cb, user, db)

    assert saver.await_args.args[1] is False
    assert 'Показать' in ' '.join(_keyboard_labels(cb.message.edit_text.call_args.kwargs['reply_markup']))
