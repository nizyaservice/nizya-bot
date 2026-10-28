"""Симуляция: тариф с базовым лимитом 20 устройств.

Оператор задал в тарифе 20, пользователю начисляется 15. Прогон проходит весь
путь значения — тариф в базе, создание подписки, продление, отправка в
RemnaWave — чтобы увидеть, на каком шаге 20 превращается в 15.
"""

from datetime import UTC, datetime

import pytest

from app.config import settings
from app.database.crud.subscription import create_paid_subscription, extend_subscription
from app.database.crud.tariff import create_tariff, get_tariff_by_id, update_tariff
from app.database.models import Base, PromoGroup, Subscription, Tariff, User
from app.utils.subscription_utils import resolve_hwid_device_limit
from tests.fixtures.sqlite_memory import memory_session


# tariff_promo_groups подтягивается при чтении тарифа — без этих таблиц
# selectinload падает на пустой базе.
TABLES = (
    User.__table__,
    Tariff.__table__,
    Subscription.__table__,
    PromoGroup.__table__,
    Base.metadata.tables['tariff_promo_groups'],
    Base.metadata.tables['grace_access_sessions'],
    Base.metadata.tables['traffic_purchases'],
    Base.metadata.tables['server_squads'],
    Base.metadata.tables['sent_notifications'],
)

BASE_DEVICES = 20


async def _user(db) -> User:
    user = User(telegram_id=555, first_name='Тест', language='ru', balance_kopeks=0)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _tariff(db, **overrides) -> Tariff:
    values = {
        'name': 'Тариф на 20 устройств',
        'description': '',
        'traffic_limit_gb': 100,
        'device_limit': BASE_DEVICES,
        'period_prices': {30: 10000},
        'allowed_squads': [],
    }
    values.update(overrides)
    return await create_tariff(db, **values)


async def test_tariff_keeps_the_base_device_limit(monkeypatch) -> None:
    """Шаг 1: значение доживает до базы и читается обратно."""
    async with memory_session(monkeypatch, TABLES) as db:
        tariff = await _tariff(db)
        reloaded = await get_tariff_by_id(db, tariff.id)

        assert reloaded.device_limit == BASE_DEVICES


async def test_editing_the_limit_is_not_capped(monkeypatch) -> None:
    """Шаг 2: правка лимита в админке не упирается в MAX_DEVICES_LIMIT."""
    monkeypatch.setattr(settings, 'MAX_DEVICES_LIMIT', 15)
    async with memory_session(monkeypatch, TABLES) as db:
        tariff = await _tariff(db, device_limit=1)

        tariff = await update_tariff(db, tariff, device_limit=BASE_DEVICES)

        assert tariff.device_limit == BASE_DEVICES


@pytest.mark.parametrize('max_devices_limit', [0, 15, 20, 50])
async def test_purchase_grants_the_tariff_limit(monkeypatch, max_devices_limit) -> None:
    """Шаг 3: покупка выдаёт базовый лимит тарифа при любом MAX_DEVICES_LIMIT.

    MAX_DEVICES_LIMIT ограничивает ДОКУПКУ устройств сверх тарифа, а не сам
    тариф: оператор, задавший в тарифе 20, должен получить ровно 20.
    """
    monkeypatch.setattr(settings, 'MAX_DEVICES_LIMIT', max_devices_limit)
    async with memory_session(monkeypatch, TABLES) as db:
        user = await _user(db)
        tariff = await _tariff(db)

        subscription = await create_paid_subscription(
            db,
            user_id=user.id,
            duration_days=30,
            traffic_limit_gb=tariff.traffic_limit_gb,
            device_limit=tariff.device_limit,
            connected_squads=[],
            tariff_id=tariff.id,
        )

        assert subscription.device_limit == BASE_DEVICES, (
            f'при MAX_DEVICES_LIMIT={max_devices_limit} выдано {subscription.device_limit} вместо {BASE_DEVICES}'
        )


async def test_extension_keeps_the_limit(monkeypatch) -> None:
    """Шаг 4: продление тем же тарифом лимит не срезает."""
    monkeypatch.setattr(settings, 'MAX_DEVICES_LIMIT', 15)
    async with memory_session(monkeypatch, TABLES) as db:
        user = await _user(db)
        tariff = await _tariff(db)
        subscription = await create_paid_subscription(
            db,
            user_id=user.id,
            duration_days=30,
            traffic_limit_gb=tariff.traffic_limit_gb,
            device_limit=tariff.device_limit,
            connected_squads=[],
            tariff_id=tariff.id,
        )

        extended = await extend_subscription(
            db,
            subscription,
            days=30,
            tariff_id=tariff.id,
            traffic_limit_gb=tariff.traffic_limit_gb,
            device_limit=tariff.device_limit,
            connected_squads=[],
        )

        assert extended.device_limit == BASE_DEVICES


def test_panel_payload_carries_the_full_limit(monkeypatch) -> None:
    """Шаг 5: в RemnaWave уходит тот же лимит, что записан в подписке."""
    monkeypatch.setattr(settings, 'MAX_DEVICES_LIMIT', 15)
    subscription = Subscription(
        user_id=1,
        status='active',
        device_limit=BASE_DEVICES,
        traffic_limit_gb=100,
        start_date=datetime.now(UTC),
        end_date=datetime.now(UTC),
    )

    assert resolve_hwid_device_limit(subscription) == BASE_DEVICES


# ---- найденная причина: админский редактор устройств ----


async def test_admin_editor_ceiling_follows_the_tariff(monkeypatch) -> None:
    """Потолок админского редактора — не ниже базы тарифа.

    Это и был баг: MAX_DEVICES_LIMIT=15 ограничивал редактор, и подписке с
    тарифом на 20 устройств нельзя было выставить больше 15 — ни кнопкой, ни
    числом. Настройка ограничивает ДОКУПКУ сверх тарифа, а не сам тариф.
    """
    from unittest.mock import AsyncMock

    import app.handlers.admin.users as admin_users

    monkeypatch.setattr(settings, 'MAX_DEVICES_LIMIT', 15)
    async with memory_session(monkeypatch, TABLES) as db:
        user = await _user(db)
        tariff = await _tariff(db)
        subscription = await create_paid_subscription(
            db,
            user_id=user.id,
            duration_days=30,
            traffic_limit_gb=tariff.traffic_limit_gb,
            device_limit=tariff.device_limit,
            connected_squads=[],
            tariff_id=tariff.id,
        )
        monkeypatch.setattr(admin_users, '_resolve_admin_subscription', AsyncMock(return_value=subscription))

        ceiling = await admin_users._resolve_device_limit_ceiling(db, user.id, subscription.id)

        assert ceiling == BASE_DEVICES, f'админ не сможет выставить больше {ceiling}'


async def test_admin_editor_respects_the_tariff_own_cap(monkeypatch) -> None:
    """Явный max_device_limit тарифа главнее: он и есть потолок докупки."""
    from unittest.mock import AsyncMock

    import app.handlers.admin.users as admin_users

    monkeypatch.setattr(settings, 'MAX_DEVICES_LIMIT', 15)
    async with memory_session(monkeypatch, TABLES) as db:
        user = await _user(db)
        tariff = await _tariff(db, max_device_limit=25)
        subscription = await create_paid_subscription(
            db,
            user_id=user.id,
            duration_days=30,
            traffic_limit_gb=tariff.traffic_limit_gb,
            device_limit=tariff.device_limit,
            connected_squads=[],
            tariff_id=tariff.id,
        )
        monkeypatch.setattr(admin_users, '_resolve_admin_subscription', AsyncMock(return_value=subscription))

        assert await admin_users._resolve_device_limit_ceiling(db, user.id, subscription.id) == 25


async def test_admin_editor_keeps_unlimited_meaning(monkeypatch) -> None:
    """MAX_DEVICES_LIMIT=0 ≡ «без ограничения» — конвенция всей кодовой базы."""
    from unittest.mock import AsyncMock

    import app.handlers.admin.users as admin_users

    monkeypatch.setattr(settings, 'MAX_DEVICES_LIMIT', 0)
    async with memory_session(monkeypatch, TABLES) as db:
        user = await _user(db)
        monkeypatch.setattr(admin_users, '_resolve_admin_subscription', AsyncMock(return_value=None))

        assert await admin_users._resolve_device_limit_ceiling(db, user.id, None) is None
