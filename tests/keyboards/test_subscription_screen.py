"""Экран «Моя подписка»: состав текста и клавиатуры.

Экран «Настройки» свёрнут — его содержимое поднято на уровень выше: на месте
кнопки настроек стоит «Управление устройствами», перевыпуск идёт отдельной
строкой во всю ширину. Докупка слотов устройств убрана вместе с экраном.

Из текста убраны блок-цитата с описанием тарифа, строки «Серверы» и
«Устройства» и список подключённых устройств: расход и устройства
пользователь смотрит в приложении, а управлять ими идёт по кнопке.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.keyboards.inline as kb


LOCALE_DIRS = (
    Path(__file__).resolve().parents[2] / 'app' / 'localization' / 'locales',
    Path(__file__).resolve().parents[2] / 'locales',
)
LANGS = ('ru', 'en', 'ua', 'fa', 'zh')
TEMPLATE_KEYS = ('SUBSCRIPTION_OVERVIEW_TEMPLATE', 'SUBSCRIPTION_DAILY_OVERVIEW_TEMPLATE')

# Подсказка вместо цифр расхода — своя на каждом языке, но «Happ» в ней везде.
TRAFFIC_HINT = 'Happ/Incy'


def _callbacks(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


def _rows(markup) -> list[list[str]]:
    return [[b.callback_data or 'url' for b in row] for row in markup.inline_keyboard]


def _subscription(**overrides):
    tariff = SimpleNamespace(is_daily=False, is_free=False, device_price_kopeks=0, can_topup_traffic=lambda: True)
    values = {
        'id': 1,
        'tariff': tariff,
        'tariff_id': 7,
        'actual_status': 'active',
        'status': 'active',
        'traffic_limit_gb': 10,
        'subscription_url': 'https://sub.example/x',
        'subscription_crypto_link': None,
        'is_trial': False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _paid_keyboard(**overrides):
    return kb.get_subscription_keyboard(
        language='ru', has_subscription=True, is_trial=False, subscription=_subscription(**overrides)
    )


# ---- клавиатура платной подписки ----


def test_manage_devices_replaces_the_settings_button() -> None:
    callbacks = _callbacks(_paid_keyboard())

    assert 'subscription_manage_devices' in callbacks
    assert 'subscription_settings' not in callbacks


def test_manage_devices_shares_a_row_with_tariff() -> None:
    """Разметка сохранена: прежний ряд [Настройки] [Тариф] стал [Устройства] [Тариф]."""
    rows = _rows(_paid_keyboard())
    row = next(r for r in rows if 'subscription_manage_devices' in r)

    assert row == ['subscription_manage_devices', 'instant_switch']


def test_revoke_takes_a_full_row_below(monkeypatch) -> None:
    monkeypatch.setattr(type(kb.settings), 'is_subscription_revoke_enabled', lambda self: True)
    rows = _rows(_paid_keyboard())

    devices_at = next(i for i, r in enumerate(rows) if 'subscription_manage_devices' in r)

    assert rows[devices_at + 1] == ['subscription_revoke']


def test_revoke_row_respects_its_setting(monkeypatch) -> None:
    monkeypatch.setattr(type(kb.settings), 'is_subscription_revoke_enabled', lambda self: False)

    assert 'subscription_revoke' not in _callbacks(_paid_keyboard())


def test_device_slot_purchase_is_gone() -> None:
    """Докупка слотов жила только на экране настроек и ушла вместе с ним."""
    assert 'subscription_change_devices' not in _callbacks(_paid_keyboard())


def test_trial_still_has_no_device_management() -> None:
    """Для пробника управление устройствами закрыто — решение не менялось.

    Хендлер отказывает триалу отдельно (devices.py, PAID_FEATURE_ONLY),
    так что кнопка здесь была бы тупиком.
    """
    trial = kb.get_subscription_keyboard(
        language='ru', has_subscription=True, is_trial=True, subscription=_subscription(is_trial=True)
    )
    callbacks = _callbacks(trial)

    assert 'subscription_manage_devices' not in callbacks
    assert 'subscription_settings' not in callbacks


# ---- текст экрана ----


@pytest.mark.parametrize('directory', LOCALE_DIRS, ids=('base', 'override'))
@pytest.mark.parametrize('lang', LANGS)
@pytest.mark.parametrize('key', TEMPLATE_KEYS)
def test_overview_template_drops_servers_and_devices(directory, lang, key) -> None:
    template = json.loads((directory / f'{lang}.json').read_text(encoding='utf-8'))[key]

    assert '{servers}' not in template
    assert '{devices_used}' not in template
    assert '{device_limit}' not in template


@pytest.mark.parametrize('directory', LOCALE_DIRS, ids=('base', 'override'))
@pytest.mark.parametrize('lang', LANGS)
@pytest.mark.parametrize('key', TEMPLATE_KEYS)
def test_traffic_line_points_to_the_app(directory, lang, key) -> None:
    template = json.loads((directory / f'{lang}.json').read_text(encoding='utf-8'))[key]

    assert '{traffic}' not in template, 'цифры расхода должны были уступить место подсказке'
    assert TRAFFIC_HINT in template
