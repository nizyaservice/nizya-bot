"""Промокод на бонусные баллы: оператор выдаёт код, человек получает баллы.

Баллы — второй кошелёк: они тратятся раньше денег и наружу не выводятся,
поэтому начисление идёт той же ручкой, что у оператора в админке
(``change_user_bonus_points``), а не через баланс. Денежная транзакция тут не
создаётся намеренно — баллы в истории платежей исказили бы отчёты.

Сумма лежит в ``balance_bonus_kopeks``: балл равен рублю в копейках, так что
единица та же, а отличает тип. Поэтому здесь закреплено и то, что
балансовый промокод баллов не трогает, и наоборот.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import PromoCodeType
from app.services.promocode_service import PromoCodeService


def _promocode(**overrides) -> SimpleNamespace:
    base = {
        'id': 7,
        'code': 'POINTS500',
        'type': PromoCodeType.BONUS_POINTS.value,
        'balance_bonus_kopeks': 50000,  # 500 баллов
        'subscription_days': 0,
        'traffic_gb': 0,
        'tariff_id': None,
        'promo_group_id': None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _service(monkeypatch) -> PromoCodeService:
    monkeypatch.setattr('app.services.promocode_service.RemnaWaveService', MagicMock())
    stub = SimpleNamespace(update_remnawave_user=AsyncMock(), enable_remnawave_user=AsyncMock())
    monkeypatch.setattr('app.services.promocode_service.SubscriptionService', lambda: stub)
    return PromoCodeService()


def _user() -> SimpleNamespace:
    return SimpleNamespace(id=1, telegram_id=100, email=None, balance_kopeks=0, bonus_points_kopeks=0, language='ru')


@pytest.fixture
def credit(monkeypatch) -> AsyncMock:
    """Ручка начисления баллов — та же, что у оператора в админке."""
    double = AsyncMock(return_value=True)
    monkeypatch.setattr('app.database.crud.user.change_user_bonus_points', double)
    return double


@pytest.fixture
def add_balance(monkeypatch) -> AsyncMock:
    double = AsyncMock()
    monkeypatch.setattr('app.services.promocode_service.add_user_balance', double)
    return double


async def test_points_are_credited_to_the_second_wallet(monkeypatch, credit, add_balance):
    service = _service(monkeypatch)
    user = _user()

    description = await service._apply_promocode_effects(AsyncMock(), user, _promocode())

    credit.assert_awaited_once()
    args, kwargs = credit.await_args
    assert args[2] == 50000, 'сумма промокода в копейках = количество баллов'
    assert kwargs['commit'] is False, 'активация коммитится одной транзакцией'
    # Деньги промокодом на баллы не начисляются.
    add_balance.assert_not_awaited()
    assert '500' in description, 'человек должен увидеть, сколько баллов получил'


async def test_zero_amount_credits_nothing(monkeypatch, credit):
    """Код без суммы — не повод трогать кошелёк."""
    service = _service(monkeypatch)

    await service._apply_promocode_effects(AsyncMock(), _user(), _promocode(balance_bonus_kopeks=0))

    credit.assert_not_awaited()


async def test_balance_promocode_does_not_touch_points(monkeypatch, credit, add_balance):
    """Соседний тип с той же колонкой: деньги остаются деньгами."""
    service = _service(monkeypatch)
    promocode = _promocode(type=PromoCodeType.BALANCE.value, code='MONEY500')

    await service._apply_promocode_effects(AsyncMock(), _user(), promocode)

    add_balance.assert_awaited_once()
    credit.assert_not_awaited()


async def test_points_promocode_does_not_touch_money(monkeypatch, credit, add_balance):
    service = _service(monkeypatch)

    await service._apply_promocode_effects(AsyncMock(), _user(), _promocode())

    add_balance.assert_not_awaited()


# ─── выдача оператором ───


def test_admin_can_issue_a_points_promocode():
    """Тип доступен в боте: кнопка, подсказка ввода и сопоставление с типом."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    keyboards = (repo / 'app' / 'keyboards' / 'admin.py').read_text(encoding='utf-8')
    handlers = (repo / 'app' / 'handlers' / 'admin' / 'promocodes.py').read_text(encoding='utf-8')

    assert "callback_data='promo_type_points'" in keyboards
    assert "'points': PromoCodeType.BONUS_POINTS," in handlers
    assert 'Введите количество бонусных баллов' in handlers
    assert "balance_bonus_kopeks = value * 100 if promo_type in ('balance', 'points') else 0" in handlers


def test_cabinet_refuses_a_points_promocode_without_amount():
    """Промокод на ноль баллов ничего не даёт — в кабинете такой не создать."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    source = (repo / 'app' / 'cabinet' / 'routes' / 'admin_promocodes.py').read_text(encoding='utf-8')

    assert 'PromoCodeType.BONUS_POINTS and payload.balance_bonus_kopeks <= 0' in source
