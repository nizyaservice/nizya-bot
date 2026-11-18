"""Помощь: обращения в поддержку с портала.

Что закреплено:

* тикеты можно выключить в боте — тогда раздел показывает, куда писать, а не
  пустой список и не пятисотку;
* обращение создаётся только POST'ом с CSRF-токеном, тема и текст обязательны;
* переписка видна, ответ уходит в бота, закрытое обращение не предлагает форму.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from portal.api import CabinetApi


TICKET = {
    'id': 12,
    'title': 'Не подключается',
    'status': 'open',
    'priority': 'normal',
    'created_at': '2026-11-17T10:00:00',
    'updated_at': '2026-11-17T11:00:00',
    'is_reply_blocked': False,
    'messages': [
        {
            'id': 1,
            'message_text': 'Ссылка не открывается',
            'is_from_admin': False,
            'created_at': '2026-11-17T10:00:00',
        },
        {
            'id': 2,
            'message_text': 'Проверьте приложение',
            'is_from_admin': True,
            'created_at': '2026-11-17T11:00:00',
        },
    ],
}


class Bot:
    def __init__(self, **overrides: Any) -> None:
        self.overrides = overrides
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
        if path.endswith('/info/support-config'):
            return self.overrides.get(
                'config',
                httpx.Response(200, json={'tickets_enabled': True, 'support_type': 'tickets'}),
            )
        if path.endswith('/tickets') and request.method == 'POST':
            return self.overrides.get('create', httpx.Response(200, json=TICKET))
        if path.endswith('/tickets'):
            return self.overrides.get(
                'list',
                httpx.Response(200, json={'items': [TICKET], 'total': 1, 'page': 1, 'per_page': 20, 'pages': 1}),
            )
        if path.endswith('/messages'):
            return httpx.Response(200, json={'id': 3, 'message_text': (body or {}).get('message')})
        if '/tickets/' in path:
            return self.overrides.get('detail', httpx.Response(200, json=TICKET))
        return httpx.Response(200, json={})

    def calls_to(self, needle: str, method: str | None = None) -> list[dict[str, Any] | None]:
        return [
            body
            for call_method, path, body in self.calls
            if path.endswith(needle) and (method is None or call_method == method)
        ]


@pytest.fixture
def bot() -> Bot:
    return Bot()


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


@pytest.fixture
def client(logged_in, bot: Bot) -> TestClient:
    return logged_in(bot)


def _csrf_of(html: str) -> str:
    marker = 'name="csrf_token" value="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


# ─── список ───


def test_tickets_are_listed(client: TestClient):
    response = client.get('/support')

    assert response.status_code == 200
    assert 'Не подключается' in response.text
    assert 'Создать обращение' in response.text


def test_disabled_tickets_show_where_to_write(logged_in):
    bot = Bot(
        config=httpx.Response(
            200,
            json={
                'tickets_enabled': False,
                'support_type': 'profile',
                'support_username': '@nizya_support',
            },
        )
    )
    client = logged_in(bot)

    response = client.get('/support')

    assert response.status_code == 200
    assert 'недоступны' in response.text
    assert 'https://t.me/nizya_support' in response.text
    assert bot.calls_to('/tickets') == [], 'список не запрашиваем, когда тикеты выключены'


def test_tickets_switched_off_between_requests_is_not_an_error(logged_in):
    """Настройку поменяли между двумя запросами — ведём себя как при выключенных."""
    bot = Bot(list=httpx.Response(403, json={'detail': 'Tickets are disabled'}))
    client = logged_in(bot)

    response = client.get('/support')

    assert response.status_code == 200
    assert 'недоступны' in response.text


# ─── создание ───


def test_ticket_is_created_and_opens(client: TestClient, bot: Bot):
    csrf = _csrf_of(client.get('/support').text)

    response = client.post(
        '/support',
        data={'title': ' Не подключается ', 'message': ' Ссылка не открывается ', 'csrf_token': csrf},
    )

    assert response.status_code == 303
    assert response.headers['location'] == '/support/12'
    assert bot.calls_to('/tickets', method='POST') == [{'title': 'Не подключается', 'message': 'Ссылка не открывается'}]


def test_short_title_is_refused_without_calling_the_bot(client: TestClient, bot: Bot):
    csrf = _csrf_of(client.get('/support').text)

    response = client.post('/support', data={'title': 'ой', 'message': 'текст', 'csrf_token': csrf})

    assert response.status_code == 400
    assert 'от трёх символов' in response.text
    assert bot.calls_to('/tickets', method='POST') == []


def test_empty_message_is_refused(client: TestClient, bot: Bot):
    csrf = _csrf_of(client.get('/support').text)

    response = client.post('/support', data={'title': 'Проблема', 'message': '   ', 'csrf_token': csrf})

    assert response.status_code == 400
    assert bot.calls_to('/tickets', method='POST') == []


def test_without_csrf_token_no_ticket_is_created(client: TestClient, bot: Bot):
    response = client.post('/support', data={'title': 'Проблема', 'message': 'текст'})

    assert response.status_code == 400
    assert bot.calls_to('/tickets', method='POST') == []


# ─── переписка ───


def test_conversation_is_shown(client: TestClient):
    response = client.get('/support/12')

    assert response.status_code == 200
    assert 'Ссылка не открывается' in response.text
    assert 'Проверьте приложение' in response.text
    assert 'Поддержка' in response.text


def test_reply_reaches_the_bot(client: TestClient, bot: Bot):
    csrf = _csrf_of(client.get('/support/12').text)

    response = client.post('/support/12', data={'message': 'Переустановил, работает', 'csrf_token': csrf})

    assert response.status_code == 303
    assert bot.calls_to('/messages') == [{'message': 'Переустановил, работает'}]


def test_reply_without_csrf_token_is_not_sent(client: TestClient, bot: Bot):
    response = client.post('/support/12', data={'message': 'Чужое сообщение'})

    assert response.status_code == 400
    assert bot.calls_to('/messages') == []


def test_closed_ticket_offers_no_reply_form(logged_in):
    closed = {**TICKET, 'status': 'closed', 'closed_at': '2026-11-17T12:00:00'}
    client = logged_in(Bot(detail=httpx.Response(200, json=closed)))

    response = client.get('/support/12')

    assert 'Обращение закрыто' in response.text
    assert 'name="message"' not in response.text


def test_unavailable_ticket_is_a_page(logged_in):
    client = logged_in(Bot(detail=httpx.Response(404, json={'detail': 'Ticket not found'})))

    response = client.get('/support/404')

    assert response.status_code == 404
    assert 'Ticket not found' in response.text
