"""Серверные сессии портала.

Токены кабинета остаются на сервере: в браузер уходит только непредсказуемый
идентификатор. Отсюда свойства, которые тут и закреплены — из cookie нельзя
достать токен, выход действительно убивает сессию, а битую запись портал
выбрасывает вместо падения.
"""

from __future__ import annotations

import json

import pytest

from portal.sessions import SESSION_KEY_PREFIX, SessionStore


@pytest.mark.asyncio
async def test_created_session_has_unpredictable_id(store: SessionStore):
    first = await store.create('access-1', 'refresh-1')
    second = await store.create('access-2', 'refresh-2')

    assert first.sid != second.sid
    assert len(first.sid) >= 32, 'идентификатор сессии должен быть не угадываемым'


@pytest.mark.asyncio
async def test_tokens_live_in_storage_not_in_the_identifier(store: SessionStore, fake_redis):
    session = await store.create('access-token', 'refresh-token')

    assert 'access-token' not in session.sid
    stored = json.loads(fake_redis.items[f'{SESSION_KEY_PREFIX}{session.sid}'])
    assert stored['access_token'] == 'access-token'
    assert stored['refresh_token'] == 'refresh-token'


@pytest.mark.asyncio
async def test_session_round_trip(store: SessionStore):
    created = await store.create('access', 'refresh', user={'email': 'user@example.com'})

    loaded = await store.get(created.sid)

    assert loaded is not None
    assert loaded.access_token == 'access'
    assert loaded.refresh_token == 'refresh'
    assert loaded.csrf_token == created.csrf_token
    assert loaded.user == {'email': 'user@example.com'}


@pytest.mark.asyncio
async def test_each_session_gets_its_own_csrf_token(store: SessionStore):
    first = await store.create('a', 'b')
    second = await store.create('c', 'd')

    assert first.csrf_token != second.csrf_token


@pytest.mark.asyncio
async def test_missing_and_empty_identifiers_are_no_session(store: SessionStore):
    assert await store.get(None) is None
    assert await store.get('') is None
    assert await store.get('unknown') is None


@pytest.mark.asyncio
async def test_deleted_session_is_gone(store: SessionStore):
    session = await store.create('access', 'refresh')

    await store.delete(session.sid)

    assert await store.get(session.sid) is None


@pytest.mark.asyncio
async def test_broken_record_is_discarded_instead_of_crashing(store: SessionStore, fake_redis):
    """Мусор в Redis (чужой ключ, обрезанный JSON) не должен ронять страницу."""
    fake_redis.items[f'{SESSION_KEY_PREFIX}garbage'] = '{not json'

    assert await store.get('garbage') is None
    assert f'{SESSION_KEY_PREFIX}garbage' not in fake_redis.items, 'битую запись выбрасываем'


@pytest.mark.asyncio
async def test_record_without_tokens_is_discarded(store: SessionStore, fake_redis):
    fake_redis.items[f'{SESSION_KEY_PREFIX}partial'] = json.dumps({'user': {'email': 'x@y.z'}})

    assert await store.get('partial') is None


@pytest.mark.asyncio
async def test_session_expires_with_the_refresh_token(store: SessionStore, fake_redis):
    """Держать сессию дольше refresh-токена нечем: он и есть её срок жизни."""
    session = await store.create('access', 'refresh')

    assert fake_redis.expires[f'{SESSION_KEY_PREFIX}{session.sid}'] == 3600


@pytest.mark.asyncio
async def test_saving_updates_tokens_in_place(store: SessionStore):
    session = await store.create('old-access', 'old-refresh')

    session.access_token = 'new-access'
    await store.save(session)

    loaded = await store.get(session.sid)
    assert loaded is not None
    assert loaded.access_token == 'new-access'
    assert loaded.sid == session.sid, 'обновление токена не должно менять идентификатор'
