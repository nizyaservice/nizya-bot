"""Двойники для тестов портала: Redis в памяти и приложение с подменами."""

from __future__ import annotations

from typing import Any

import pytest

from portal.api import CabinetApi, build_client
from portal.config import get_settings
from portal.deps import get_api, get_store
from portal.main import create_app
from portal.sessions import SessionStore


class FakeRedis:
    """Минимум из redis-py, который нужен хранилищу сессий."""

    def __init__(self) -> None:
        self.items: dict[str, str] = {}
        self.expires: dict[str, int | None] = {}

    async def get(self, key: str) -> str | None:
        return self.items.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.items[key] = value
        self.expires[key] = ex
        return True

    async def delete(self, key: str) -> int:
        self.expires.pop(key, None)
        return 1 if self.items.pop(key, None) is not None else 0


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def store(fake_redis: FakeRedis) -> SessionStore:
    return SessionStore(fake_redis, ttl_seconds=3600)


@pytest.fixture
def portal_app(store: SessionStore, monkeypatch):
    """Приложение портала с подменёнными хранилищем и клиентом API.

    Клиент API подставляет каждый тест сам — через ``app.state.api_double``.

    Secure-cookie выключаем: TestClient ходит по http, а cookie с флагом
    Secure браузерный клиент по http не сохраняет — тесты входа не увидели бы
    сессию. На проде флаг включён (PORTAL_COOKIE_SECURE=true).
    """
    monkeypatch.setenv('PORTAL_COOKIE_SECURE', 'false')
    get_settings.cache_clear()

    app = create_app()

    def _store() -> SessionStore:
        return store

    def _api() -> CabinetApi:
        return app.state.api_double

    app.dependency_overrides[get_store] = _store
    app.dependency_overrides[get_api] = _api
    app.state.api_double = CabinetApi(build_client('http://bot.invalid', 1.0))

    yield app

    # Настройки кэшируются на процесс: оставить подменённые — значит сломать
    # соседние тесты, которые ждут прод-значения.
    get_settings.cache_clear()


def api_from_handler(handler: Any) -> CabinetApi:
    """Клиент к боту, отвечающий переданной функцией (httpx.MockTransport)."""
    import httpx

    transport = httpx.MockTransport(handler)
    return CabinetApi(httpx.AsyncClient(base_url='http://bot.invalid', transport=transport))
