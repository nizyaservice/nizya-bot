"""Вход на портал: почта → код → сессия.

Пароля нет. Проверяется то, что человек видит и что уходит в браузер: экран
кода вместо подсказки «такого аккаунта нет», cookie без токенов, выход,
закрытый CSRF-токеном.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from portal.api import CabinetApi
from portal.sessions import SessionStore


def _api(handler) -> CabinetApi:
    return CabinetApi(httpx.AsyncClient(base_url='http://bot.invalid', transport=httpx.MockTransport(handler)))


def _tokens_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith('/auth/email/otp/request'):
        return httpx.Response(200, json={'expires_in_seconds': 900, 'resend_after_seconds': 60})
    if path.endswith('/auth/email/otp/verify'):
        return httpx.Response(
            200,
            json={
                'access_token': 'access-1',
                'refresh_token': 'refresh-1',
                'user': {'id': 7, 'email': 'user@example.com'},
            },
        )
    if path.endswith('/auth/logout'):
        return httpx.Response(204)
    if path.endswith('/auth/me'):
        return httpx.Response(200, json={'id': 7, 'email': 'user@example.com'})
    return httpx.Response(200, json={})


@pytest.fixture
def client(portal_app) -> TestClient:
    portal_app.state.api_double = _api(_tokens_handler)
    with TestClient(portal_app, follow_redirects=False) as test_client:
        yield test_client


def test_login_page_opens(client: TestClient):
    response = client.get('/login')

    assert response.status_code == 200
    assert 'Выслать код' in response.text


def test_requesting_a_code_shows_the_code_screen(client: TestClient):
    response = client.post('/login', data={'email': 'user@example.com'})

    assert response.status_code == 200
    assert 'Код из письма' in response.text
    # Почта возвращается скрытым полем, а не в адресе страницы.
    assert 'name="email"' in response.text
    assert 'user@example.com' not in str(response.url)


def test_empty_email_is_refused_without_calling_the_bot(portal_app):
    called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request.url.path)
        return httpx.Response(200, json={})

    portal_app.state.api_double = _api(handler)
    with TestClient(portal_app, follow_redirects=False) as test_client:
        response = test_client.post('/login', data={'email': '  '})

    assert response.status_code == 400
    assert called == []


def test_bot_refusal_is_shown_to_the_person(portal_app):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={'detail': 'Disposable email addresses are not allowed'})

    portal_app.state.api_double = _api(handler)
    with TestClient(portal_app, follow_redirects=False) as test_client:
        response = test_client.post('/login', data={'email': 'user@mailinator.com'})

    assert response.status_code == 400
    assert 'Disposable email addresses are not allowed' in response.text


def test_correct_code_opens_a_session(client: TestClient, store: SessionStore):
    response = client.post('/login/code', data={'email': 'user@example.com', 'code': '123456'})

    assert response.status_code == 303
    assert response.headers['location'] == '/dashboard'

    cookie = response.cookies.get('portal_session')
    assert cookie, 'портал должен поставить cookie сессии'
    # В cookie — только идентификатор: токенов в браузере быть не должно.
    assert 'access-1' not in cookie
    assert 'refresh-1' not in cookie

    set_cookie = response.headers['set-cookie'].lower()
    assert 'httponly' in set_cookie
    assert 'samesite=lax' in set_cookie


def test_wrong_code_keeps_the_person_on_the_code_screen(portal_app):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={'detail': 'Invalid or expired code'})

    portal_app.state.api_double = _api(handler)
    with TestClient(portal_app, follow_redirects=False) as test_client:
        response = test_client.post('/login/code', data={'email': 'user@example.com', 'code': '000000'})

    assert response.status_code == 400
    assert 'Код из письма' in response.text
    assert 'Invalid or expired code' in response.text
    assert not response.cookies.get('portal_session')


def test_consent_is_asked_once_and_then_submitted(portal_app):
    """Бот отвечает 428, пока не присланы галочки, — портал их и рисует."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        path = request.url.path
        if path.endswith('/info/legal-consent'):
            return httpx.Response(200, json={'documents': [{'key': 'rules', 'title': 'Правила', 'url': '/rules'}]})
        if path.endswith('/auth/email/otp/verify'):
            payload = json.loads(request.content)
            seen.append(payload)
            if not payload.get('accepted_legal_documents'):
                return httpx.Response(428, json={'detail': 'Нужно принять документы'})
            return httpx.Response(200, json={'access_token': 'a', 'refresh_token': 'r', 'user': {}})
        return httpx.Response(200, json={})

    portal_app.state.api_double = _api(handler)
    with TestClient(portal_app, follow_redirects=False) as test_client:
        first = test_client.post('/login/code', data={'email': 'user@example.com', 'code': '123456'})
        assert first.status_code == 428
        assert 'Правила' in first.text

        second = test_client.post(
            '/login/code',
            data={'email': 'user@example.com', 'code': '123456', 'accepted': 'rules'},
        )

    assert second.status_code == 303
    assert seen[1]['accepted_legal_documents'] == ['rules']


def test_cabinet_requires_login(client: TestClient):
    response = client.get('/dashboard')

    assert response.status_code == 303
    assert response.headers['location'] == '/login?next=%2Fdashboard'


def test_the_shop_window_is_open_without_a_session(client: TestClient):
    """Витрина не просит входа: цену смотрят до регистрации."""
    assert client.get('/').status_code == 200
    assert client.get('/tariffs').status_code == 200
    assert client.get('/status').status_code == 200


def test_after_login_the_person_lands_where_they_were_going(client: TestClient):
    """Адрес возврата доезжает от гейта до формы и дальше в редирект."""
    gate = client.get('/tariffs/1?days=30')
    assert gate.status_code == 303
    assert gate.headers['location'] == '/login?next=%2Ftariffs%2F1%3Fdays%3D30'

    form = client.get(gate.headers['location'])
    assert 'value="/tariffs/1?days=30"' in form.text, 'адрес возврата едет скрытым полем'

    done = client.post(
        '/login/code',
        data={'email': 'user@example.com', 'code': '123456', 'next': '/tariffs/1?days=30'},
    )

    assert done.headers['location'] == '/tariffs/1?days=30'


def test_a_foreign_address_is_not_a_place_to_return_to(client: TestClient):
    """`//evil.example` браузер читает как чужой сайт — такой возврат отбрасываем."""
    done = client.post(
        '/login/code',
        data={'email': 'user@example.com', 'code': '123456', 'next': '//evil.example/steal'},
    )

    assert done.headers['location'] == '/dashboard'


def test_a_logged_in_person_is_sent_to_the_cabinet(client: TestClient):
    client.post('/login/code', data={'email': 'user@example.com', 'code': '123456'})

    response = client.get('/')

    assert response.status_code == 303
    assert response.headers['location'] == '/dashboard'


def test_cabinet_opens_with_a_session(client: TestClient):
    client.post('/login/code', data={'email': 'user@example.com', 'code': '123456'})

    response = client.get('/dashboard')

    assert response.status_code == 200
    assert 'Ваша подписка' in response.text


def test_logout_without_csrf_token_does_nothing(client: TestClient, store: SessionStore):
    client.post('/login/code', data={'email': 'user@example.com', 'code': '123456'})
    sid = client.cookies.get('portal_session')

    response = client.post('/logout', data={})

    assert response.status_code == 303
    assert await_session(store, sid) is not None, 'подделанный выход не должен закрывать сессию'


def test_logout_with_csrf_token_closes_the_session(client: TestClient, store: SessionStore):
    client.post('/login/code', data={'email': 'user@example.com', 'code': '123456'})
    sid = client.cookies.get('portal_session')
    session = await_session(store, sid)
    assert session is not None

    response = client.post('/logout', data={'csrf_token': session.csrf_token})

    assert response.status_code == 303
    # После выхода — на витрину: человек вышел нарочно, и форма входа
    # с пустым полем была бы тупиком.
    assert response.headers['location'] == '/'
    assert await_session(store, sid) is None


def test_logout_with_a_foreign_csrf_token_does_nothing(client: TestClient, store: SessionStore):
    """Токен с не-ASCII символами — отказ, а не пятисотка: ``compare_digest``
    на таких строках бросает TypeError, если сравнивать их напрямую."""
    client.post('/login/code', data={'email': 'user@example.com', 'code': '123456'})
    sid = client.cookies.get('portal_session')

    response = client.post('/logout', data={'csrf_token': 'подделка'})

    assert response.status_code == 303
    assert await_session(store, sid) is not None


def test_healthz_does_not_need_the_bot(client: TestClient):
    """Проверка живости не должна падать вместе с туннелем до бота."""
    assert client.get('/healthz').json() == {'status': 'ok'}


def await_session(store: SessionStore, sid: str | None):
    """Синхронная обёртка: тесты страниц синхронные, а хранилище асинхронное.

    Двойник Redis — обычный словарь, поэтому своя петля событий ему безразлична.
    """
    import asyncio

    return asyncio.run(store.get(sid))
