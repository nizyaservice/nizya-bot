"""Роутер-ссылки: продажа vless-ключа одной локации для прошивки в роутер.

Зачем сервис, а не код в хендлере: решение «какие локации человеку вообще
положены» собирается из трёх источников панели (сквады пользователя, состав
сквадов, привязка хостов), и повторять эту сборку в хендлере, в тестах и в
админке было бы тремя расхождениями.

Главное правило фичи — **ссылка существует только после оплаты**. Поэтому:

* до оплаты наружу уходит лишь имя локации; адрес, порт, SNI и сам ключ
  остаются внутри сервиса и не попадают ни в текст, ни в ``callback_data``;
* ссылка запрашивается у панели ПОСЛЕ успешного списания (и при повторном
  показе уже оплаченной локации), а не заранее — так её нечего перехватить
  в трафике бота: до оплаты её попросту никто не запрашивал;
* в нашей базе ссылка не хранится (см. ``RouterLinkPurchase``), в логи не
  пишется: в ней vless-UUID пользователя, то есть его ключ доступа.

Настройки живут в ``system_settings`` (как ``ui_section_image.*``): оператор
правит их в админке на ходу, перезапуск бота не нужен.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.router_link import create_purchase, get_purchase, get_user_purchases
from app.database.crud.system_setting import get_setting_value, upsert_system_setting
from app.database.crud.user import lock_user_for_pricing, subtract_user_balance
from app.database.models import Subscription, SubscriptionStatus, User


logger = structlog.get_logger(__name__)

PRICE_SETTING_KEY = 'router_link.price_kopeks'
SQUADS_SETTING_KEY = 'router_link.squads'

#: Статусы подписки, при которых ключ вообще работает: ссылку из панели отдаём
#: только тем, у кого доступ живой — иначе человек заплатит за нерабочий ключ.
_USABLE_STATUSES = (SubscriptionStatus.ACTIVE.value, SubscriptionStatus.TRIAL.value)


@dataclass(frozen=True)
class RouterLocation:
    """Локация, доступная пользователю: что показываем и чем сверяем ссылку.

    ``address``/``port`` нужны только для сопоставления ссылки с хостом (имена
    хостов в панели не уникальны) и наружу до оплаты не уходят.
    """

    host_uuid: str
    remark: str
    squad_uuid: str
    squad_name: str
    address: str = ''
    port: int | None = None


@dataclass(frozen=True)
class Availability:
    """Что показать пользователю: локации и причина, если их нет."""

    locations: list[RouterLocation]
    #: ``disabled`` — функция не настроена, ``no_subscription`` — нет живой
    #: подписки, ``no_squads`` — подписка есть, но её сквады не продаются,
    #: ``panel_error`` — панель не ответила, ``ok`` — есть что показать.
    reason: str
    subscription_id: int | None = None


@dataclass(frozen=True)
class PurchaseOutcome:
    """Итог покупки. ``link`` заполняется только при фактическом доступе."""

    status: str  # ok | already | insufficient | unavailable | disabled | charge_failed | link_missing
    link: str | None = None
    price_kopeks: int = 0
    missing_kopeks: int = 0
    location: RouterLocation | None = None


# ---- настройки (админка) ----


async def get_price_kopeks(db: AsyncSession) -> int:
    """Цена одной локации в копейках; 0 — цена не задана."""
    raw = await get_setting_value(db, PRICE_SETTING_KEY)
    try:
        return max(0, int(raw)) if raw else 0
    except (TypeError, ValueError):
        logger.warning('Нечитаемая цена роутер-ссылки в настройках', raw=raw)
        return 0


async def set_price_kopeks(db: AsyncSession, price_kopeks: int) -> None:
    await upsert_system_setting(
        db,
        PRICE_SETTING_KEY,
        str(max(0, int(price_kopeks))),
        description='Цена vless-ссылки для роутера, копейки (0 — функция выключена)',
    )
    await db.commit()


async def get_enabled_squad_names(db: AsyncSession) -> list[str]:
    """Имена сквадов, из которых разрешено продавать ссылки."""
    raw = await get_setting_value(db, SQUADS_SETTING_KEY)
    if not raw:
        return []
    return [name.strip() for name in raw.split(',') if name.strip()]


async def set_enabled_squad_names(db: AsyncSession, names: list[str]) -> None:
    """Сохраняет список сквадов, сохраняя порядок и выкидывая повторы."""
    unique = list(dict.fromkeys(name.strip() for name in names if name.strip()))
    await upsert_system_setting(
        db,
        SQUADS_SETTING_KEY,
        ','.join(unique),
        description='Сквады, из которых продаются vless-ссылки для роутера (через запятую)',
    )
    await db.commit()


async def is_configured(db: AsyncSession) -> bool:
    """Функция включена: заданы и сквады, и цена.

    Цена 0 — выключатель: без неё оператор, добавивший сквад, начал бы
    раздавать ключи бесплатно.
    """
    return bool(await get_enabled_squad_names(db)) and await get_price_kopeks(db) > 0


# ---- панель ----


def _panel_client():
    from app.services.remnawave_service import RemnaWaveService

    return RemnaWaveService().get_api_client()


async def list_panel_squad_names() -> list[str]:
    """Имена внутренних сквадов панели — для подсказки и проверки в админке."""
    async with _panel_client() as api:
        return [squad.name for squad in await api.get_internal_squads()]


async def _resolve_subscription(db: AsyncSession, user_id: int) -> Subscription | None:
    """Живая подписка пользователя с панельным shortUuid."""
    result = await db.execute(
        select(Subscription)
        .where(
            Subscription.user_id == user_id,
            Subscription.status.in_(_USABLE_STATUSES),
            Subscription.remnawave_short_uuid.is_not(None),
        )
        .order_by(Subscription.end_date.desc())
        .limit(1)
    )
    return result.scalars().first()


async def get_available_locations(db: AsyncSession, user: User) -> Availability:
    """Локации, которые человеку можно продать.

    Цепочка: разрешённые оператором сквады → сквады, в которых состоит
    пользователь (панель) → хосты этих сквадов (по инбаунду и привязке
    ``internalSquads``, скрытые пропускаем). Так ссылка не может прийти из
    сквада, который оператор не включал.
    """
    squad_names = await get_enabled_squad_names(db)
    if not squad_names or await get_price_kopeks(db) <= 0:
        return Availability([], 'disabled')

    subscription = await _resolve_subscription(db, user.id)
    if subscription is None:
        return Availability([], 'no_subscription')

    try:
        async with _panel_client() as api:
            panel_user = await api.get_user_by_short_uuid(subscription.remnawave_short_uuid)
            squads = await api.get_internal_squads()
            hosts = await api.get_all_hosts()
    except Exception as error:
        logger.error(
            'Не удалось получить данные панели для роутер-ссылок',
            user_id=user.id,
            error=str(error),
        )
        return Availability([], 'panel_error', subscription.id)

    if panel_user is None:
        logger.warning('Панельный пользователь не найден по shortUuid', user_id=user.id)
        return Availability([], 'no_subscription', subscription.id)

    user_squad_uuids = {
        str(squad.get('uuid'))
        for squad in (panel_user.active_internal_squads or [])
        if isinstance(squad, dict) and squad.get('uuid')
    }
    wanted = {name.casefold() for name in squad_names}
    allowed_squads = [squad for squad in squads if squad.name.casefold() in wanted and squad.uuid in user_squad_uuids]
    if not allowed_squads:
        return Availability([], 'no_squads', subscription.id)

    locations: dict[str, RouterLocation] = {}
    for squad in allowed_squads:
        inbound_uuids = {inbound.uuid for inbound in (squad.inbounds or [])}
        for host in hosts:
            if host.uuid in locations:
                continue
            if host.config_profile_inbound_uuid not in inbound_uuids:
                continue
            if not host.is_served_to_squad(squad.uuid):
                continue
            locations[host.uuid] = RouterLocation(
                host_uuid=host.uuid,
                remark=host.remark or host.address,
                squad_uuid=squad.uuid,
                squad_name=squad.name,
                address=host.address,
                port=host.port,
            )

    ordered = sorted(locations.values(), key=lambda location: location.remark)
    return Availability(ordered, 'ok' if ordered else 'no_squads', subscription.id)


async def purchased_host_uuids(db: AsyncSession, user_id: int) -> set[str]:
    return {purchase.host_uuid for purchase in await get_user_purchases(db, user_id)}


def _link_matches(link: str, location: RouterLocation) -> bool:
    """Ссылка принадлежит этой локации.

    Имя (``#remark``) совпадает с remark хоста — так панель и подписывает
    ключи. Имена хостов не уникальны (один адрес отдают два хоста), поэтому
    при совпадении имени дополнительно сверяем адрес и порт, если они известны.
    """
    fragment = unquote(link.split('#', 1)[1]).strip() if '#' in link else ''
    if fragment and location.remark and fragment == location.remark:
        return True
    if not location.address:
        return False
    parts = urlsplit(link)
    if (parts.hostname or '').casefold() != location.address.casefold():
        return False
    return location.port is None or parts.port == location.port


async def resolve_link(db: AsyncSession, user: User, location: RouterLocation) -> str | None:
    """Ключ локации из панели. Вызывать только когда локация уже оплачена."""
    subscription = await _resolve_subscription(db, user.id)
    if subscription is None:
        return None

    try:
        async with _panel_client() as api:
            links = await api.get_subscription_links_by_short_uuid(subscription.remnawave_short_uuid)
    except Exception as error:
        logger.error('Не удалось получить ссылки подписки из панели', user_id=user.id, error=str(error))
        return None

    for link in links:
        if _link_matches(str(link), location):
            return str(link)

    # Ссылки не нашлось: хост убрали из сквада или переименовали уже после
    # покупки. Ключ в лог не пишем — только то, чего не хватило.
    logger.warning(
        'В подписке нет ключа оплаченной локации',
        user_id=user.id,
        host_uuid=location.host_uuid,
        links_count=len(links),
    )
    return None


# ---- покупка ----


async def purchase_location(db: AsyncSession, user: User, host_uuid: str) -> PurchaseOutcome:
    """Продаёт локацию: проверка → списание → право → ссылка.

    Порядок важен дважды. Во-первых, доступность проверяется заново по панели,
    а не по ``callback_data``: кнопка могла устареть, а сквад — закрыться.
    Во-вторых, ссылка запрашивается последней, уже после списания — до оплаты
    бот её вообще не запрашивает, поэтому и перехватить нечего.

    Списание и запись права уходят одним коммитом под блокировкой строки
    пользователя: иначе двойной тап по кнопке списал бы деньги дважды.
    """
    availability = await get_available_locations(db, user)
    location = next((item for item in availability.locations if item.host_uuid == host_uuid), None)
    if location is None:
        return PurchaseOutcome('disabled' if availability.reason == 'disabled' else 'unavailable')

    price_kopeks = await get_price_kopeks(db)
    if price_kopeks <= 0:
        return PurchaseOutcome('disabled')

    locked_user = await lock_user_for_pricing(db, user.id)

    existing = await get_purchase(db, locked_user.id, host_uuid)
    if existing is not None:
        await db.commit()
        return PurchaseOutcome(
            'already',
            link=await resolve_link(db, locked_user, location),
            price_kopeks=price_kopeks,
            location=location,
        )

    balance = locked_user.balance_kopeks or 0
    if balance < price_kopeks:
        await db.commit()
        return PurchaseOutcome(
            'insufficient',
            price_kopeks=price_kopeks,
            missing_kopeks=price_kopeks - balance,
            location=location,
        )

    charged = await subtract_user_balance(
        db,
        locked_user,
        price_kopeks,
        f'Ссылка для роутера: {location.remark}',
        create_transaction=True,
        commit=False,
    )
    if not charged:
        await db.rollback()
        return PurchaseOutcome('charge_failed', price_kopeks=price_kopeks, location=location)

    await create_purchase(
        db,
        user_id=locked_user.id,
        subscription_id=availability.subscription_id,
        squad_uuid=location.squad_uuid,
        squad_name=location.squad_name,
        host_uuid=location.host_uuid,
        host_remark=location.remark,
        price_kopeks=price_kopeks,
        commit=False,
    )
    await db.commit()

    link = await resolve_link(db, locked_user, location)
    if link is None:
        # Деньги списаны, право записано — ссылку отдадим при следующем
        # открытии экрана. Молча «терять» оплату нельзя, поэтому отдельный
        # статус: хендлер скажет человеку, что доступ уже его.
        return PurchaseOutcome('link_missing', price_kopeks=price_kopeks, location=location)

    logger.info(
        'Роутер-ссылка оплачена',
        user_id=locked_user.id,
        host_uuid=location.host_uuid,
        squad=location.squad_name,
        price_kopeks=price_kopeks,
    )
    return PurchaseOutcome('ok', link=link, price_kopeks=price_kopeks, location=location)
