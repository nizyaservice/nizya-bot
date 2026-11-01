"""Оплаченные роутер-ссылки: право смотреть ключ одной локации.

Сама ссылка не хранится (в ней vless-UUID пользователя) — см. модель
``RouterLinkPurchase``. Здесь только учёт прав.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import RouterLinkPurchase


async def get_user_purchases(db: AsyncSession, user_id: int) -> list[RouterLinkPurchase]:
    """Все оплаченные локации пользователя, в порядке покупки."""
    result = await db.execute(
        select(RouterLinkPurchase)
        .where(RouterLinkPurchase.user_id == user_id)
        .order_by(RouterLinkPurchase.created_at.asc(), RouterLinkPurchase.id.asc())
    )
    return list(result.scalars().all())


async def get_purchase(db: AsyncSession, user_id: int, host_uuid: str) -> RouterLinkPurchase | None:
    """Право на эту локацию, если она уже оплачена."""
    result = await db.execute(
        select(RouterLinkPurchase).where(
            RouterLinkPurchase.user_id == user_id,
            RouterLinkPurchase.host_uuid == host_uuid,
        )
    )
    return result.scalar_one_or_none()


async def create_purchase(
    db: AsyncSession,
    *,
    user_id: int,
    subscription_id: int | None,
    squad_uuid: str,
    squad_name: str,
    host_uuid: str,
    host_remark: str,
    price_kopeks: int,
    commit: bool = True,
) -> RouterLinkPurchase:
    """Записывает оплаченную локацию.

    Вызывается после успешного списания, в той же транзакции: право и списание
    должны появиться вместе — иначе человек заплатит и останется без ссылки.
    """
    purchase = RouterLinkPurchase(
        user_id=user_id,
        subscription_id=subscription_id,
        squad_uuid=squad_uuid,
        squad_name=squad_name,
        host_uuid=host_uuid,
        host_remark=host_remark,
        price_kopeks=price_kopeks,
    )
    db.add(purchase)
    if commit:
        await db.commit()
        await db.refresh(purchase)
    else:
        await db.flush()
    return purchase
