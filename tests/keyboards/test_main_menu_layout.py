"""Раскладка главного меню: пять строк по утверждённому макету.

Меню — самый частый экран бота, его видят в каждой сессии, и ломается он
незаметно: кнопка просто уезжает в другую строку или пропадает по условию.
Поэтому здесь проверяется не «кнопка существует», а порядок строк целиком.

Макет:
    ⚡ Подключить VPN            (или пробный период — новичку)
    💜 Тарифы и продление
    📊 Моя подписка | 💰 Баланс
    💬 Помощь       | 🎁 Позвать друга
    ⋯ Ещё
"""

from types import SimpleNamespace

import pytest

from app.config import settings
from app.keyboards.inline import get_main_menu_keyboard, get_more_menu_keyboard


def _subscription(**overrides):
    values = {
        'subscription_url': 'https://sub.example/abc',
        'subscription_crypto_link': None,
        'is_trial': False,
        'is_active': True,
        'traffic_limit_gb': 0,
        'tariff_id': 1,
        'autopay_enabled': False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _rows(keyboard) -> list[list[str]]:
    return [[button.callback_data or 'url' for button in row] for row in keyboard.inline_keyboard]


def _texts(keyboard) -> list[list[str]]:
    return [[button.text for button in row] for row in keyboard.inline_keyboard]


def _flat(keyboard) -> list[str]:
    return [data for row in _rows(keyboard) for data in row]


@pytest.fixture
def active_menu():
    return get_main_menu_keyboard(
        'ru',
        has_active_subscription=True,
        subscription_is_active=True,
        balance_kopeks=0,
        subscription=_subscription(),
    )


def test_active_subscriber_sees_the_mockup_rows(active_menu) -> None:
    assert _rows(active_menu) == [
        ['url'],  # «Подключить VPN» — web_app/url, зависит от CONNECT_BUTTON_MODE
        ['menu_buy'],
        ['menu_subscription', 'menu_balance'],
        ['menu_support', 'menu_referrals'],
        ['menu_more'],
    ]


def test_button_labels_match_the_mockup(active_menu) -> None:
    labels = _texts(active_menu)
    assert labels[0] == ['⚡ Подключить VPN']
    assert labels[1] == ['💜 Тарифы и продление']
    assert labels[2][0] == '📊 Моя подписка'
    assert labels[3] == ['💬 Помощь', '🎁 Позвать друга']
    assert labels[4] == ['⋯ Ещё']


def test_tariffs_button_stays_for_active_subscriber(active_menu) -> None:
    """Раньше «Купить подписку» пряталась при активной подписке.

    Кнопка называется «Тарифы и продление» именно потому, что действующий
    подписчик меняет тариф и продлевается на том же экране.
    """
    assert 'menu_buy' in _flat(active_menu)


def test_newcomer_gets_trial_in_place_of_connect() -> None:
    keyboard = get_main_menu_keyboard('ru', balance_kopeks=0)
    rows = _rows(keyboard)

    assert rows[0] == ['menu_trial']
    assert rows[1] == ['menu_buy']
    # Без подписки «Моя подписка» не нужна — строка остаётся только с балансом.
    assert rows[2] == ['menu_balance']
    assert rows[-1] == ['menu_more']


def test_connect_button_requires_a_working_subscription() -> None:
    """Истёкшая подписка не даёт «Подключить VPN», но «Моя подписка» остаётся."""
    keyboard = get_main_menu_keyboard(
        'ru',
        has_active_subscription=True,
        subscription_is_active=False,
        has_had_paid_subscription=True,
        balance_kopeks=0,
        subscription=_subscription(is_active=False),
    )
    rows = _rows(keyboard)

    assert rows[0] == ['menu_buy']
    assert 'menu_subscription' in _flat(keyboard)


@pytest.mark.parametrize(
    'moved_away',
    ['menu_promocode', 'menu_language', 'menu_info', 'simple_subscription_purchase', 'buy_traffic'],
)
def test_secondary_buttons_are_not_on_the_main_screen(active_menu, moved_away) -> None:
    assert moved_away not in _flat(active_menu)


def test_admin_button_stays_last() -> None:
    keyboard = get_main_menu_keyboard('ru', is_admin=True, balance_kopeks=0)

    assert _rows(keyboard)[-1] == ['admin_panel']


# ---- подменю «Ещё» ----


def test_more_menu_has_the_requested_entries() -> None:
    keyboard = get_more_menu_keyboard('ru')

    assert _rows(keyboard) == [
        ['menu_promocode'],
        ['url'],  # канал — внешняя ссылка
        ['menu_language'],
        ['menu_info'],
        ['back_to_menu'],
    ]


def test_channel_button_carries_the_configured_url() -> None:
    keyboard = get_more_menu_keyboard('ru')
    channel = keyboard.inline_keyboard[1][0]

    assert channel.url == settings.MENU_CHANNEL_URL
    assert channel.callback_data is None


def test_channel_button_disappears_without_a_url(monkeypatch) -> None:
    """Пустой URL — кнопки нет: Telegram отвергает кнопку с пустой ссылкой."""
    monkeypatch.setattr(settings, 'MENU_CHANNEL_URL', '')

    assert 'url' not in _flat(get_more_menu_keyboard('ru'))


def test_info_entry_is_renamed_to_rules() -> None:
    """«Инфо» переименован в «Правила и условия», раздел тот же."""
    keyboard = get_more_menu_keyboard('ru')
    info = next(b for row in keyboard.inline_keyboard for b in row if b.callback_data == 'menu_info')

    assert info.text == '📋 Правила и условия'
