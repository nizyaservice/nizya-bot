"""Цена продления в уведомлении об истёкшей подписке (expired_1d).

Уведомление обещало продление за ``PRICE_30_DAYS`` из .env: промогруппы
пользователя не грузились eager, pricing_engine падал на ленивой загрузке
(MissingGreenlet), и срабатывал env-фолбэк. В тарифном режиме это чужая цена —
у нас в прайсе осталось 1000 коп. (10 ₽), а тариф стоит своё.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.sql import Select

from app.config import settings
from app.services.monitoring_service import MonitoringService
from app.services.notification_settings_service import NotificationSettingsService
from app.services.pricing_engine import pricing_engine


TARIFF_PERIOD_PRICES = {'30': 29900, '90': 79900}


def _service() -> MonitoringService:
    service = MonitoringService.__new__(MonitoringService)
    service.bot = SimpleNamespace(send_message=AsyncMock())
    service._notified_users = set()
    return service


def _tariff():
    """Тариф с ценами по периодам — ровно те методы, что читает уведомление."""
    tariff = SimpleNamespace(id=5, name='Nizya Pro', is_daily=False, daily_price_kopeks=0)
    tariff.get_shortest_period = lambda: 30
    tariff.get_price_for_period = lambda days: TARIFF_PERIOD_PRICES.get(str(days))
    tariff.get_purchasable_price_for_period = lambda days: TARIFF_PERIOD_PRICES.get(str(days))
    return tariff


def _subscription(tariff=None):
    return SimpleNamespace(
        id=11,
        user_id=42,
        tariff=tariff,
        tariff_id=getattr(tariff, 'id', None),
        device_limit=1,
        end_date=datetime.now(UTC) - timedelta(days=1),
    )


def _user():
    return SimpleNamespace(id=42, telegram_id=1234, language='ru', notification_settings={})


async def test_price_falls_back_to_tariff_when_pricing_engine_fails(monkeypatch):
    """Расчёт сорвался — в сообщении цена тарифа, а не PRICE_30_DAYS из .env."""
    monkeypatch.setattr(settings, 'PRICE_30_DAYS', 1000)
    monkeypatch.setattr(
        pricing_engine,
        'calculate_renewal_price',
        AsyncMock(side_effect=RuntimeError('greenlet_spawn has not been called')),
    )

    service = _service()
    service._send_message_with_logo = AsyncMock()

    sent = await service._send_expired_day1_notification(MagicMock(), _user(), _subscription(_tariff()))

    assert sent is True
    text = service._send_message_with_logo.await_args.kwargs['text']
    assert settings.format_price(29900) in text
    assert settings.format_price(1000) not in text


async def test_price_retried_without_discounts_before_tariff_fallback(monkeypatch):
    """Первый расчёт (со скидками) упал — считаем без скидок, а не по .env."""
    calls: list[object] = []

    async def fake_calculate(db, subscription, period_days, *, user=None):
        calls.append(user)
        if user is not None:
            raise RuntimeError('greenlet_spawn has not been called')
        return SimpleNamespace(final_total=29900)

    monkeypatch.setattr(pricing_engine, 'calculate_renewal_price', fake_calculate)

    service = _service()
    service._send_message_with_logo = AsyncMock()

    await service._send_expired_day1_notification(MagicMock(), _user(), _subscription(_tariff()))

    assert len(calls) == 2 and calls[1] is None
    assert settings.format_price(29900) in service._send_message_with_logo.await_args.kwargs['text']


async def test_classic_subscription_keeps_env_price_fallback(monkeypatch):
    """Подписке без тарифа классический прайс и адресован — фолбэк сохраняем."""
    monkeypatch.setattr(settings, 'PRICE_30_DAYS', 1000)
    monkeypatch.setattr(
        pricing_engine,
        'calculate_renewal_price',
        AsyncMock(side_effect=RuntimeError('boom')),
    )

    service = _service()
    service._send_message_with_logo = AsyncMock()

    await service._send_expired_day1_notification(MagicMock(), _user(), _subscription(None))

    assert settings.format_price(1000) in service._send_message_with_logo.await_args.kwargs['text']


async def test_followups_query_eager_loads_promo_groups(monkeypatch):
    """Запрос грузит промогруппы — иначе pricing_engine падает на ленивой загрузке."""
    for name in (
        'are_notifications_globally_enabled',
        'is_expired_1d_enabled',
        'is_second_wave_enabled',
        'is_third_wave_enabled',
    ):
        monkeypatch.setattr(NotificationSettingsService, name, classmethod(lambda cls: True))

    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)

    service = _service()
    service._log_monitoring_event = AsyncMock()

    await service._check_expired_subscription_followups(db)

    stmt = db.execute.await_args.args[0]
    assert isinstance(stmt, Select)
    loaded_attrs: set[str] = set()
    for opt in getattr(stmt, '_with_options', ()):
        for element in getattr(opt, 'path', None) or ():
            key = getattr(element, 'key', None)
            if isinstance(key, str):
                loaded_attrs.add(key)
    assert {'user', 'tariff', 'promo_group', 'user_promo_groups'} <= loaded_attrs


@pytest.mark.parametrize('period_days', [30, 90])
async def test_tariff_fallback_matches_requested_period(monkeypatch, period_days):
    """Фолбэк берёт цену запрошенного периода, а не первого попавшегося."""
    monkeypatch.setattr(
        pricing_engine,
        'calculate_renewal_price',
        AsyncMock(side_effect=RuntimeError('boom')),
    )

    price = await _service()._resolve_renewal_price_for_notification(
        MagicMock(), _user(), _subscription(_tariff()), period_days
    )

    assert price == TARIFF_PERIOD_PRICES[str(period_days)]
