"""Управление устройствами на портале.

Что закреплено:

* имя устройства берётся как в боте: своё название, иначе «платформа — модель»;
* удаление необратимо, поэтому GET только спрашивает, а удаляет POST с
  CSRF-токеном — подделанная форма не должна отключать чужие устройства;
* пустое поле названия снимает своё имя, а не падает;
* «устройства уже нет» и «подписки нет» — это экраны, а не пятисотка.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from portal.api import CabinetApi


HWID_NAMED = 'hw-1'
HWID_PLAIN = 'hw-2'

DEVICES = {
    'devices': [
        {
            'hwid': HWID_NAMED,
            'platform': 'android',
            'device_model': 'Pixel 8',
            'local_name': 'Телефон жены',
            'created_at': '2026-11-10T10:00:00',
        },
        {
            'hwid': HWID_PLAIN,
            'platform': 'ios',
            'device_model': 'iPhone 15 Pro Max',
            'local_name': None,
            'created_at': '2026-11-12T10:00:00',
        },
    ],
    'total': 2,
    'device_limit': 3,
}


class Bot:
    """Двойник бота: вход, профиль и ручки устройств; помнит запросы."""

    def __init__(self, devices: dict[str, Any] | None = None, status_code: int = 200) -> None:
        self.devices_payload = devices if devices is not None else DEVICES
        self.devices_status = status_code
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
        if path.endswith('/name'):
            return httpx.Response(200, json={'local_name': (body or {}).get('name')})
        if path.endswith('/subscription/devices'):
            if request.method == 'DELETE':
                return httpx.Response(200, json={'success': True, 'deleted_count': 2})
            if self.devices_status != 200:
                return httpx.Response(self.devices_status, json={'detail': 'No subscription found'})
            return httpx.Response(200, json=self.devices_payload)
        if '/subscription/devices/' in path and request.method == 'DELETE':
            return httpx.Response(200, json={'success': True, 'deleted_hwid': path.rsplit('/', 1)[-1]})
        return httpx.Response(200, json={})

    def calls_to(self, needle: str, method: str | None = None) -> list[dict[str, Any] | None]:
        return [
            body
            for call_method, path, body in self.calls
            if needle in path and (method is None or call_method == method)
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


def test_list_shows_own_name_and_falls_back_to_the_model(client: TestClient):
    response = client.get('/devices')

    assert response.status_code == 200
    assert 'Телефон жены' in response.text, 'своё название важнее платформы'
    assert 'ios — iPhone 15 Pro Max' in response.text, 'без своего названия — платформа и модель'
    assert '2 из 3' in response.text


def test_list_requires_login(portal_app, bot: Bot):
    transport = httpx.MockTransport(bot)
    portal_app.state.api_double = CabinetApi(httpx.AsyncClient(base_url='http://bot.invalid', transport=transport))
    with TestClient(portal_app, follow_redirects=False) as anonymous:
        response = anonymous.get('/devices')

    assert response.status_code == 303
    # Адрес, куда человек шёл, едет в ?next — после входа он попадёт туда же.
    assert response.headers['location'] == '/login?next=%2Fdevices'


def test_list_without_subscription_is_a_page_not_an_error(logged_in):
    client = logged_in(Bot(status_code=404))

    response = client.get('/devices')

    assert response.status_code == 200
    assert 'Устройств пока нет' in response.text


def test_empty_list_does_not_offer_the_reset(logged_in):
    client = logged_in(Bot(devices={'devices': [], 'total': 0, 'device_limit': 3}))

    response = client.get('/devices')

    assert '/devices-reset' not in response.text


# ─── переименование ───


def test_card_offers_the_rename_form(client: TestClient):
    response = client.get(f'/devices/{HWID_NAMED}')

    assert response.status_code == 200
    assert 'Телефон жены' in response.text
    assert f'/devices/{HWID_NAMED}/rename' in response.text


def test_unknown_device_is_reported_as_gone(client: TestClient):
    response = client.get('/devices/hw-404')

    assert response.status_code == 404
    assert 'уже отключено' in response.text


def test_rename_sends_the_new_name(client: TestClient, bot: Bot):
    csrf = _csrf_of(client.get(f'/devices/{HWID_NAMED}').text)

    response = client.post(f'/devices/{HWID_NAMED}/rename', data={'name': ' Планшет ', 'csrf_token': csrf})

    assert response.status_code == 303
    assert bot.calls_to('/name') == [{'name': 'Планшет'}], 'пробелы по краям обрезаются'


def test_empty_name_clears_the_alias(client: TestClient, bot: Bot):
    csrf = _csrf_of(client.get(f'/devices/{HWID_NAMED}').text)

    client.post(f'/devices/{HWID_NAMED}/rename', data={'name': '   ', 'csrf_token': csrf})

    assert bot.calls_to('/name') == [{'name': None}], 'пустое поле снимает своё название'


def test_rename_without_csrf_token_does_nothing(client: TestClient, bot: Bot):
    response = client.post(f'/devices/{HWID_NAMED}/rename', data={'name': 'Чужое'})

    assert response.status_code == 400
    assert bot.calls_to('/name') == []


# ─── удаление ───


def test_remove_asks_before_deleting(client: TestClient, bot: Bot):
    response = client.get(f'/devices/{HWID_NAMED}/remove')

    assert response.status_code == 200
    assert 'Отключить' in response.text and 'Телефон жены' in response.text
    assert bot.calls_to('/subscription/devices/', method='DELETE') == [], 'GET не должен удалять'


def test_remove_with_csrf_token_deletes_the_device(client: TestClient, bot: Bot):
    csrf = _csrf_of(client.get(f'/devices/{HWID_NAMED}/remove').text)

    response = client.post(f'/devices/{HWID_NAMED}/remove', data={'csrf_token': csrf})

    assert response.status_code == 303
    assert response.headers['location'] == '/devices'
    assert len(bot.calls_to('/subscription/devices/', method='DELETE')) == 1


def test_remove_without_csrf_token_does_nothing(client: TestClient, bot: Bot):
    response = client.post(f'/devices/{HWID_NAMED}/remove', data={})

    assert response.status_code == 400
    assert bot.calls_to('/subscription/devices/', method='DELETE') == []


def test_reset_all_asks_first(client: TestClient, bot: Bot):
    response = client.get('/devices-reset')

    assert response.status_code == 200
    assert 'Отключить все устройства?' in response.text
    assert bot.calls_to('/subscription/devices', method='DELETE') == []


def test_reset_all_with_csrf_token_deletes_everything(client: TestClient, bot: Bot):
    csrf = _csrf_of(client.get('/devices-reset').text)

    response = client.post('/devices-reset', data={'csrf_token': csrf})

    assert response.status_code == 303
    assert len(bot.calls_to('/subscription/devices', method='DELETE')) == 1


def test_reset_all_without_csrf_token_does_nothing(client: TestClient, bot: Bot):
    response = client.post('/devices-reset', data={})

    assert response.status_code == 400
    assert bot.calls_to('/subscription/devices', method='DELETE') == []
