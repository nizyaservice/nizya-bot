"""Выключатель кнопки «Автопродление через СБП» в боте.

Саму фичу Platega-рекуррента включает ``PLATEGA_RECURRENT_ENABLED``, но из
админки её не переключить: значения, заданные в окружении, попадают в
``ENV_OVERRIDE_KEYS`` и перебивают БД — запись бы сохранилась, а на живой
процесс не применилась. Поэтому видимость кнопки — отдельный ключ в
``system_settings`` (как ``router_link.*``): он никогда не env-locked и
меняется на ходу, без перезапуска.

Выключатель прячет только вход в подключение. Уже подключённые автопродления
он не трогает: списания по ним продолжаются (так же, как при выключенной
фиче — см. ``monitoring_service``), а строка отмены остаётся доступной тому,
у кого привязка жива. Иначе человек остался бы со списаниями и без кнопки
«отменить» — ровно тот случай, из-за которого у Cashera строка отмены
показывается при выключенной фиче.
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.system_setting import get_setting_value, upsert_system_setting


logger = structlog.get_logger(__name__)

BUTTON_VISIBLE_SETTING_KEY = 'sbp_recurring.button_visible'

_TRUE_VALUES = frozenset({'1', 'true', 'yes', 'on'})
_FALSE_VALUES = frozenset({'0', 'false', 'no', 'off'})


async def is_button_visible(db: AsyncSession) -> bool:
    """Показывать ли кнопку «Автопродление через СБП».

    Ключа нет — показываем: включение фичи не должно требовать ещё и
    отдельного «да» в админке, иначе у всех, кто обновился, кнопка пропала бы.
    """
    raw = await get_setting_value(db, BUTTON_VISIBLE_SETTING_KEY)
    if raw is None:
        return True

    normalized = raw.strip().lower()
    if normalized in _FALSE_VALUES:
        return False
    if normalized in _TRUE_VALUES:
        return True

    logger.warning('Нечитаемое значение выключателя СБП-кнопки — показываем кнопку', raw=raw)
    return True


async def set_button_visible(db: AsyncSession, visible: bool) -> None:
    """Сохраняет состояние выключателя и коммитит: правка идёт из админки."""
    await upsert_system_setting(
        db,
        BUTTON_VISIBLE_SETTING_KEY,
        'true' if visible else 'false',
        description='Показывать кнопку «Автопродление через СБП» в меню автоплатежа',
    )
    await db.commit()
