"""Разделы кабинета: подключение, документы, язык, рефералка.

Что закреплено:

* ссылка подписки видна человеку, а когда её нет — экран объясняет, почему;
* документы оператора показываются разметкой, а не текстом с тегами;
* смена языка пишется в бота и сразу видна в сессии (от неё зависит язык
  документов и шапка);
* реферальная ссылка портала работает: код из адреса доезжает до регистрации.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from portal.api import CabinetApi


CONNECTION = {
    'subscription_url': 'https://panel.example/sub/abc123',
    'display_link': 'https://panel.example/sub/abc123',
    'happ_crypto_link': 'happ://crypt5/xyz',
    'hide_link': False,
}

RULES_HTML = '<h2>Правила</h2><p>Пользуйтесь на здоровье.</p>'


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
        if path.endswith('/auth/email/otp/request'):
            return httpx.Response(200, json={'expires_in_seconds': 900})
        if path.endswith('/auth/me'):
            return httpx.Response(200, json={'id': 7, 'email': 'user@example.com', 'language': 'ru'})
        if path.endswith('/subscription/connection-link'):
            if 'connection' in self.overrides:
                return self.overrides['connection']
            return httpx.Response(200, json=CONNECTION)
        if path.endswith('/info/service'):
            return httpx.Response(200, json={'name': 'Nizya', 'channel_url': 'https://t.me/nizya_vpn'})
        if path.endswith('/info/rules'):
            return self.overrides.get('rules', httpx.Response(200, json={'content': RULES_HTML}))
        if path.endswith('/info/languages'):
            return httpx.Response(
                200,
                json={
                    'languages': [
                        {'code': 'ru', 'name': 'Русский', 'flag': '🇷🇺'},
                        {'code': 'en', 'name': 'English', 'flag': '🇬🇧'},
                    ],
                    'default': 'ru',
                },
            )
        if path.endswith('/info/user/language'):
            return httpx.Response(200, json={'language': (body or {}).get('language')})
        if path.endswith('/referral/list'):
            return self.overrides.get(
                'referral_list',
                httpx.Response(200, json={'referrals': [{'name': 'Петя', 'created_at': '2026-11-01T10:00:00'}]}),
            )
        if path.endswith('/referral'):
            return httpx.Response(
                200,
                json={
                    'referral_code': 'CODE123',
                    'referral_link': 'https://t.me/bot?start=ref_CODE123',
                    'bot_referral_link': 'https://t.me/bot?start=ref_CODE123',
                    'total_referrals': 3,
                    'active_referrals': 1,
                    'total_earnings_kopeks': 12345,
                    'total_earnings_rubles': 123.45,
                    'commission_percent': 25,
                },
            )
        return httpx.Response(200, json={})

    def calls_to(self, needle: str) -> list[dict[str, Any] | None]:
        return [body for _, path, body in self.calls if path.endswith(needle)]


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


# ─── подключение ───


def test_subscription_link_is_shown(client: TestClient):
    response = client.get('/connect')

    assert response.status_code == 200
    assert 'https://panel.example/sub/abc123' in response.text
    assert 'happ://crypt5/xyz' in response.text


def test_without_subscription_the_reason_is_explained(logged_in):
    client = logged_in(Bot(connection=httpx.Response(404, json={'detail': 'No subscription found'})))

    response = client.get('/connect')

    assert response.status_code == 200
    assert 'No subscription found' in response.text or 'активная подписка' in response.text
    assert '/tariffs' in response.text, 'человеку нужен путь к покупке'


# ─── документы ───


def test_document_is_rendered_as_markup(client: TestClient):
    response = client.get('/documents/rules')

    assert response.status_code == 200
    assert '<h2>Правила</h2>' in response.text, 'теги оператора — это разметка, а не текст'
    assert '&lt;h2&gt;' not in response.text


def test_unknown_document_is_a_page_not_a_crash(client: TestClient):
    response = client.get('/documents/unknown')

    assert response.status_code == 404
    assert 'Такого документа' in response.text


def test_hidden_document_says_so(logged_in):
    client = logged_in(Bot(rules=httpx.Response(404, json={'detail': 'Rules are not available'})))

    response = client.get('/documents/rules')

    assert response.status_code == 404
    assert 'не опубликован' in response.text


# ─── язык ───


def test_language_list_marks_the_current_one(client: TestClient):
    response = client.get('/language')

    assert response.status_code == 200
    assert 'Русский' in response.text
    assert 'English' in response.text
    assert 'value="ru"' in response.text


def test_language_choice_reaches_the_bot(client: TestClient, bot: Bot):
    csrf = _csrf_of(client.get('/language').text)

    response = client.post('/language', data={'language': 'en', 'csrf_token': csrf})

    assert response.status_code == 303
    assert bot.calls_to('/info/user/language') == [{'language': 'en'}]


def test_language_without_csrf_token_does_nothing(client: TestClient, bot: Bot):
    response = client.post('/language', data={'language': 'en'})

    assert response.status_code == 400
    assert bot.calls_to('/info/user/language') == []


# ─── рефералка ───


def test_referral_page_shows_code_and_totals(client: TestClient):
    response = client.get('/referral')

    assert response.status_code == 200
    assert 'CODE123' in response.text
    assert '123.45 ₽' in response.text
    assert '25%' in response.text
    assert 'Петя' in response.text


def test_referral_page_survives_a_broken_list(logged_in):
    """Список приглашённых — необязательная часть экрана."""
    client = logged_in(Bot(referral_list=httpx.Response(500, json={'detail': 'boom'})))

    response = client.get('/referral')

    assert response.status_code == 200
    assert 'CODE123' in response.text


# ─── реферальная ссылка портала ───


def test_referral_code_from_the_link_reaches_registration(portal_app):
    bot = Bot()
    transport = httpx.MockTransport(bot)
    portal_app.state.api_double = CabinetApi(httpx.AsyncClient(base_url='http://bot.invalid', transport=transport))

    with TestClient(portal_app, follow_redirects=False) as client:
        page = client.get('/login?ref=CODE123')
        assert 'name="ref" value="CODE123"' in page.text, 'код должен доехать до формы'

        client.post('/login', data={'email': 'new@example.com', 'ref': 'CODE123'})
        client.post('/login/code', data={'email': 'new@example.com', 'code': '123456', 'ref': 'CODE123'})

    assert bot.calls_to('/auth/email/otp/verify') == [
        {
            'email': 'new@example.com',
            'code': '123456',
            'language': 'ru',
            'referral_code': 'CODE123',
        }
    ]


def test_junk_in_the_referral_parameter_is_dropped(portal_app):
    """Код из адреса — чужой ввод: что не похоже на код, не передаём боту."""
    bot = Bot()
    transport = httpx.MockTransport(bot)
    portal_app.state.api_double = CabinetApi(httpx.AsyncClient(base_url='http://bot.invalid', transport=transport))

    with TestClient(portal_app, follow_redirects=False) as client:
        page = client.get('/login?ref=%3Cscript%3Ealert(1)%3C/script%3E')
        assert 'name="ref"' not in page.text

        client.post('/login/code', data={'email': 'new@example.com', 'code': '123456', 'ref': '<script>'})

    expected = {'email': 'new@example.com', 'code': '123456', 'language': 'ru'}
    assert bot.calls_to('/auth/email/otp/verify') == [expected]
