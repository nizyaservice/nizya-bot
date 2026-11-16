"""Главная портала: что человек видит про свою подписку.

Ручка ``/subscription`` отдаёт обёртку ``{has_subscription, subscription}`` —
портал её разворачивает, иначе страница показывала бы пустые поля. Статус и
дата приходят машинными (``expired``, ISO-строка): на странице они по-русски.

Отдельно закреплено, что отказ необязательного блока не роняет всю страницу:
баланс не пришёл — подписка всё равно видна.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from portal.api import CabinetApi
from portal.templating import format_moment, status_label


SUBSCRIPTION = {
    'id': 1,
    'status': 'expired',
    'tariff_name': 'Базовый',
    'end_date': '2026-12-16T10:00:00',
    'time_left_display': '30д 2ч',
    'traffic_limit_gb': 50,
    'traffic_used_gb': 1.5,
    'device_limit': 3,
}


def _handler(*, balance_fails: bool = False):
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith('/auth/email/otp/verify'):
            return httpx.Response(
                200,
                json={'access_token': 'a', 'refresh_token': 'r', 'user': {'id': 7, 'email': 'user@example.com'}},
            )
        if path.endswith('/auth/me'):
            return httpx.Response(200, json={'id': 7, 'email': 'user@example.com'})
        if path.endswith('/subscription'):
            return httpx.Response(200, json={'has_subscription': True, 'subscription': SUBSCRIPTION})
        if path.endswith('/balance'):
            if balance_fails:
                return httpx.Response(500, json={'detail': 'Internal error'})
            return httpx.Response(200, json={'balance_kopeks': 50000, 'balance_rubles': 500.0})
        return httpx.Response(200, json={})

    return handle


@pytest.fixture
def client(portal_app):
    def make(*, balance_fails: bool = False) -> TestClient:
        transport = httpx.MockTransport(_handler(balance_fails=balance_fails))
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
    response = client().get('/')

    assert response.status_code == 200
    assert 'Базовый' in response.text
    assert '16.12.2026' in response.text, 'дата показана по-человечески'
    assert 'истекла' in response.text, 'статус переведён'
    assert '1.5 из 50 ГБ' in response.text
    assert 'expired' not in response.text


def test_balance_is_shown_in_rubles(client):
    assert '500.00 ₽' in client().get('/').text


def test_a_failing_block_does_not_break_the_page(client):
    """Баланс не пришёл — подписка всё равно на экране."""
    response = client(balance_fails=True).get('/')

    assert response.status_code == 200
    assert 'Базовый' in response.text
    assert 'Баланс' not in response.text, 'строки баланса нет, а страница есть'


# ─── фильтры шаблонов ───


def test_moment_keeps_unparsable_values_visible():
    assert format_moment('2026-12-16T10:00:00') == '16.12.2026'
    assert format_moment('скоро') == 'скоро'
    assert format_moment(None) == '—'


def test_unknown_status_is_shown_as_is():
    assert status_label('active') == 'активна'
    assert status_label('что-то новое') == 'что-то новое'
    assert status_label(None) == '—'
