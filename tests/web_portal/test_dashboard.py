"""Кабинет (`/dashboard`): что человек видит про свою подписку.

Ручка ``/subscription`` отдаёт обёртку ``{has_subscription, subscription}`` —
портал её разворачивает, иначе страница показывала бы пустые поля. Статус и
дата приходят машинными (``expired``, ISO-строка): на странице они по-русски.

Отдельно закреплено, что отказ необязательного блока не роняет всю страницу:
баланс не пришёл — подписка всё равно видна.

И главное про деньги: в шапке стоит сумма, доступная к оплате
(деньги + бонусные баллы), а не денежный кошелёк. Бот решает «хватает ли»
именно по ней, и меньшая цифра отговаривала бы от покупки, которая прошла бы.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from portal.api import CabinetApi
from portal.templating import fill_class, format_moment, period_label, rubles, status_label


SUBSCRIPTION = {
    'id': 1,
    'status': 'expired',
    'tariff_name': 'Базовый',
    'end_date': '2026-12-16T10:00:00',
    'time_left_display': '30д 2ч',
    'days_left': 30,
    'traffic_limit_gb': 50,
    'traffic_used_gb': 1.5,
    'device_limit': 3,
    'is_expired': True,
}

BALANCE = {
    'balance_kopeks': 50000,
    'balance_rubles': 500.0,
    'bonus_points_kopeks': 25000,
    'bonus_points_rubles': 250.0,
    'available_kopeks': 75000,
    'available_rubles': 750.0,
}


def _handler(*, balance_fails: bool = False, subscription: dict | None = SUBSCRIPTION):
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith('/auth/email/otp/verify'):
            return httpx.Response(
                200,
                json={'access_token': 'a', 'refresh_token': 'r', 'user': {'id': 7, 'email': 'user@example.com'}},
            )
        if path.endswith('/auth/me'):
            return httpx.Response(200, json={'id': 7, 'email': 'user@example.com', 'balance_kopeks': 50000})
        if path.endswith('/subscription'):
            return httpx.Response(
                200,
                json={'has_subscription': subscription is not None, 'subscription': subscription},
            )
        if path.endswith('/subscription/connection-link'):
            return httpx.Response(200, json={'subscription_url': 'https://sub.example/abc', 'hide_link': False})
        if path.endswith('/balance'):
            if balance_fails:
                return httpx.Response(500, json={'detail': 'Internal error'})
            return httpx.Response(200, json=BALANCE)
        return httpx.Response(200, json={})

    return handle


@pytest.fixture
def client(portal_app):
    def make(**kwargs) -> TestClient:
        transport = httpx.MockTransport(_handler(**kwargs))
        portal_app.state.api_double = CabinetApi(httpx.AsyncClient(base_url='http://bot.invalid', transport=transport))
        test_client = TestClient(portal_app, follow_redirects=False)
        test_client.__enter__()
        opened.append(test_client)
        test_client.post('/login/code', data={'email': 'user@example.com', 'code': '123456'})
        return test_client

    opened: list[TestClient] = []
    yield make
    for test_client in opened:
        test_client.__exit__(None, None, None)


def test_subscription_fields_reach_the_page(client):
    response = client().get('/dashboard')

    assert response.status_code == 200
    assert 'Базовый' in response.text
    assert '16.12.2026' in response.text, 'дата показана по-человечески'
    assert 'истекла' in response.text, 'статус переведён'
    assert 'из 50 ГБ' in response.text
    assert 'expired' not in response.text


def test_the_header_shows_what_can_actually_be_spent(client):
    """750 ₽ — деньги плюс баллы; 500 ₽ был бы денежный кошелёк."""
    text = client().get('/dashboard').text

    assert '750' in text
    assert 'на счету' in text


def test_connection_link_is_offered_when_there_is_a_subscription(client):
    assert 'https://sub.example/abc' in client().get('/dashboard').text


def test_without_a_subscription_the_page_offers_tariffs(client):
    response = client(subscription=None).get('/dashboard')

    assert response.status_code == 200
    assert 'Подписки пока нет' in response.text
    # Ссылку подключения спрашивать не у чего — её на экране и нет.
    assert 'sub.example' not in response.text


def test_a_failing_block_does_not_break_the_page(client):
    """Баланс не пришёл — подписка всё равно на экране."""
    response = client(balance_fails=True).get('/dashboard')

    assert response.status_code == 200
    assert 'Базовый' in response.text


# ─── фильтры шаблонов ───


def test_moment_keeps_unparsable_values_visible():
    assert format_moment('2026-12-16T10:00:00') == '16.12.2026'
    assert format_moment('скоро') == 'скоро'
    assert format_moment(None) == '—'


def test_unknown_status_is_shown_as_is():
    assert status_label('active') == 'активна'
    assert status_label('что-то новое') == 'что-то новое'
    assert status_label(None) == '—'


def test_kopeks_become_readable_rubles():
    assert rubles(29900).replace(' ', ' ').replace(' ', ' ') == '299 ₽'
    assert rubles(1234500).replace(' ', ' ').replace(' ', ' ') == '12 345 ₽'
    # Копейки показываем только когда они есть — иначе соврали бы на сумме.
    assert rubles(14950).replace(' ', ' ') == '149,50 ₽'
    assert rubles(None).replace(' ', ' ') == '0 ₽'


def test_bar_width_is_a_class_because_inline_styles_are_forbidden():
    assert fill_class(0) == 'fill-0'
    assert fill_class(73.4) == 'fill-75'
    assert fill_class(1000) == 'fill-100', 'за сто процентов полоса не вылезает'
    assert fill_class(-5) == 'fill-0'
    assert fill_class('нет данных') == 'fill-0'


def test_period_is_shortened_for_the_switcher():
    assert period_label(30).replace(' ', ' ') == '1 мес'
    assert period_label(90).replace(' ', ' ') == '3 мес'
    assert period_label(365).replace(' ', ' ') == '1 год'
    assert period_label(7).replace(' ', ' ') == '7 дней'
