"""Продажа vless-ссылки для роутера.

Главное требование фичи — ссылка существует только после оплаты. Поэтому здесь
проверяется не только «цена посчиталась», а порядок обращений: до списания бот
не должен ни разу спросить у панели ключи подписки, а экраны до оплаты не
должны содержать ни ссылки, ни адреса хоста.

Второе по важности — выборка локаций: ссылка не имеет права прийти из сквада,
который оператор не включал, или из сквада, в котором пользователя нет.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.router_link_service as service
from app.external.remnawave_api import RemnaWaveHost, RemnaWaveInbound, RemnaWaveInternalSquad


GAME_PREMIUM = 'squad-game-premium'
GAME_STANDART = 'squad-game-standart'
VPN_PREMIUM = 'squad-vpn-premium'

PREMIUM_LINK = 'vless://user-uuid@nl.example:443?security=reality#%F0%9F%87%B3%F0%9F%87%B1%20Netherlands'
STANDART_LINK = 'vless://user-uuid@de.example:443?security=reality#Germany'
VPN_LINK = 'vless://user-uuid@se.example:22?security=reality#Sweden'


def _inbound(uuid: str) -> RemnaWaveInbound:
    return RemnaWaveInbound(uuid=uuid, profile_uuid='profile', tag=uuid, type='vless')


def _squad(uuid: str, name: str, inbound_uuid: str) -> RemnaWaveInternalSquad:
    return RemnaWaveInternalSquad(
        uuid=uuid,
        name=name,
        members_count=1,
        inbounds_count=1,
        inbounds=[_inbound(inbound_uuid)],
    )


def _host(
    uuid: str,
    remark: str,
    address: str,
    inbound_uuid: str,
    *,
    is_hidden: bool = False,
    mode: str | None = None,
    squads: list[str] | None = None,
) -> RemnaWaveHost:
    return RemnaWaveHost(
        uuid=uuid,
        remark=remark,
        address=address,
        port=443,
        is_hidden=is_hidden,
        config_profile_inbound_uuid=inbound_uuid,
        internal_squads_mode=mode,
        internal_squad_uuids=squads or [],
    )


SQUADS = [
    _squad(GAME_PREMIUM, 'game-premium', 'inbound-game'),
    _squad(GAME_STANDART, 'game-standart', 'inbound-game'),
    _squad(VPN_PREMIUM, 'vpn-premium', 'inbound-vpn'),
]

HOSTS = [
    # Игровая локация для премиума: отдаётся только своему скваду.
    _host('host-nl', '🇳🇱 Netherlands', 'nl.example', 'inbound-game', mode='ALLOW_ONLY', squads=[GAME_PREMIUM]),
    # Игровая локация без ограничений: доступна любому скваду своего инбаунда.
    _host('host-de', 'Germany', 'de.example', 'inbound-game', mode='EXCLUDE', squads=[]),
    # Скрытый хост: в подписки не попадает вовсе.
    _host('host-hidden', 'Hidden node', 'hidden.example', 'inbound-game', is_hidden=True),
    # Хост другого (не игрового) сквада: продаваться не должен.
    _host('host-se', 'Sweden', 'se.example', 'inbound-vpn', mode='EXCLUDE', squads=[]),
]


class FakeApi:
    """Двойник панели: считает, когда спрашивали ключи подписки."""

    def __init__(self, *, user_squads: list[str], links: list[str] | None = None):
        self.user_squads = user_squads
        self.links = links if links is not None else [PREMIUM_LINK, STANDART_LINK, VPN_LINK]
        self.link_calls = 0

    async def get_user_by_short_uuid(self, short_uuid: str):
        return SimpleNamespace(
            short_uuid=short_uuid,
            active_internal_squads=[{'uuid': uuid, 'name': uuid} for uuid in self.user_squads],
        )

    async def get_internal_squads(self):
        return SQUADS

    async def get_all_hosts(self):
        return HOSTS

    async def get_subscription_links_by_short_uuid(self, short_uuid: str):
        self.link_calls += 1
        return self.links


@pytest.fixture
def panel(monkeypatch):
    """Панель с премиум-пользователем; тест может подменить состав сквадов."""
    api = FakeApi(user_squads=[GAME_PREMIUM, VPN_PREMIUM])

    @asynccontextmanager
    async def client():
        yield api

    monkeypatch.setattr(service, '_panel_client', client)
    return api


@pytest.fixture
def settings_store(monkeypatch):
    """Настройки фичи в памяти вместо system_settings."""
    store = {service.SQUADS_SETTING_KEY: 'game-premium,game-standart', service.PRICE_SETTING_KEY: '19900'}

    async def get_setting_value(_db, key):
        return store.get(key)

    async def upsert_system_setting(_db, key, value, description=None):
        store[key] = value
        return SimpleNamespace(key=key, value=value)

    monkeypatch.setattr(service, 'get_setting_value', get_setting_value)
    monkeypatch.setattr(service, 'upsert_system_setting', upsert_system_setting)
    return store


@pytest.fixture
def subscription(monkeypatch):
    sub = SimpleNamespace(id=7, remnawave_short_uuid='SHORT-1')
    monkeypatch.setattr(service, '_resolve_subscription', AsyncMock(return_value=sub))
    return sub


def _db():
    db = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _user(balance_kopeks: int = 100_000):
    return SimpleNamespace(id=42, telegram_id=1, language='ru', balance_kopeks=balance_kopeks)


# ---- настройки ----


async def test_price_and_squads_round_trip(settings_store) -> None:
    db = _db()
    await service.set_price_kopeks(db, 25_000)
    await service.set_enabled_squad_names(db, ['game-premium', ' game-premium ', 'game-standart'])

    assert await service.get_price_kopeks(db) == 25_000
    # Повторы и пробелы отбрасываются, порядок сохраняется.
    assert await service.get_enabled_squad_names(db) == ['game-premium', 'game-standart']
    assert await service.is_configured(db) is True


@pytest.mark.parametrize(
    ('squads', 'price'),
    [('', '19900'), ('game-premium', '0'), ('', '0')],
    ids=('без сквадов', 'без цены', 'ничего не задано'),
)
async def test_feature_is_off_until_both_settings_are_set(settings_store, squads, price) -> None:
    settings_store[service.SQUADS_SETTING_KEY] = squads
    settings_store[service.PRICE_SETTING_KEY] = price

    assert await service.is_configured(_db()) is False


async def test_broken_price_in_settings_reads_as_zero(settings_store) -> None:
    settings_store[service.PRICE_SETTING_KEY] = 'не число'

    assert await service.get_price_kopeks(_db()) == 0


# ---- выборка локаций ----


async def test_locations_come_only_from_enabled_and_joined_squads(settings_store, panel, subscription) -> None:
    availability = await service.get_available_locations(_db(), _user())

    assert availability.reason == 'ok'
    remarks = [location.remark for location in availability.locations]
    # host-nl — игровой сквад премиума, host-de — игровой инбаунд без ограничений.
    assert remarks == ['Germany', '🇳🇱 Netherlands']
    # Швеция — сквад vpn-premium, он не включён оператором.
    assert 'Sweden' not in remarks
    # Скрытый хост панель в подписки не отдаёт.
    assert 'Hidden node' not in remarks


async def test_allow_only_host_is_hidden_from_other_squads(settings_store, panel, subscription) -> None:
    """Стандартному пользователю премиум-локация не положена."""
    panel.user_squads = [GAME_STANDART]

    availability = await service.get_available_locations(_db(), _user())

    assert [location.remark for location in availability.locations] == ['Germany']


async def test_user_without_enabled_squads_gets_reason(settings_store, panel, subscription) -> None:
    panel.user_squads = [VPN_PREMIUM]

    availability = await service.get_available_locations(_db(), _user())

    assert availability.reason == 'no_squads'
    assert availability.locations == []


async def test_disabled_feature_does_not_touch_the_panel(settings_store, panel, subscription) -> None:
    settings_store[service.SQUADS_SETTING_KEY] = ''

    availability = await service.get_available_locations(_db(), _user())

    assert availability.reason == 'disabled'
    assert panel.link_calls == 0


async def test_no_subscription_reason(settings_store, panel, monkeypatch) -> None:
    monkeypatch.setattr(service, '_resolve_subscription', AsyncMock(return_value=None))

    availability = await service.get_available_locations(_db(), _user())

    assert availability.reason == 'no_subscription'


async def test_panel_failure_is_reported_not_raised(settings_store, subscription, monkeypatch) -> None:
    @asynccontextmanager
    async def broken_client():
        raise RuntimeError('панель недоступна')
        yield  # pragma: no cover

    monkeypatch.setattr(service, '_panel_client', broken_client)

    availability = await service.get_available_locations(_db(), _user())

    assert availability.reason == 'panel_error'


# ---- сопоставление ссылки с локацией ----


async def test_link_is_matched_by_remark(settings_store, panel, subscription) -> None:
    location = service.RouterLocation(
        host_uuid='host-nl', remark='🇳🇱 Netherlands', squad_uuid=GAME_PREMIUM, squad_name='game-premium'
    )

    assert await service.resolve_link(_db(), _user(), location) == PREMIUM_LINK


async def test_link_is_matched_by_address_when_remark_changed(settings_store, panel, subscription) -> None:
    """Хост переименовали после покупки — остаётся адрес и порт."""
    location = service.RouterLocation(
        host_uuid='host-de',
        remark='Германия (новое имя)',
        squad_uuid=GAME_STANDART,
        squad_name='game-standart',
        address='de.example',
        port=443,
    )

    assert await service.resolve_link(_db(), _user(), location) == STANDART_LINK


async def test_missing_link_returns_none(settings_store, panel, subscription) -> None:
    panel.links = [VPN_LINK]
    location = service.RouterLocation(
        host_uuid='host-nl',
        remark='🇳🇱 Netherlands',
        squad_uuid=GAME_PREMIUM,
        squad_name='game-premium',
        address='nl.example',
        port=443,
    )

    assert await service.resolve_link(_db(), _user(), location) is None


# ---- покупка ----


@pytest.fixture
def purchases(monkeypatch):
    """Учёт прав в памяти + счётчик списаний."""
    state = {'rows': [], 'charges': []}

    async def get_purchase(_db, user_id, host_uuid):
        return next(
            (row for row in state['rows'] if row.user_id == user_id and row.host_uuid == host_uuid),
            None,
        )

    async def get_user_purchases(_db, user_id):
        return [row for row in state['rows'] if row.user_id == user_id]

    async def create_purchase(_db, **kwargs):
        row = SimpleNamespace(**kwargs)
        state['rows'].append(row)
        return row

    async def subtract_user_balance(_db, user, amount_kopeks, description, **kwargs):
        state['charges'].append((user.id, amount_kopeks, description))
        user.balance_kopeks -= amount_kopeks
        return True

    monkeypatch.setattr(service, 'get_purchase', get_purchase)
    monkeypatch.setattr(service, 'get_user_purchases', get_user_purchases)
    monkeypatch.setattr(service, 'create_purchase', create_purchase)
    monkeypatch.setattr(service, 'subtract_user_balance', subtract_user_balance)
    return state


@pytest.fixture
def locked_user(monkeypatch):
    """lock_user_for_pricing возвращает того же пользователя, что пришёл."""

    async def lock(_db, user_id):
        return lock.user

    lock.user = None
    monkeypatch.setattr(service, 'lock_user_for_pricing', lock)
    return lock


async def test_purchase_charges_once_and_returns_the_link(
    settings_store, panel, subscription, purchases, locked_user
) -> None:
    user = _user(balance_kopeks=50_000)
    locked_user.user = user

    outcome = await service.purchase_location(_db(), user, 'host-nl')

    assert outcome.status == 'ok'
    assert outcome.link == PREMIUM_LINK
    assert purchases['charges'] == [(42, 19_900, 'Ссылка для роутера: 🇳🇱 Netherlands')]
    assert [row.host_uuid for row in purchases['rows']] == ['host-nl']
    assert purchases['rows'][0].squad_name == 'game-premium'


async def test_second_purchase_of_the_same_location_is_free(
    settings_store, panel, subscription, purchases, locked_user
) -> None:
    user = _user(balance_kopeks=50_000)
    locked_user.user = user

    await service.purchase_location(_db(), user, 'host-nl')
    outcome = await service.purchase_location(_db(), user, 'host-nl')

    assert outcome.status == 'already'
    assert outcome.link == PREMIUM_LINK
    assert len(purchases['charges']) == 1, 'повторный показ не должен списывать деньги'


async def test_insufficient_balance_neither_charges_nor_asks_for_the_key(
    settings_store, panel, subscription, purchases, locked_user
) -> None:
    """Ключ не должен даже запрашиваться у панели, пока оплаты нет."""
    user = _user(balance_kopeks=5_000)
    locked_user.user = user

    outcome = await service.purchase_location(_db(), user, 'host-nl')

    assert outcome.status == 'insufficient'
    assert outcome.missing_kopeks == 14_900
    assert outcome.link is None
    assert purchases['charges'] == []
    assert purchases['rows'] == []
    assert panel.link_calls == 0, 'до оплаты бот не должен запрашивать ключи подписки'


async def test_location_outside_enabled_squads_cannot_be_bought_by_callback(
    settings_store, panel, subscription, purchases, locked_user
) -> None:
    """Устаревшая кнопка не должна продать локацию чужого сквада."""
    user = _user()
    locked_user.user = user

    outcome = await service.purchase_location(_db(), user, 'host-se')

    assert outcome.status == 'unavailable'
    assert purchases['charges'] == []
    assert panel.link_calls == 0


async def test_hidden_host_cannot_be_bought_by_callback(
    settings_store, panel, subscription, purchases, locked_user
) -> None:
    user = _user()
    locked_user.user = user

    outcome = await service.purchase_location(_db(), user, 'host-hidden')

    assert outcome.status == 'unavailable'
    assert purchases['charges'] == []


async def test_purchase_is_recorded_even_if_the_panel_has_no_link_yet(
    settings_store, panel, subscription, purchases, locked_user
) -> None:
    """Деньги списаны — право остаётся за человеком, ссылку покажем позже."""
    user = _user()
    locked_user.user = user
    panel.links = []

    outcome = await service.purchase_location(_db(), user, 'host-nl')

    assert outcome.status == 'link_missing'
    assert outcome.link is None
    assert [row.host_uuid for row in purchases['rows']] == ['host-nl']
    assert len(purchases['charges']) == 1


async def test_key_is_requested_only_after_the_charge(
    settings_store, panel, subscription, purchases, locked_user, monkeypatch
) -> None:
    """Порядок: сначала списание, потом запрос ключа — не наоборот."""
    user = _user()
    locked_user.user = user
    order: list[str] = []

    original_charge = service.subtract_user_balance

    async def charge(*args, **kwargs):
        order.append('charge')
        return await original_charge(*args, **kwargs)

    original_links = panel.get_subscription_links_by_short_uuid

    async def links(short_uuid):
        order.append('ask-key')
        return await original_links(short_uuid)

    monkeypatch.setattr(service, 'subtract_user_balance', charge)
    panel.get_subscription_links_by_short_uuid = links

    outcome = await service.purchase_location(_db(), user, 'host-nl')

    assert outcome.status == 'ok'
    assert order == ['charge', 'ask-key']
