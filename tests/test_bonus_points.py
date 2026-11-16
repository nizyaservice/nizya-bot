"""Бонусные баллы: второй кошелёк, который тратится раньше денег.

Правила фичи (задал владелец):

* баллы можно тратить наравне с деньгами, 1 балл = 1 ₽ (храним в копейках);
* при любой оплате с баланса сначала уходят все доступные баллы, потом деньги;
* баллы видны в главном меню над балансом;
* начисляет их оператор в админке так же, как деньги.

Главное, что тут закреплено: списание «баллы вперёд» живёт в одной точке
(``subtract_user_balance``), а проверки «хватает ли» в путях оплаты считают по
сумме обоих кошельков — иначе человек с баллами и нулём денег видел бы
«недостаточно средств» там, где ему хватает.
"""

from __future__ import annotations

import pathlib
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database.models import User
from app.utils.formatters import format_bonus_points


REPO = pathlib.Path(__file__).resolve().parents[1]


def _user(balance_kopeks: int = 0, bonus_points_kopeks: int = 0) -> User:
    user = User()
    user.balance_kopeks = balance_kopeks
    user.bonus_points_kopeks = bonus_points_kopeks
    return user


# ─── кошелёк ───


def test_available_balance_sums_both_wallets():
    assert _user(10000, 5000).available_balance_kopeks == 15000


def test_available_balance_tolerates_empty_columns():
    """У строк, созданных до миграции, баллы приезжают как None."""
    user = _user(10000)
    user.bonus_points_kopeks = None

    assert user.available_balance_kopeks == 10000
    assert user.bonus_points_rubles == 0


def test_points_are_spent_before_money():
    user = _user(balance_kopeks=10000, bonus_points_kopeks=5000)

    assert user.subtract_balance(7000) is True
    assert user.bonus_points_kopeks == 0
    assert user.balance_kopeks == 8000, 'деньгами должно уйти только то, что не закрыли баллами'


def test_points_alone_cover_a_purchase():
    user = _user(balance_kopeks=0, bonus_points_kopeks=30000)

    assert user.subtract_balance(30000) is True
    assert user.bonus_points_kopeks == 0
    assert user.balance_kopeks == 0


def test_money_is_untouched_while_points_suffice():
    user = _user(balance_kopeks=10000, bonus_points_kopeks=10000)

    assert user.subtract_balance(4000) is True
    assert user.bonus_points_kopeks == 6000
    assert user.balance_kopeks == 10000


def test_insufficient_total_changes_nothing():
    user = _user(balance_kopeks=1000, bonus_points_kopeks=2000)

    assert user.subtract_balance(5000) is False
    assert user.balance_kopeks == 1000
    assert user.bonus_points_kopeks == 2000


def test_add_bonus_points_accumulates():
    user = _user()
    user.bonus_points_kopeks = None

    user.add_bonus_points(5000)
    user.add_bonus_points(2500)

    assert user.bonus_points_kopeks == 7500


# ─── формат ───


@pytest.mark.parametrize(
    ('kopeks', 'expected'),
    [(0, '0'), (None, '0'), (50000, '500'), (100, '1'), (1250, '12.5'), (1234, '12.34')],
)
def test_format_bonus_points(kopeks, expected):
    assert format_bonus_points(kopeks) == expected


# ─── списание с баланса ───


@pytest.mark.asyncio
async def test_subtract_user_balance_takes_points_first():
    from app.database.crud import user as user_crud

    stored = _user(balance_kopeks=10000, bonus_points_kopeks=4000)
    stored.id = 1

    db = AsyncMock()
    result = MagicMock()
    result.scalar_one = MagicMock(return_value=stored)
    db.execute = AsyncMock(return_value=result)

    created = AsyncMock()
    with patch('app.database.crud.transaction.create_transaction', new=created):
        ok = await user_crud.subtract_user_balance(db, stored, 6000, 'Оплата тарифа', create_transaction=True)

    assert ok is True
    assert stored.bonus_points_kopeks == 0
    assert stored.balance_kopeks == 8000
    # Транзакция — на полную цену, а доля баллов названа в описании: иначе по
    # истории не понять, почему с баланса ушло меньше, чем стоила покупка.
    assert created.await_args.kwargs['amount_kopeks'] == 6000
    assert 'баллами' in created.await_args.kwargs['description']


@pytest.mark.asyncio
async def test_subtract_user_balance_refuses_when_both_wallets_are_short():
    from app.database.crud import user as user_crud

    stored = _user(balance_kopeks=1000, bonus_points_kopeks=1000)
    stored.id = 1

    db = AsyncMock()
    result = MagicMock()
    result.scalar_one = MagicMock(return_value=stored)
    db.execute = AsyncMock(return_value=result)

    ok = await user_crud.subtract_user_balance(db, stored, 5000, 'Оплата тарифа')

    assert ok is False
    assert stored.balance_kopeks == 1000
    assert stored.bonus_points_kopeks == 1000


@pytest.mark.asyncio
async def test_money_only_purchase_keeps_the_old_shape():
    """Контрольный кейс: без баллов списание ведёт себя как до фичи."""
    from app.database.crud import user as user_crud

    stored = _user(balance_kopeks=10000)
    stored.id = 1

    db = AsyncMock()
    result = MagicMock()
    result.scalar_one = MagicMock(return_value=stored)
    db.execute = AsyncMock(return_value=result)

    created = AsyncMock()
    with patch('app.database.crud.transaction.create_transaction', new=created):
        ok = await user_crud.subtract_user_balance(db, stored, 6000, 'Оплата тарифа', create_transaction=True)

    assert ok is True
    assert stored.balance_kopeks == 4000
    assert created.await_args.kwargs['description'] == 'Оплата тарифа'


# ─── начисление баллов ───


@pytest.mark.asyncio
async def test_change_user_bonus_points_adds_and_removes():
    from app.database.crud import user as user_crud

    stored = _user(bonus_points_kopeks=5000)
    stored.id = 1

    db = AsyncMock()
    result = MagicMock()
    result.scalar_one = MagicMock(return_value=stored)
    db.execute = AsyncMock(return_value=result)

    assert await user_crud.change_user_bonus_points(db, stored, 2500) is True
    assert stored.bonus_points_kopeks == 7500

    assert await user_crud.change_user_bonus_points(db, stored, -7500) is True
    assert stored.bonus_points_kopeks == 0


@pytest.mark.asyncio
async def test_change_user_bonus_points_refuses_to_go_negative():
    from app.database.crud import user as user_crud

    stored = _user(bonus_points_kopeks=1000)
    stored.id = 1

    db = AsyncMock()
    result = MagicMock()
    result.scalar_one = MagicMock(return_value=stored)
    db.execute = AsyncMock(return_value=result)

    assert await user_crud.change_user_bonus_points(db, stored, -2000) is False
    assert stored.bonus_points_kopeks == 1000


@pytest.mark.asyncio
async def test_crediting_points_writes_no_money_transaction():
    """transactions кормит финансовые отчёты: баллы там выглядели бы выручкой."""
    from app.database.crud import user as user_crud

    stored = _user()
    stored.id = 1

    db = AsyncMock()
    result = MagicMock()
    result.scalar_one = MagicMock(return_value=stored)
    db.execute = AsyncMock(return_value=result)

    created = AsyncMock()
    with patch('app.database.crud.transaction.create_transaction', new=created):
        await user_crud.change_user_bonus_points(db, stored, 50000)

    created.assert_not_awaited()


# ─── главное меню ───


def test_rich_menu_shows_points_above_the_balance():
    source = (REPO / 'app' / 'utils' / 'rich_menu.py').read_text(encoding='utf-8')

    points_at = source.index('MAIN_MENU_RICH_BONUS_POINTS')
    balance_at = source.index('MAIN_MENU_RICH_BALANCE')
    assert points_at < balance_at, 'баллы должны уходить в полотно раньше баланса'


def test_live_menu_fingerprint_includes_points():
    """Иначе живое меню не перерисуется после начисления баллов."""
    source = (REPO / 'app' / 'utils' / 'rich_menu.py').read_text(encoding='utf-8')
    start = source.index('def live_menu_fingerprint')
    end = source.index('\ndef ', start + 1)

    assert 'bonus_points_kopeks' in source[start:end]


# ─── проверки «хватает ли средств» ───


PAYMENT_PATHS = (
    'app/handlers/simple_subscription.py',
    'app/handlers/subscription/traffic.py',
    'app/handlers/subscription/addon_cart.py',
    'app/handlers/subscription/devices.py',
    'app/handlers/subscription/countries.py',
    'app/handlers/subscription/purchase.py',
    'app/handlers/subscription/gift.py',
    'app/handlers/balance/main.py',
    'app/handlers/router_link.py',
    'app/services/daily_subscription_service.py',
    'app/services/subscription_auto_purchase_service.py',
    'app/services/subscription_purchase_service.py',
    'app/services/gift_purchase_service.py',
    'app/services/router_link_service.py',
)

_MONEY_ONLY_COMPARISON = re.compile(r'(?<!available_)\bbalance_kopeks\s*[<>]')


@pytest.mark.parametrize('path', PAYMENT_PATHS)
def test_payment_paths_compare_against_both_wallets(path):
    """Сравнение с одними деньгами показало бы «недостаточно» тому, у кого есть баллы."""
    offenders = [
        f'{index}: {line.strip()}'
        for index, line in enumerate((REPO / path).read_text(encoding='utf-8').split('\n'), 1)
        if _MONEY_ONLY_COMPARISON.search(line)
    ]

    assert not offenders, offenders


def test_payouts_still_count_money_only():
    """Баллы наружу не выводятся: реферальная выплата смотрит только на деньги."""
    source = (REPO / 'app' / 'services' / 'referral_withdrawal_service.py').read_text(encoding='utf-8')

    assert 'user.balance_kopeks < request.amount_kopeks' in source
    assert 'available_balance_kopeks' not in source


def test_admin_credits_points_like_money():
    """Кнопка «🎁 Баллы» рядом с «💰 Баланс» и свой шаг ввода суммы."""
    keyboards = (REPO / 'app' / 'keyboards' / 'admin.py').read_text(encoding='utf-8')
    handlers = (REPO / 'app' / 'handlers' / 'admin' / 'users.py').read_text(encoding='utf-8')

    assert 'admin_user_points_' in keyboards
    assert "F.data.startswith('admin_user_points_')" in handlers
    assert 'AdminStates.editing_user_bonus_points' in handlers
