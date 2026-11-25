"""Витрина: что видно без входа.

Главное правило портала — смотреть можно, покупать после входа. Здесь оно и
закреплено: витрина, каталог, статус серверов и документы открываются без
сессии, а любое действие с деньгами уводит на вход.

Отдельно закреплено, что витрина переживает недоступность бота. Она —
первое, что видит человек, и показать ему пятисотку вместо страницы значит
потерять его до того, как он что-то узнал о сервисе.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from portal.api import CabinetApi


TARIFF: dict[str, Any] = {
    'id': 11,
    'name': 'Базовый',
    'description': 'Для одного устройства',
    'traffic_limit_label': '100 ГБ',
    'traffic_limit_gb': 100,
    'device_limit': 2,
    'servers_count': 3,
    'is_available': True,
    'is_daily': False,
    'is_purchased': False,
    'is_highlighted': False,
    'periods': [
        {'days': 30, 'label': '1 месяц', 'price_kopeks': 29900, 'price_per_month_kopeks': 29900},
    ],
}

STATUS: dict[str, Any] = {
    'enabled': True,
    'total': 2,
    'online': 1,
    'offline': 1,
    'servers': [
        {'name': 'nl', 'display_name': 'Нидерланды', 'flag': '🇳🇱', 'latency_ms': 31, 'is_online': True},
        {'name': 'fi', 'display_name': 'Финляндия', 'flag': '🇫🇮', 'latency_ms': None, 'is_online': False},
    ],
}


class Bot:
    def __init__(self, **overrides: httpx.Response) -> None:
        self.overrides = overrides
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(path)

        if path.endswith('/public/tariffs'):
            return self.overrides.get(
                'tariffs', httpx.Response(200, json={'sales_mode': 'tariffs', 'tariffs': [TARIFF]})
            )
        if path.endswith('/public/server-status'):
            return self.overrides.get('status', httpx.Response(200, json=STATUS))
        return httpx.Response(200, json={})


@pytest.fixture
def anonymous(portal_app):
    def make(double: Bot) -> TestClient:
        portal_app.state.api_double = CabinetApi(
            httpx.AsyncClient(base_url='http://bot.invalid', transport=httpx.MockTransport(double))
        )
        client = TestClient(portal_app, follow_redirects=False)
        client.__enter__()
        opened.append(client)
        return client

    opened: list[TestClient] = []
    yield make
    for client in opened:
        client.__exit__(None, None, None)


# ─── витрина ───


def test_the_shop_window_shows_prices_without_an_account(anonymous):
    response = anonymous(Bot()).get('/')

    assert response.status_code == 200
    assert 'Базовый' in response.text
    assert '299' in response.text
    assert 'Войти' in response.text, 'вход предлагается, но не требуется'


def test_the_shop_window_survives_a_silent_bot(anonymous):
    """Каталог и статус не пришли — страница всё равно открывается."""
    bot = Bot(
        tariffs=httpx.Response(503, json={'detail': 'down'}),
        status=httpx.Response(503, json={'detail': 'down'}),
    )

    response = anonymous(bot).get('/')

    assert response.status_code == 200
    assert 'Nizya' in response.text


def test_a_down_server_is_not_hidden_from_the_shop_window(anonymous):
    text = anonymous(Bot()).get('/').text

    assert '1 из 2 вне сети' in text


# ─── статус серверов ───


def test_status_page_lists_servers_without_a_session(anonymous):
    response = anonymous(Bot()).get('/status')

    assert response.status_code == 200
    assert 'Нидерланды' in response.text
    assert '31 мс' in response.text
    assert 'Финляндия' in response.text
    assert 'нет ответа' in response.text


def test_unreachable_metrics_do_not_look_like_an_outage(anonymous):
    """Метрики молчат — это «данных нет», а не «всё легло»."""
    response = anonymous(Bot(status=httpx.Response(503, json={'detail': 'down'}))).get('/status')

    assert response.status_code == 200
    assert 'Данных сейчас нет' in response.text
    assert 'вне сети' not in response.text


def test_disabled_integration_says_nothing_about_servers(anonymous):
    disabled = httpx.Response(200, json={'enabled': False, 'servers': [], 'total': 0, 'online': 0, 'offline': 0})

    response = anonymous(Bot(status=disabled)).get('/status')

    assert response.status_code == 200
    assert 'не публикует статус' in response.text
    # И на витрине строки о серверах тоже нет — обещать нечего.
    assert 'в сети' not in anonymous(Bot(status=disabled)).get('/').text


# ─── гейт ───


@pytest.mark.parametrize(
    'path',
    ['/dashboard', '/devices', '/balance', '/balance/topup', '/promocode', '/referral', '/tariffs/11?days=30'],
)
def test_personal_sections_send_the_person_to_login(anonymous, path: str):
    response = anonymous(Bot()).get(path)

    assert response.status_code == 303
    assert response.headers['location'].startswith('/login?next=')


def test_an_error_page_keeps_the_person_logged_in(portal_app):
    """Шапка на странице ошибки — как везде: иначе человек решит, что его выкинуло."""
    portal_app.state.api_double = CabinetApi(
        httpx.AsyncClient(base_url='http://bot.invalid', transport=httpx.MockTransport(Bot()))
    )

    with TestClient(portal_app, follow_redirects=False) as client:
        client.post('/login/code', data={'email': 'user@example.com', 'code': '123456'})
        # Форма без CSRF-токена: отвечает общий обработчик, а не страница.
        response = client.post('/promocode', data={'code': 'X'})

    assert response.status_code == 400
    assert 'Страница устарела' in response.text
    assert '>Войти<' not in response.text, 'вошедшему не предлагают войти заново'
