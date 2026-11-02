"""На кнопке способа оплаты — название способа, а не имя шлюза.

Кнопки Platega подписывались как «🌍 Международные карты (Platega)»: человек
выбирает, чем заплатить, и имя провайдера ему ничего не говорит. Проверяем
заодно, что подпись чистится, а callback_data — нет: на ней держится вся
воронка пополнения.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.keyboards.inline import get_payment_methods_keyboard


SBP_QR = 2
INTERNATIONAL_CARD = 12


@pytest.fixture(autouse=True)
def _platega_on(monkeypatch):
    for key, value in {
        'PLATEGA_ENABLED': True,
        'PLATEGA_MERCHANT_ID': 'merchant',
        'PLATEGA_SECRET': 'secret',
        'PLATEGA_ACTIVE_METHODS': f'{SBP_QR},{INTERNATIONAL_CARD}',
        'PLATEGA_INLINE_METHODS': True,
        'PLATEGA_DISPLAY_NAME': 'Platega',
    }.items():
        monkeypatch.setattr(settings, key, value, raising=False)


def _buttons(amount_kopeks: int = 0) -> dict[str, str]:
    markup = get_payment_methods_keyboard(amount_kopeks, 'ru')
    return {
        button.callback_data: button.text for row in markup.inline_keyboard for button in row if button.callback_data
    }


def test_method_button_shows_only_the_method_name():
    label = _buttons()[f'topup_platega_m{INTERNATIONAL_CARD}']

    assert label == settings.get_platega_method_display_title(INTERNATIONAL_CARD)
    assert 'Platega' not in label
    assert '(' not in label


def test_every_active_method_is_clean():
    buttons = _buttons()

    for method_code in (SBP_QR, INTERNATIONAL_CARD):
        assert 'Platega' not in buttons[f'topup_platega_m{method_code}']


def test_prefilled_amount_keeps_the_clean_label_and_its_callback():
    """Экран быстрых сумм ведёт в ту же воронку."""
    buttons = _buttons(50000)
    callback = f'topup_amount|platega_m{INTERNATIONAL_CARD}|50000'

    assert buttons[callback] == settings.get_platega_method_display_title(INTERNATIONAL_CARD)


def test_single_gateway_button_still_names_the_gateway(monkeypatch):
    """Без разбивки по методам имя шлюза — единственное, что можно написать."""
    monkeypatch.setattr(settings, 'PLATEGA_INLINE_METHODS', False, raising=False)
    buttons = _buttons()

    assert 'Platega' in buttons['topup_platega']
    assert not [callback for callback in buttons if callback.startswith('topup_platega_m')]
