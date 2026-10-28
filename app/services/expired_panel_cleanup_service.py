"""Снос пользователей Remnawave через N дней после истечения подписки.

До этого истёкшая подписка меняла статус только в базе бота: аккаунт в панели
оставался навсегда и продолжал занимать место. Автоматической чистки не было
вовсе — ни одна из существующих не подходила. ``INACTIVE_USER_DELETE_MONTHS``
считает неактивность пользователя (12 месяцев) и панель не трогает, а снос
истёкших триалов запускается только кнопкой в админке.

Срок задаётся ``REMNAWAVE_DELETE_EXPIRED_AFTER_DAYS``; 0 выключает чистку.
Способ — общий для всего бота ``REMNAWAVE_USER_DELETE_MODE``: ``delete``
сносит аккаунт, ``disable`` только отключает.

Запись подписки в базе бота остаётся со статусом EXPIRED: история покупок и
статистика нужны, а повторная покупка создаст в панели свежий аккаунт. Для
этого после сноса стирается панельная идентичность — иначе бот попытался бы
обновить несуществующего пользователя. Следствие, о котором нужно помнить:
вернувшийся клиент получит НОВУЮ ссылку подписки, старая работать не будет.
"""

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.models import Subscription, SubscriptionStatus, User


logger = structlog.get_logger(__name__)

# Сколько аккаунтов сносим за один проход: панель — внешний сервис, большие
# пачки упираются в её лимиты и держат транзакцию открытой.
_BATCH_LIMIT = 200


def _grace_is_open(subscription: Subscription) -> bool:
    """Подписке продлён доступ грейсом — трогать панель нельзя."""
    if getattr(subscription, 'grace_session_open', False):
        return True
    now = datetime.now(UTC)
    for field in ('grace_tail_expire_at', 'grace_overlay_expire_at'):
        value = getattr(subscription, field, None)
        if value is not None and value > now:
            return True
    return False


async def _has_other_live_subscription(db: AsyncSession, subscription: Subscription) -> bool:
    """У человека есть другая живая подписка — аккаунт в панели ещё нужен."""
    result = await db.execute(
        select(Subscription.id)
        .where(
            Subscription.user_id == subscription.user_id,
            Subscription.id != subscription.id,
            Subscription.status.in_((SubscriptionStatus.ACTIVE.value, SubscriptionStatus.TRIAL.value)),
        )
        .limit(1)
    )
    return result.scalar() is not None


async def _select_candidates(db: AsyncSession, cutoff: datetime) -> list[Subscription]:
    result = await db.execute(
        select(Subscription)
        .options(selectinload(Subscription.user))
        .where(
            Subscription.status == SubscriptionStatus.EXPIRED.value,
            Subscription.end_date.is_not(None),
            Subscription.end_date <= cutoff,
        )
        .order_by(Subscription.end_date)
        .limit(_BATCH_LIMIT)
    )
    return list(result.scalars().unique().all())


def _panel_user_id(subscription: Subscription) -> int | None:
    """Аккаунт панели: в мультитарифе свой у каждой подписки, иначе общий у человека."""
    if settings.is_multi_tariff_enabled():
        return subscription.remnawave_id
    user = subscription.user
    return user.remnawave_id if user else None


async def _forget_panel_identity(db: AsyncSession, subscription: Subscription, panel_user_id: int) -> None:
    """Стереть ссылки на снесённый аккаунт, чтобы покупка завела новый.

    Только для режима ``delete``: при ``disable`` аккаунт жив, и потерять связь
    с ним нельзя — следующая покупка завела бы рядом дубль вместо включения
    отключённого (та же логика, что в wipe_trial_subscriptions).
    """
    subscription.remnawave_id = None
    subscription.remnawave_short_uuid = None
    subscription.subscription_url = ''
    subscription.subscription_crypto_link = ''

    if not settings.is_multi_tariff_enabled():
        await db.execute(
            update(User)
            .where(User.id == subscription.user_id, User.remnawave_id == panel_user_id)
            .values(remnawave_id=None, remnawave_uuid=None)
        )
    else:
        # Первый аккаунт записан и человеку: оставить там id снесённого значит
        # отдать его следующей покупке. Стираем только совпадающий.
        await db.execute(
            update(User)
            .where(User.id == subscription.user_id, User.remnawave_id == panel_user_id)
            .values(remnawave_id=None)
        )


async def cleanup_expired_panel_users(db: AsyncSession) -> int:
    """Снести из панели аккаунты подписок, истёкших больше N дней назад."""
    days = int(getattr(settings, 'REMNAWAVE_DELETE_EXPIRED_AFTER_DAYS', 0) or 0)
    if days <= 0:
        return 0

    from app.services.remnawave_service import RemnaWaveService

    service = RemnaWaveService()
    if not service.is_configured:
        return 0

    cutoff = datetime.now(UTC) - timedelta(days=days)
    candidates = await _select_candidates(db, cutoff)
    if not candidates:
        return 0

    delete_mode = settings.get_remnawave_user_delete_mode() == 'delete'
    processed = 0

    async with service.get_api_client() as api:
        for subscription in candidates:
            panel_user_id = _panel_user_id(subscription)
            if not panel_user_id:
                continue  # в панели уже нечего сносить
            if _grace_is_open(subscription):
                continue
            if await _has_other_live_subscription(db, subscription):
                continue

            try:
                if delete_mode:
                    await api.delete_user(panel_user_id)
                else:
                    await api.disable_user(panel_user_id)
            except Exception as error:
                logger.error(
                    'Не удалось убрать истёкшего пользователя из панели',
                    subscription_id=subscription.id,
                    panel_user_id=panel_user_id,
                    delete_mode=delete_mode,
                    error=str(error),
                )
                continue

            if delete_mode:
                await _forget_panel_identity(db, subscription, panel_user_id)
            processed += 1
            logger.info(
                'Истёкшая подписка убрана из панели',
                subscription_id=subscription.id,
                user_id=subscription.user_id,
                panel_user_id=panel_user_id,
                delete_mode=delete_mode,
                expired_at=subscription.end_date.isoformat() if subscription.end_date else None,
            )

    if processed:
        await db.commit()
    return processed
