"""Слияние аккаунтов: остаток второй подписки не пропадает.

Правило владельца: сроки складываются, а при разных тарифах остаток
пересчитывается по стоимости — сколько времени эти деньги купили бы на тарифе,
который человек выбрал оставить. До этого подписка-проигравшая удалялась
целиком, и оплаченное время сгорало.

Дневная ставка берётся той же функцией, что при смене тарифа
(``get_tariff_daily_rate_fraction``), иначе два флоу оценивали бы один и тот же
остаток по-разному.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.account_merge_service import _absorb_remaining_days
from app.services.pricing_engine import PricingEngine


SERVICE_SOURCE = pathlib.Path('app/services/account_merge_service.py').read_text(encoding='utf-8')


def _tariff(price_kopeks: int, period_days: int = 30, tariff_id: int = 1) -> SimpleNamespace:
    """Тариф с одним периодом: ставка считается по кратчайшему."""
    return SimpleNamespace(
        id=tariff_id,
        is_daily=False,
        daily_price_kopeks=0,
        period_prices={str(period_days): price_kopeks},
        get_available_periods=lambda: [period_days],
        get_price_for_period=lambda days, _p=price_kopeks, _d=period_days: _p if days == _d else None,
    )


# ─── пересчёт дней ───


def test_same_price_means_terms_add_up():
    same = _tariff(30000)

    assert PricingEngine.convert_remaining_days(same, same, 10) == 10


def test_cheaper_tariff_gives_more_days():
    """Остаток дорогого тарифа на дешёвом живёт дольше — деньги те же."""
    expensive = _tariff(60000)
    cheap = _tariff(30000)

    assert PricingEngine.convert_remaining_days(expensive, cheap, 10) == 20


def test_pricier_tariff_gives_fewer_days():
    cheap = _tariff(30000)
    expensive = _tariff(60000)

    assert PricingEngine.convert_remaining_days(cheap, expensive, 10) == 5


def test_different_period_lengths_are_compared_by_daily_rate():
    """Тарифы с разными наборами периодов сравниваются по дневной ставке."""
    month = _tariff(30000, period_days=30)  # 1000 коп./день
    week = _tariff(14000, period_days=7)  # 2000 коп./день

    assert PricingEngine.convert_remaining_days(week, month, 10) == 20
    assert PricingEngine.convert_remaining_days(month, week, 10) == 5


def test_fractional_day_is_rounded_down():
    """Неполный день не выдаём: в подписке дробных дней нет."""
    source = _tariff(29900)
    target = _tariff(40000)

    # 7 * 29900 / 40000 = 5.2325
    assert PricingEngine.convert_remaining_days(source, target, 7) == 5


def test_nothing_to_convert():
    tariff = _tariff(30000)

    assert PricingEngine.convert_remaining_days(tariff, tariff, 0) == 0
    assert PricingEngine.convert_remaining_days(tariff, tariff, -5) == 0


def test_free_target_keeps_the_days():
    """На бесплатный тариф делить нечем — переносим остаток как есть."""
    paid = _tariff(30000)
    free = _tariff(0)

    assert PricingEngine.convert_remaining_days(paid, free, 10) == 10


def test_remainder_of_a_free_tariff_is_worth_nothing():
    free = _tariff(0)
    paid = _tariff(30000)

    assert PricingEngine.convert_remaining_days(free, paid, 10) == 0


def test_subscription_without_a_tariff_keeps_its_days():
    """Классический режим продаж: тарифа у подписки нет, пересчитывать не по чему."""
    paid = _tariff(30000)

    assert PricingEngine.convert_remaining_days(None, paid, 10) == 10
    assert PricingEngine.convert_remaining_days(paid, None, 10) == 10


# ─── перенос в подписку ───


class _Db:
    """Двойник сессии: отдаёт тарифы по id. Остальное делает CRUD-продление."""

    def __init__(self, tariffs: dict[int, SimpleNamespace]) -> None:
        self.tariffs = tariffs

    async def get(self, _model, tariff_id):
        return self.tariffs.get(tariff_id)


def _subscription(tariff_id: int, end_date: datetime | None, sub_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(id=sub_id, tariff_id=tariff_id, end_date=end_date)


@pytest.fixture
def extend(monkeypatch):
    """Продление подменяем: дата, грейс и трафик — забота CRUD, у него свои тесты."""
    double = AsyncMock()
    monkeypatch.setattr('app.database.crud.subscription.extend_subscription', double)
    return double


@pytest.mark.asyncio
async def test_days_are_handed_to_the_common_extender(extend):
    """Дни добавляет общий CRUD: он же вернёт оверлей грейса и правило трафика."""
    now = datetime.now(UTC)
    db = _Db({1: _tariff(30000, tariff_id=1), 2: _tariff(60000, tariff_id=2)})
    keep = _subscription(1, now + timedelta(days=5, minutes=1), sub_id=10)
    drop = _subscription(2, now + timedelta(days=10, minutes=1), sub_id=11)

    added = await _absorb_remaining_days(db, keep=keep, drop=drop)

    # Остаток дорогого тарифа (10 дней) на вдвое дешёвом — 20 дней.
    assert added == 20
    extend.assert_awaited_once()
    args, kwargs = extend.await_args
    assert args[1] is keep
    assert args[2] == 20
    assert kwargs['commit'] is False, 'слияние идёт одной транзакцией'
    assert kwargs['reset_used_traffic'] is False, 'слияние добавляет время, а не начинает новый период'


@pytest.mark.asyncio
async def test_expired_dropped_subscription_adds_nothing(extend):
    now = datetime.now(UTC)
    db = _Db({1: _tariff(30000, tariff_id=1), 2: _tariff(30000, tariff_id=2)})
    keep = _subscription(1, now + timedelta(days=5), sub_id=10)
    drop = _subscription(2, now - timedelta(days=1), sub_id=11)

    added = await _absorb_remaining_days(db, keep=keep, drop=drop)

    assert added == 0
    extend.assert_not_awaited()


@pytest.mark.asyncio
async def test_days_that_do_not_buy_a_single_day_change_nothing(extend):
    """Остаток дешёвого тарифа может не дать ни дня дорогого — это не ошибка."""
    now = datetime.now(UTC)
    db = _Db({1: _tariff(100000, tariff_id=1), 2: _tariff(1000, tariff_id=2)})
    keep = _subscription(1, now + timedelta(days=5), sub_id=10)
    drop = _subscription(2, now + timedelta(days=2, minutes=1), sub_id=11)

    added = await _absorb_remaining_days(db, keep=keep, drop=drop)

    assert added == 0
    extend.assert_not_awaited()


@pytest.mark.asyncio
async def test_panel_failure_does_not_break_the_merge(monkeypatch):
    """Расхождение с панелью лечится ресинком, откат оставил бы два аккаунта."""
    from app.services import account_merge_service as svc

    broken = AsyncMock(side_effect=RuntimeError('panel down'))
    monkeypatch.setattr('app.services.subscription_service.SubscriptionService.update_remnawave_user', broken)

    await svc._push_end_date_to_panel(AsyncMock(), _subscription(1, datetime.now(UTC)))

    broken.assert_awaited()


# ─── сторожа: правило применено во всех ветках слияния ───


def test_every_branch_that_drops_a_subscription_absorbs_its_days():
    """Три места, где подписка перестаёт быть живой: все обязаны забрать остаток."""
    assert SERVICE_SOURCE.count('_absorb_remaining_days(') == 5, (
        'определение, две ветки одиночного режима и две ветки конфликта в мультитарифе'
    )


def test_new_end_date_is_pushed_to_the_panel():
    """Без этого бот считал бы подписку живой дольше, чем Remnawave."""
    assert SERVICE_SOURCE.count('_push_end_date_to_panel(') == 4


def test_absorb_happens_before_the_subscription_is_deleted():
    """После delete у подписки не прочитать ни срок, ни тариф."""
    absorb = SERVICE_SOURCE.index('_absorb_remaining_days(db, keep=secondary_sub, drop=primary_sub)')
    delete = SERVICE_SOURCE.index('await db.delete(primary_sub)')

    assert absorb < delete
