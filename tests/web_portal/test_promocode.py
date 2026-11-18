"""Активация промокода на портале.

Бот отвечает на отказ машинным кодом, а текст внутри — английский. Портал
обязан показывать свою русскую фразу по коду и не превращать незнакомый код в
«ошибку сервера»: иначе человек видит невнятицу вместо причины.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from portal.api import CabinetApi
from portal.routes.promocode import GENERIC_REFUSAL


class Bot:
    def __init__(self, response: httpx.Response | None = None) -> None:
        self.response = response or httpx.Response(
            200,
            json={
                'success': True,
                'message': 'Promo code activated successfully',
                'balance_before': 100.0,
                'balance_after': 350.0,
                'bonus_description': 'Начислено 250 ₽ на баланс',
            },
        )
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))

        if path.endswith('/auth/email/otp/verify'):
            return httpx.Response(
                200,
                json={'access_token': 'a', 'refresh_token': 'r', 'user': {'id': 7, 'email': 'user@example.com'}},
            )
        if path.endswith('/auth/me'):
            return httpx.Response(200, json={'id': 7, 'email': 'user@example.com'})
        if path.endswith('/promocode/activate'):
            return self.response
        return httpx.Response(200, json={})

    @property
    def activations(self) -> list[dict[str, Any] | None]:
        return [body for _, path, body in self.calls if path.endswith('/promocode/activate')]


@pytest.fixture
def logged_in(portal_app):
    opened: list[TestClient] = []

    def make(double: Bot) -> TestClient:
        transport = httpx.MockTransport(double)
        portal_app.state.api_double = CabinetApi(httpx.AsyncClient(base_url='http://bot.invalid', transport=transport))
        client = TestClient(portal_app, follow_redirects=False)
        client.__enter__()
        opened.append(client)
        client.post('/login/code', data={'email': 'user@example.com', 'code': '123456'})
        return client

    yield make

    for client in opened:
        client.__exit__(None, None, None)


def _csrf_of(html: str) -> str:
    marker = 'name="csrf_token" value="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


def test_form_opens(logged_in):
    response = logged_in(Bot()).get('/promocode')

    assert response.status_code == 200
    assert 'Промокод' in response.text


def test_code_reaches_the_bot_and_the_bonus_is_shown(logged_in):
    bot = Bot()
    client = logged_in(bot)
    csrf = _csrf_of(client.get('/promocode').text)

    response = client.post('/promocode', data={'code': ' bonus250 ', 'csrf_token': csrf})

    assert response.status_code == 200
    assert bot.activations == [{'code': 'bonus250'}], 'пробелы по краям обрезаются'
    assert 'Начислено 250 ₽ на баланс' in response.text


def test_empty_code_is_refused_without_calling_the_bot(logged_in):
    bot = Bot()
    client = logged_in(bot)
    csrf = _csrf_of(client.get('/promocode').text)

    response = client.post('/promocode', data={'code': '   ', 'csrf_token': csrf})

    assert response.status_code == 400
    assert bot.activations == []


def test_without_csrf_token_nothing_is_activated(logged_in):
    bot = Bot()
    client = logged_in(bot)

    response = client.post('/promocode', data={'code': 'bonus250'})

    assert response.status_code == 400
    assert bot.activations == []


def test_known_refusal_is_shown_in_russian(logged_in):
    bot = Bot(
        httpx.Response(
            400,
            json={'detail': {'code': 'already_used_by_user', 'message': 'You have already used this promo code'}},
        )
    )
    client = logged_in(bot)
    csrf = _csrf_of(client.get('/promocode').text)

    response = client.post('/promocode', data={'code': 'bonus250', 'csrf_token': csrf})

    assert response.status_code == 400
    assert 'Вы уже активировали этот промокод.' in response.text
    assert 'already used' not in response.text, 'английский текст бота человеку не показываем'
    assert 'value="bonus250"' in response.text, 'введённый код остаётся в поле'


def test_unknown_refusal_code_falls_back_to_a_readable_text(logged_in):
    bot = Bot(httpx.Response(400, json={'detail': {'code': 'brand_new_reason', 'message': 'Nope'}}))
    client = logged_in(bot)
    csrf = _csrf_of(client.get('/promocode').text)

    response = client.post('/promocode', data={'code': 'bonus250', 'csrf_token': csrf})

    assert GENERIC_REFUSAL in response.text


def test_choice_of_subscription_is_explained(logged_in):
    """Бот отвечает 200 с success=false, когда подписок несколько."""
    bot = Bot(httpx.Response(200, json={'success': False, 'error': 'select_subscription', 'code': 'bonus250'}))
    client = logged_in(bot)
    csrf = _csrf_of(client.get('/promocode').text)

    response = client.post('/promocode', data={'code': 'bonus250', 'csrf_token': csrf})

    assert response.status_code == 400
    assert 'выберите нужную в боте' in response.text
