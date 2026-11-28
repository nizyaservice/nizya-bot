"""Бонусные баллы: второй кошелёк, который тратится раньше денег.

Правила фичи (задал владелец):

* баллы можно тратить наравне с деньгами, 1 балл = 0,5 ₽ (в колонке лежит их
  денежная стоимость в копейках, количество = стоимость / курс);
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
from app.utils.bonus_points import (
    BONUS_POINT_VALUE_KOPEKS,
    format_bonus_points,
    format_bonus_points_with_value,
    kopeks_to_points,
    points_to_kopeks,
)


REPO = pathlib.Path(__file__).resolve().parents[1]


def _user(balance_kopeks: int = 0, bonus_points_kopeks: int = 0) -> User:
    user = User()
    user.balance_kopeks = balance_kopeks
    user.bonus_points_kopeks = bonus_points_kopeks
    return user


# ─── кошелёк ───


def test_available_balance_sums_both_wallets():
    assert _user(10000, 5000).available_balance_kopeks == 15000


def test_the_case_the_tariff_flow_refused():
    """139 ₽ и 80 баллов = ровно 179 ₽: тариф за 179 ₽ оплачивается полностью."""
    user = _user(balance_kopeks=13900, bonus_points_kopeks=points_to_kopeks(80))

    assert user.available_balance_kopeks == 17900
    assert user.subtract_balance(17900)
    assert user.balance_kopeks == 0
    assert user.bonus_points_kopeks == 0


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
    [(0, '0'), (None, '0'), (50000, '1000'), (50, '1'), (625, '12.5'), (617, '12.34')],
)
def test_format_bonus_points(kopeks, expected):
    assert format_bonus_points(kopeks) == expected


# ─── курс балла ───


def test_one_point_costs_half_a_rouble():
    """Решение владельца (26.11.2026): балл оплачивает 50 копеек, не рубль."""
    assert BONUS_POINT_VALUE_KOPEKS == 50
    assert points_to_kopeks(100) == 5000, '100 баллов оплатят 50 ₽'
    assert kopeks_to_points(5000) == 100


def test_odd_points_are_counted_to_the_kopek():
    """49 баллов = 24,5 ₽: половина рубля — целое число копеек, терять нечего."""
    assert points_to_kopeks(49) == 2450
    assert kopeks_to_points(2450) == 49
    assert format_bonus_points_with_value(2450) == '49 (24.5 ₽)'


def test_the_value_is_shown_next_to_the_count():
    """Курс не единица, поэтому одно число человек прочёл бы как рубли.

    И копейки в стоимости не округляем: при PRICE_ROUNDING_ENABLED те самые
    24,5 ₽ показались бы как «24 ₽», то есть меньше, чем человек может
    заплатить.
    """
    assert format_bonus_points_with_value(0) == '0 (0 ₽)'
    assert format_bonus_points_with_value(5000) == '100 (50 ₽)'


def test_the_rate_is_applied_in_one_place():
    """Пересчёт баллов в деньги — только через points_to_kopeks/kopeks_to_points.

    Пока курс был единицей, количество баллов умножали на 100 прямо в
    хендлерах — и смена курса разъехалась бы по экранам: где-то 100 баллов
    стоили бы 50 ₽, где-то 100. Поэтому входы оператора обязаны идти через
    модуль курса.
    """
    entry_points = (
        'app/handlers/admin/users.py',  # начисление оператором
        'app/handlers/admin/promocodes.py',  # промокод на баллы
    )

    for path in entry_points:
        source = (REPO / path).read_text(encoding='utf-8')
        assert 'points_to_kopeks(' in source, f'{path}: курс применяется вручную'

    assert 'amount_points * 100' not in (REPO / 'app' / 'handlers' / 'admin' / 'users.py').read_text(
        encoding='utf-8'
    ), 'баллы снова считаются по старому курсу'


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


# ─── слияние аккаунтов ───


def test_merge_carries_bonus_points_over():
    """Баллы — такой же кошелёк: при слиянии их нельзя терять вместе с аккаунтом."""
    source = (REPO / 'app' / 'services' / 'account_merge_service.py').read_text(encoding='utf-8')

    assert 'primary.bonus_points_kopeks = (primary.bonus_points_kopeks or 0) + transferred_points' in source
    assert 'secondary.bonus_points_kopeks = 0' in source
    # И в превью, по которому человек подтверждает слияние.
    assert "'bonus_points_kopeks': user.bonus_points_kopeks or 0," in source


# ─── главное меню ───


def test_rich_menu_shows_points_above_the_balance():
    source = (REPO / 'app' / 'utils' / 'rich_menu.py').read_text(encoding='utf-8')

    points_at = source.index('MAIN_MENU_RICH_BONUS_POINTS')
    balance_at = source.index('MAIN_MENU_RICH_BALANCE')
    assert points_at < balance_at, 'баллы должны уходить в полотно раньше баланса'


def test_points_line_is_shown_even_at_zero():
    """Решение владельца: строка видна всегда, иначе кошелька будто и нет.

    Проверяются все три места, где она живёт: оба главных меню и «Баланс».
    """
    rich_menu = (REPO / 'app' / 'utils' / 'rich_menu.py').read_text(encoding='utf-8')
    classic_menu = (REPO / 'app' / 'handlers' / 'menu.py').read_text(encoding='utf-8')
    balance_screen = (REPO / 'app' / 'handlers' / 'balance' / 'main.py').read_text(encoding='utf-8')

    for source, key in (
        (rich_menu, 'MAIN_MENU_RICH_BONUS_POINTS'),
        (classic_menu, 'MAIN_MENU_BONUS_POINTS'),
        (balance_screen, 'BALANCE_BONUS_POINTS_LINE'),
    ):
        line_at = source.index(key)
        preceding = source[max(0, line_at - 400) : line_at]
        assert 'points_kopeks > 0' not in preceding, f'{key}: строка снова спрятана при нуле'


def test_live_menu_fingerprint_includes_points():
    """Иначе живое меню не перерисуется после начисления баллов."""
    source = (REPO / 'app' / 'utils' / 'rich_menu.py').read_text(encoding='utf-8')
    start = source.index('def live_menu_fingerprint')
    end = source.index('\ndef ', start + 1)

    assert 'bonus_points_kopeks' in source[start:end]


# ─── проверки «хватает ли средств» ───


PAYMENT_PATHS = (
    'app/handlers/simple_subscription.py',
    'app/handlers/admin/users.py',
    'app/handlers/subscription/traffic.py',
    'app/handlers/subscription/addon_cart.py',
    'app/handlers/subscription/devices.py',
    'app/handlers/subscription/countries.py',
    'app/handlers/subscription/purchase.py',
    # Основной флоу продаж (`SALES_MODE=tariffs` — значение по умолчанию). Он
    # складывал деньги в переменную `user_balance` и сравнивал уже её, поэтому
    # прошёл мимо правила: человеку с 139 ₽ и 80 баллами бот отказывал в тарифе
    # за 179 ₽, хотя списание ровно на эту сумму прошло бы.
    'app/handlers/subscription/tariff_purchase.py',
    'app/handlers/subscription/gift.py',
    'app/handlers/balance/main.py',
    'app/handlers/router_link.py',
    'app/services/daily_subscription_service.py',
    'app/services/subscription_auto_purchase_service.py',
    'app/services/subscription_purchase_service.py',
    'app/services/gift_purchase_service.py',
    'app/services/router_link_service.py',
    # Дозапрос картой: сам autopay (`monitoring_service`) считает по обоим
    # кошелькам, поэтому и нехватку надо считать так же — иначе с карты
    # списывается то, что уже лежит баллами.
    'app/services/recurrent_payment_service.py',
    # Кабинет и мини-апп списывают той же точкой, поэтому и «хватает ли» обязаны
    # считать по обоим кошелькам: иначе человеку с баллами на сайте отказывают
    # там, где бот бы продал.
    'app/cabinet/routes/subscription_modules/purchase.py',
    'app/cabinet/routes/subscription_modules/renewal.py',
    'app/cabinet/routes/subscription_modules/traffic.py',
    'app/cabinet/routes/subscription_modules/devices.py',
    'app/cabinet/routes/subscription_modules/servers.py',
    'app/cabinet/routes/subscription_modules/daily.py',
    'app/cabinet/routes/subscription_modules/tariff_switch.py',
    'app/webapi/routes/miniapp.py',
)

#: Места, где смотреть на одни деньги правильно: оператор правит денежный
#: кошелёк, а баллами распоряжается отдельной ручкой.
MONEY_ONLY_ALLOWED = {
    'app/cabinet/routes/admin_users.py': ('if user.balance_kopeks < amount_to_subtract:',),
    # Строка в кнопке списка пользователей: показать сумму, если деньги есть.
    # Это не гейт оплаты, а подпись, и баллы в ней были бы враньём.
    'app/handlers/admin/users.py': ('if user.balance_kopeks > 0:',),
}

#: Любое сравнение «что-то ...balance_kopeks» с чем-то: и `user.balance_kopeks <`,
#: и через промежуточную переменную вроде `user_balance_kopeks <`. Первая версия
#: сторожа требовала границу слова и второй вид пропускала — ровно так мимо
#: правила прошли обе покупки в simple_subscription.py.
_BALANCE_COMPARISON = re.compile(r'([\w.]*balance_kopeks)\s*[<>]')

#: Тот же гейт, но через getattr: `getattr(user, 'balance_kopeks', 0) < цена`.
#: Так в мини-аппе жили три проверки, которых сторож не видел.
_GETATTR_COMPARISON = re.compile(r"getattr\([^)]*'([\w]*balance_kopeks)'[^)]*\)\s*[<>]")

#: Денежный кошелёк, уложенный в переменную с произвольным именем:
#: `user_balance = db_user.balance_kopeks or 0`. Регексы выше требуют
#: `balance_kopeks` в самом имени и такую переменную не видят — именно так весь
#: тарифный флоу сравнивал цену с одними деньгами. Поэтому имя после такого
#: присваивания считается деньгами, как бы оно ни называлось.
#: `\b` перед именем столбца не даёт матчу зацепить `available_balance_kopeks`.
_MONEY_ASSIGNMENT = re.compile(r'^\s*([a-z_][\w]*)\s*=\s*[\w.]*\bbalance_kopeks\b')


def _money_only_names(line: str) -> list[str]:
    """Имена из сравнений, которые смотрят на деньги, а не на сумму кошельков."""
    found = _BALANCE_COMPARISON.findall(line) + _GETATTR_COMPARISON.findall(line)
    return [name for name in found if 'available' not in name]


def _money_variables(lines: list[str]) -> set[str]:
    """Переменные файла, в которые положили один денежный кошелёк."""
    return {match.group(1) for match in map(_MONEY_ASSIGNMENT.match, lines) if match}


def _compares_money_variable(line: str, names: set[str]) -> bool:
    return any(re.search(rf'\b{name}\s*[<>]|[<>]=?\s*{name}\b', line) for name in names)


def _money_only_lines(path: str) -> list[str]:
    allowed = MONEY_ONLY_ALLOWED.get(path, ())
    lines = (REPO / path).read_text(encoding='utf-8').split('\n')
    names = _money_variables(lines)
    return [
        f'{index}: {line.strip()}'
        for index, line in enumerate(lines, 1)
        if (_money_only_names(line) or _compares_money_variable(line, names)) and line.strip() not in allowed
    ]


@pytest.mark.parametrize('path', PAYMENT_PATHS)
def test_payment_paths_compare_against_both_wallets(path):
    """Сравнение с одними деньгами показало бы «недостаточно» тому, у кого есть баллы."""
    assert not _money_only_lines(path)


def test_the_guard_sees_comparisons_through_a_variable():
    """Сторож обязан ловить и `user_balance_kopeks < price` — на этом он уже прокалывался."""
    assert _money_only_names('    if total_required > 0 and user_balance_kopeks < total_required:')
    assert _money_only_names('    if user.balance_kopeks < price_kopeks:')
    assert not _money_only_names('    if user.available_balance_kopeks < price_kopeks:')
    assert not _money_only_names('    available = user.available_balance_kopeks')
    assert _money_only_names("    if total_cost > 0 and getattr(user, 'balance_kopeks', 0) < total_cost:")
    assert not _money_only_names("    if getattr(user, 'available_balance_kopeks', 0) < total_cost:")


def test_the_guard_sees_money_renamed_into_a_variable():
    """Так прошёл тарифный флоу: деньги в `user_balance`, сравнение уже с ней."""
    money = _money_variables(['    user_balance = db_user.balance_kopeks or 0'])
    assert money == {'user_balance'}
    assert _compares_money_variable('    if user_balance >= final_price:', money)
    assert _compares_money_variable('    if final_price > user_balance:', money)
    assert not _compares_money_variable('        balance=format_price_kopeks(user_balance),', money)

    assert not _money_variables(['    user_balance = db_user.available_balance_kopeks'])
    assert not _money_variables(['    user_balance = db_user.available_balance_kopeks or 0'])


def test_operator_money_withdrawal_does_not_eat_points():
    """«Снять 100 ₽» — про деньги: баллами оператор распоряжается отдельной ручкой."""
    crud = (REPO / 'app' / 'database' / 'crud' / 'user.py').read_text(encoding='utf-8')
    service = (REPO / 'app' / 'services' / 'user_service.py').read_text(encoding='utf-8')
    cabinet_admin = (REPO / 'app' / 'cabinet' / 'routes' / 'admin_users.py').read_text(encoding='utf-8')

    assert 'use_bonus_points: bool = True' in crud
    assert 'use_bonus_points=False' in service, 'списание баланса из админки бота'
    assert 'use_bonus_points=False' in cabinet_admin, 'списание баланса из админки кабинета'


def test_points_are_visible_where_money_is():
    """Человек не должен гадать, чем оплатилась покупка при нулевых деньгах."""
    balance_screen = (REPO / 'app' / 'handlers' / 'balance' / 'main.py').read_text(encoding='utf-8')
    api_schema = (REPO / 'app' / 'cabinet' / 'schemas' / 'balance.py').read_text(encoding='utf-8')

    assert 'BALANCE_BONUS_POINTS_LINE' in balance_screen, 'экран «Баланс» в боте'
    assert 'bonus_points_kopeks' in api_schema and 'available_kopeks' in api_schema, 'ответ /cabinet/balance'


def test_crediting_points_leaves_an_audit_trail():
    """Денежная правка оставляет транзакцию; у баллов след — журнал админских действий."""
    service = (REPO / 'app' / 'services' / 'user_service.py').read_text(encoding='utf-8')
    start = service.index('async def update_user_bonus_points')
    body = service[start : service.index('async def _send_bonus_points_notification', start)]

    assert 'AuditLogCRUD' in body
    assert "action='bonus_points_changed'" in body


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
