"""Экраны роутер-ссылки: ключ показывается только на экране после оплаты.

Проверка ровно того требования, ради которого фичу делали осторожно: до
списания ни в тексте экрана, ни в ``callback_data`` кнопок не должно быть ни
ссылки, ни адреса хоста — иначе ключ можно было бы снять с трафика бота, не
платя. Поэтому тесты смотрят на то, что реально уходит в сообщение.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.handlers.router_link as screen
from app.services.router_link_service import Availability, PurchaseOutcome, RouterLocation


LINK = 'vless://user-uuid@nl.example:443?security=reality&sni=nl.example#%F0%9F%87%B3%F0%9F%87%B1%20Netherlands'

PAID = RouterLocation(
    host_uuid='host-nl',
    remark='🇳🇱 Netherlands',
    squad_uuid='squad-game-premium',
    squad_name='game-premium',
    address='nl.example',
    port=443,
)
FREE = RouterLocation(
    host_uuid='host-de',
    remark='Germany',
    squad_uuid='squad-game-standart',
    squad_name='game-standart',
    address='de.example',
    port=443,
)


@pytest.fixture(autouse=True)
def _screens_without_banners(without_section_banners):
    """Кейсы смотрят текст и кнопки — баннер раздела тут только мешает."""


@pytest.fixture
def render(monkeypatch):
    """Перехватывает отправку экрана: что ушло в caption и в клавиатуру."""
    sent = AsyncMock()
    monkeypatch.setattr(screen, 'edit_or_answer_photo', sent)
    return sent


def _user(balance_kopeks: int = 100_000):
    return SimpleNamespace(id=42, telegram_id=1, language='ru', balance_kopeks=balance_kopeks)


def _callback(data: str = 'router_link'):
    callback = MagicMock()
    callback.data = data
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.message.photo = None
    return callback


def _payload(render_mock) -> str:
    """Caption + все callback_data последнего экрана одной строкой."""
    kwargs = render_mock.await_args.kwargs
    keyboard = kwargs.get('keyboard')
    rows = getattr(keyboard, 'inline_keyboard', []) or []
    callbacks = [button.callback_data or '' for row in rows for button in row]
    return '\n'.join([kwargs.get('caption', ''), *callbacks])


@pytest.fixture
def available(monkeypatch):
    monkeypatch.setattr(
        screen, 'get_available_locations', AsyncMock(return_value=Availability([FREE, PAID], 'ok', 7))
    )
    monkeypatch.setattr(screen, 'get_price_kopeks', AsyncMock(return_value=19_900))
    monkeypatch.setattr(screen, 'purchased_host_uuids', AsyncMock(return_value={PAID.host_uuid}))


# ---- до оплаты ключа нет ----


async def test_location_list_shows_names_without_keys_or_addresses(render, available) -> None:
    await screen.show_router_link_menu(_callback(), _user(), MagicMock())

    payload = _payload(render)
    assert 'Germany' in payload and 'Netherlands' in payload
    assert 'vless://' not in payload, 'ключ не должен попадать в экран выбора'
    assert 'nl.example' not in payload and 'de.example' not in payload, 'адрес хоста до оплаты не показываем'
    # В кнопках ездит только UUID хоста.
    assert 'router_link_buy:host-de' in payload
    assert 'router_link_show:host-nl' in payload


async def test_confirm_screen_shows_price_without_the_key(render, available) -> None:
    await screen.confirm_router_link(_callback('router_link_buy:host-de'), _user(), MagicMock())

    payload = _payload(render)
    assert 'Germany' in payload
    assert 'vless://' not in payload
    assert 'router_link_pay:host-de' in payload


async def test_insufficient_balance_screen_has_no_key(render, available, monkeypatch) -> None:
    monkeypatch.setattr(
        screen,
        'purchase_location',
        AsyncMock(
            return_value=PurchaseOutcome(
                'insufficient', price_kopeks=19_900, missing_kopeks=14_900, location=FREE
            )
        ),
    )

    await screen.pay_router_link(_callback('router_link_pay:host-de'), _user(5_000), MagicMock())

    payload = _payload(render)
    assert 'vless://' not in payload
    # Экран нехватки средств ведёт в пополнение, а не к ключу.
    assert 'router_link' in payload


# ---- после оплаты ключ есть ----


async def test_paid_screen_shows_the_key(render, available, monkeypatch) -> None:
    monkeypatch.setattr(
        screen,
        'purchase_location',
        AsyncMock(return_value=PurchaseOutcome('ok', link=LINK, price_kopeks=19_900, location=FREE)),
    )

    await screen.pay_router_link(_callback('router_link_pay:host-de'), _user(), MagicMock())

    caption = render.await_args.kwargs['caption']
    assert 'vless://user-uuid@nl.example' in caption
    # `&` из query-параметров экранируется, иначе Telegram не разберёт разметку.
    assert '&amp;sni=' in caption
    assert '&sni=' not in caption.replace('&amp;sni=', '')


async def test_purchased_location_is_shown_again_without_charging(render, available, monkeypatch) -> None:
    monkeypatch.setattr(screen, 'resolve_link', AsyncMock(return_value=LINK))
    charge = AsyncMock()
    monkeypatch.setattr(screen, 'purchase_location', charge)

    await screen.show_router_link(_callback('router_link_show:host-nl'), _user(), MagicMock())

    assert 'vless://user-uuid@nl.example' in render.await_args.kwargs['caption']
    charge.assert_not_awaited()


async def test_unpaid_location_cannot_be_shown_through_the_show_callback(render, available, monkeypatch) -> None:
    """Подмена callback'а показа на неоплаченную локацию ведёт на оплату."""
    resolve = AsyncMock(return_value=LINK)
    monkeypatch.setattr(screen, 'resolve_link', resolve)

    await screen.show_router_link(_callback('router_link_show:host-de'), _user(), MagicMock())

    payload = _payload(render)
    assert 'vless://' not in payload
    assert 'router_link_pay:host-de' in payload, 'экран должен предложить оплату'
    resolve.assert_not_awaited()


async def test_paid_but_missing_key_keeps_the_access_message(render, available, monkeypatch) -> None:
    monkeypatch.setattr(
        screen,
        'purchase_location',
        AsyncMock(return_value=PurchaseOutcome('link_missing', price_kopeks=19_900, location=FREE)),
    )

    await screen.pay_router_link(_callback('router_link_pay:host-de'), _user(), MagicMock())

    caption = render.await_args.kwargs['caption']
    assert 'vless://' not in caption
    assert 'оплачен' in caption.lower()


# ---- пустые состояния ----


@pytest.mark.parametrize(
    ('reason', 'expected'),
    [
        ('disabled', 'не продаётся'),
        ('no_subscription', 'подписка'),
        ('no_squads', 'тариф'),
        ('panel_error', 'ещё раз'),
    ],
)
async def test_empty_states_explain_the_reason(render, monkeypatch, reason, expected) -> None:
    monkeypatch.setattr(screen, 'get_available_locations', AsyncMock(return_value=Availability([], reason)))

    await screen.show_router_link_menu(_callback(), _user(), MagicMock())

    caption = render.await_args.kwargs['caption'].lower()
    assert expected in caption
    assert 'vless://' not in caption
