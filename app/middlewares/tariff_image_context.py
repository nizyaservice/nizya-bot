"""Сброс картинки выбранного тарифа перед обработкой апдейта.

Картинка тарифа живёт в ContextVar: хендлеры воронки покупки кладут её туда,
а обе ветки отрисовки баннеров читают (см. section_image_service).

Расчёт на то, что aiogram обрабатывает каждый апдейт в отдельной задаче и
значение не переживёт её, не оправдался: контекст доезжал до следующего
апдейта того же пользователя. Выглядело это так — выбрал тариф, нажал
«назад», и на экране со списком тарифов висела картинка выбранного.

Поэтому контекст обнуляется явно, в самом начале каждого апдейта. Хендлер,
которому картинка нужна, выставит её сам.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.services.section_image_service import reset_tariff_image_context


class TariffImageContextMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        reset_tariff_image_context()
        return await handler(event, data)
