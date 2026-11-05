"""Аудитория рассылки «Триал 0 ГБ»: кто взял триал и не потратил ни байта.

Правила аудитории:

* подписка триальная (``is_trial``) — срок и статус не важны, истёкший триал
  тоже получит рассылку;
* израсходовано **строго 0 байт**;
* аккаунт пользователя ещё существует в Remnawave: удалённым из панели не
  пишем.

Расход и существование аккаунта берутся из панели, а не из нашей
``subscriptions.traffic_used_gb``: во-первых, у синхронизаторов есть допуск
0.01 ГБ (≈10 МБ) и колонка может показывать ноль человеку, который уже что-то
скачал; во-вторых, признака «аккаунт удалён из панели» в базе бота нет вовсе —
``remnawave_id`` чистится лениво, только когда бот сам полез обновлять именно
эту подписку.

Экономия: **один** обход панели на всю рассылку (как в
``live_menu_service``), а не запрос на каждого кандидата, и результат живёт
несколько минут в памяти процесса — чтобы предпросмотр и отправка считали по
одной и той же карте и не могли разойтись в числе получателей.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import Subscription, User, UserStatus
from app.services.remnawave_service import RemnaWaveService


logger = structlog.get_logger(__name__)

#: Сколько секунд держать карту расхода в памяти процесса. Хватает, чтобы
#: нажатие «Отправить» сразу после предпросмотра не обходило панель второй раз.
PANEL_TRAFFIC_CACHE_TTL = 180.0

#: Размер страницы обхода панели — как в live_menu_service (контракт: 1..1000).
PANEL_PAGE_SIZE = 1000

_cache: tuple[float, dict[int, int]] | None = None


class PanelAudienceUnavailableError(RuntimeError):
    """Панель не ответила: аудиторию посчитать нечем.

    Молча деградировать тут нельзя — пустая карта означала бы «всех удалили»,
    а карта из базы бота вернула бы и удалённых из панели. Рассылку не
    отменить, поэтому лучше честно отказать, чем отправить не тем.
    """


def reset_panel_traffic_cache() -> None:
    """Забыть карту расхода (тесты и ручной сброс)."""
    global _cache
    _cache = None


async def get_panel_traffic_map(*, force_refresh: bool = False) -> dict[int, int]:
    """``{panel_user_id: использовано байт}`` по всей панели, одним обходом.

    Ключи карты — это и есть ответ на «аккаунт не удалён»: чего в карте нет,
    того в панели нет.
    """
    global _cache

    if not force_refresh and _cache is not None:
        fetched_at, cached = _cache
        if time.monotonic() - fetched_at < PANEL_TRAFFIC_CACHE_TTL:
            return cached

    service = RemnaWaveService()
    if not service.is_configured:
        raise PanelAudienceUnavailableError('RemnaWave API не настроен')

    try:
        async with service.get_api_client() as api:
            panel_users = await api.get_all_users_stream(size=PANEL_PAGE_SIZE, enrich_happ_links=False)
    except Exception as error:
        logger.warning('Панель не ответила на обход пользователей', error=str(error)[:200])
        raise PanelAudienceUnavailableError(str(error)) from error

    traffic = {user.id: (user.used_traffic_bytes or 0) for user in panel_users if user.id is not None}
    _cache = (time.monotonic(), traffic)
    logger.info('Карта расхода панели обновлена', panel_users=len(traffic))
    return traffic


def _panel_user_id(user: User, subscription_panel_id: int | None) -> int | None:
    """Чей аккаунт в панели отвечает за эту подписку.

    В мультитарифе у каждой подписки свой аккаунт, и подставлять общий
    ``users.remnawave_id`` нельзя — это адрес аккаунта соседней подписки (та же
    причина, что в ``_get_panel_user_id`` хендлеров устройств).
    """
    if settings.is_multi_tariff_enabled():
        return subscription_panel_id
    return subscription_panel_id or user.remnawave_id


async def get_trial_zero_traffic_users(db: AsyncSession, *, force_refresh: bool = False) -> list[User]:
    """Пользователи для рассылки «Триал 0 ГБ».

    Один источник и для числа в предпросмотре, и для самой отправки: иначе они
    расходились бы — так и было, когда предпросмотр считал SQL-ом по статусу, а
    отправка фильтровала в Python по ``is_active``.
    """
    traffic = await get_panel_traffic_map(force_refresh=force_refresh)

    panel_link_present = (
        Subscription.remnawave_id.isnot(None)
        if settings.is_multi_tariff_enabled()
        else or_(Subscription.remnawave_id.isnot(None), User.remnawave_id.isnot(None))
    )

    stmt = (
        select(User, Subscription.remnawave_id)
        .join(Subscription, Subscription.user_id == User.id)
        .where(
            User.status == UserStatus.ACTIVE.value,
            # Рассылка уходит в Telegram: получатели без него (только почта)
            # раньше попадали в число предпросмотра и отсеивались при отправке.
            User.telegram_id.isnot(None),
            Subscription.is_trial.is_(True),
            panel_link_present,
        )
    )

    return pick_zero_traffic_users((await db.execute(stmt)).all(), traffic)


def pick_zero_traffic_users(
    rows: Iterable[tuple[User, int | None]],
    traffic: Mapping[int, int],
) -> list[User]:
    """Отбор по карте панели: аккаунт жив и израсходовано ровно 0 байт.

    Считаем по человеку, а не по подписке: в мультитарифе триалов у одного
    человека бывает несколько, и если хоть на одном живом аккаунте есть
    расход — он подключался, значит не наш адресат.

    Аккаунты, которых в панели уже нет, в расчёт не идут: их расход нам
    неизвестен, а человек остаётся в панели остальными. Если живых
    аккаунтов не осталось вовсе — человек удалён и рассылку не получает.

    Вынесено из запроса отдельной функцией — решение проверяемо без БД и
    панели, а оно тут и есть суть аудитории.
    """
    candidates: dict[int, User] = {}
    alive_accounts: dict[int, int] = {}
    spent_anything: set[int] = set()

    for user, subscription_panel_id in rows:
        panel_id = _panel_user_id(user, subscription_panel_id)
        if panel_id is None or panel_id not in traffic:
            # Аккаунта в панели нет — либо удалён, либо так и не создался.
            continue

        candidates.setdefault(user.id, user)
        alive_accounts[user.id] = alive_accounts.get(user.id, 0) + 1
        # None от панели = «расхода нет», но аккаунт существует.
        if (traffic[panel_id] or 0) > 0:
            spent_anything.add(user.id)

    return [
        user
        for user_id, user in candidates.items()
        if alive_accounts.get(user_id) and user_id not in spent_anything
    ]
