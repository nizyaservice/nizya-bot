"""Обновление access-токена на страницах портала.

Access-токен кабинета живёт 15 минут, а открытая страница — неделями. Поэтому
при 401 портал один раз обменивает refresh-токен и повторяет запрос. Повтор
ровно один: если и свежий токен получает 401, дело не в сроке, и цикл
обновлений ничего не починит — сессию надо закрывать.
"""

from __future__ import annotations

import httpx
import pytest

from portal.api import CabinetApi, CabinetApiError
from portal.deps import CsrfError, SessionExpiredError, check_csrf, with_fresh_token
from portal.sessions import SessionStore


def _api(handler) -> CabinetApi:
    return CabinetApi(httpx.AsyncClient(base_url='http://bot.invalid', transport=httpx.MockTransport(handler)))


@pytest.mark.asyncio
async def test_fresh_token_passes_through(store: SessionStore):
    session = await store.create('good', 'refresh')
    calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get('authorization'))
        return httpx.Response(200, json={'id': 1})

    api = _api(handler)
    result = await with_fresh_token(session, store, api, api.me)

    assert result == {'id': 1}
    assert calls == ['Bearer good'], 'без 401 обновлять нечего'


@pytest.mark.asyncio
async def test_expired_token_is_refreshed_once_and_the_call_repeats(store: SessionStore):
    session = await store.create('stale', 'refresh-1')
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith('/auth/refresh'):
            return httpx.Response(200, json={'access_token': 'fresh', 'refresh_token': 'refresh-2'})

        token = request.headers.get('authorization')
        seen.append(token)
        if token == 'Bearer stale':
            return httpx.Response(401, json={'detail': 'Token expired'})
        return httpx.Response(200, json={'id': 1})

    api = _api(handler)
    result = await with_fresh_token(session, store, api, api.me)

    assert result == {'id': 1}
    assert seen == ['Bearer stale', 'Bearer fresh']

    # Новая пара токенов должна осесть в сессии, иначе следующая страница
    # снова пойдёт со просроченным.
    stored = await store.get(session.sid)
    assert stored is not None
    assert stored.access_token == 'fresh'
    assert stored.refresh_token == 'refresh-2'


@pytest.mark.asyncio
async def test_rotation_is_optional(store: SessionStore):
    """Если бот не прислал новый refresh-токен — остаётся прежний, не пустой."""
    session = await store.create('stale', 'refresh-1')

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith('/auth/refresh'):
            return httpx.Response(200, json={'access_token': 'fresh'})
        if request.headers.get('authorization') == 'Bearer stale':
            return httpx.Response(401, json={'detail': 'Token expired'})
        return httpx.Response(200, json={})

    api = _api(handler)
    await with_fresh_token(session, store, api, api.me)

    stored = await store.get(session.sid)
    assert stored is not None
    assert stored.refresh_token == 'refresh-1'


@pytest.mark.asyncio
async def test_dead_refresh_token_closes_the_session(store: SessionStore):
    session = await store.create('stale', 'dead-refresh')

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith('/auth/refresh'):
            return httpx.Response(401, json={'detail': 'Invalid refresh token'})
        return httpx.Response(401, json={'detail': 'Token expired'})

    api = _api(handler)
    with pytest.raises(SessionExpiredError):
        await with_fresh_token(session, store, api, api.me)

    assert await store.get(session.sid) is None, 'мёртвую сессию держать незачем'


@pytest.mark.asyncio
async def test_other_errors_are_not_treated_as_expiry(store: SessionStore):
    """403 или 500 обновлением токена не лечатся — пробрасываем как есть."""
    session = await store.create('good', 'refresh')
    refreshed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith('/auth/refresh'):
            refreshed.append('called')
            return httpx.Response(200, json={'access_token': 'fresh'})
        return httpx.Response(403, json={'detail': 'Forbidden'})

    api = _api(handler)
    with pytest.raises(CabinetApiError) as raised:
        await with_fresh_token(session, store, api, api.me)

    assert raised.value.status_code == 403
    assert refreshed == [], 'обновлять токен при 403 бессмысленно'


@pytest.mark.asyncio
async def test_second_401_does_not_loop(store: SessionStore):
    """Свежий токен тоже получил 401 — отдаём ошибку, а не крутим обновления."""
    session = await store.create('stale', 'refresh')
    refresh_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith('/auth/refresh'):
            refresh_calls.append('x')
            return httpx.Response(200, json={'access_token': 'fresh'})
        return httpx.Response(401, json={'detail': 'Token expired'})

    api = _api(handler)
    with pytest.raises(CabinetApiError):
        await with_fresh_token(session, store, api, api.me)

    assert len(refresh_calls) == 1


@pytest.mark.asyncio
async def test_csrf_token_must_match(store: SessionStore):
    session = await store.create('access', 'refresh')

    check_csrf(session, session.csrf_token)

    for submitted in (None, '', 'someone-elses-token'):
        with pytest.raises(CsrfError):
            check_csrf(session, submitted)
