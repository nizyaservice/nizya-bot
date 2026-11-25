"""Клиент портала к API бота.

Портал — серверный клиент: запросы уходят из его процесса в бот по туннелю.
Отсюда два свойства, которые тут закреплены: ошибку бота портал показывает
человеку его же словами, а «бот не ответил» отличает от «бот сказал нет» —
первое это заглушка «сервис недоступен», второе текст ошибки на странице.
"""

from __future__ import annotations

import httpx
import pytest

from portal.api import CabinetApi, CabinetApiError, CabinetUnavailableError


def _api(handler) -> CabinetApi:
    transport = httpx.MockTransport(handler)
    return CabinetApi(httpx.AsyncClient(base_url='http://bot.invalid', transport=transport))


@pytest.mark.asyncio
async def test_request_goes_to_the_cabinet_prefix():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen['path'] = request.url.path
        return httpx.Response(200, json={})

    await _api(handler).request_login_code('user@example.com')

    assert seen['path'] == '/cabinet/auth/email/otp/request'


@pytest.mark.asyncio
async def test_token_is_sent_as_bearer():
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen['auth'] = request.headers.get('authorization')
        return httpx.Response(200, json={'id': 1})

    await _api(handler).me('the-token')

    assert seen['auth'] == 'Bearer the-token'


@pytest.mark.asyncio
async def test_unauthenticated_calls_send_no_token():
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen['auth'] = request.headers.get('authorization')
        return httpx.Response(200, json={})

    await _api(handler).request_login_code('user@example.com')

    assert seen['auth'] is None


@pytest.mark.asyncio
async def test_error_detail_reaches_the_page():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={'detail': 'Invalid or expired code'})

    with pytest.raises(CabinetApiError) as raised:
        await _api(handler).verify_login_code('user@example.com', '123456', language='ru')

    assert raised.value.status_code == 400
    assert raised.value.detail == 'Invalid or expired code'


@pytest.mark.asyncio
async def test_validation_error_detail_is_unwrapped():
    """FastAPI отдаёт 422 списком объектов — человеку нужен msg, а не структура."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={'detail': [{'msg': 'value is not a valid email address'}]})

    with pytest.raises(CabinetApiError) as raised:
        await _api(handler).request_login_code('not-an-email')

    assert raised.value.detail == 'value is not a valid email address'


@pytest.mark.asyncio
async def test_non_json_error_does_not_crash_the_client():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text='<html>bad gateway</html>')

    with pytest.raises(CabinetApiError) as raised:
        await _api(handler).request_login_code('user@example.com')

    assert raised.value.status_code == 502
    assert raised.value.detail


@pytest.mark.asyncio
async def test_dead_tunnel_is_not_a_bot_error():
    """Разница существенная: туннель лёг — заглушка; бот отказал — текст ошибки."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('tunnel down')

    with pytest.raises(CabinetUnavailableError) as raised:
        await _api(handler).request_login_code('user@example.com')

    assert raised.value.status_code == 503


@pytest.mark.asyncio
async def test_empty_body_is_not_parsed_as_json():
    """Выход из кабинета отвечает пустым телом — это не повод падать."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    assert await _api(handler).logout('refresh') is None


@pytest.mark.asyncio
async def test_consent_documents_are_passed_only_when_present():
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={})

    api = _api(handler)
    await api.verify_login_code('user@example.com', '123456', language='ru')
    await api.verify_login_code('user@example.com', '123456', language='ru', accepted_legal_documents=['rules'])

    assert 'accepted_legal_documents' not in bodies[0]
    assert bodies[1]['accepted_legal_documents'] == ['rules']


@pytest.mark.asyncio
async def test_the_shop_window_asks_the_public_handles_without_a_token():
    """Витрина не передаёт токен — иначе цены зависели бы от того, кто смотрит.

    Токен в этих запросах был бы не просто лишним: ручка бота отдала бы
    персональные скидки, и страница, которую видит аноним, перестала бы быть
    одинаковой для всех.
    """
    seen: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get('authorization')))
        return httpx.Response(200, json={})

    api = _api(handler)
    await api.public_tariffs()
    await api.server_status()

    assert seen == [
        ('http://bot.invalid/cabinet/public/tariffs?language=ru', None),
        ('http://bot.invalid/cabinet/public/server-status', None),
    ]
