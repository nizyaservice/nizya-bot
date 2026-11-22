"""Замер времени обработки апдейта — для показателей доступности.

Внешний наблюдатель по этим числам отличает «бот отвечает медленно» от «бот
молчит»: первое видно только по времени обработки, второе — по возрасту
последнего контакта с Telegram (``availability_monitor``).

Мидлвара внешняя (``dp.update.outer_middleware``) и первая в цепочке: мерить
нужно всё, включая работу остальных мидлвар, ведь человек ждёт именно это.
Замер пишется и для упавшего апдейта — отказ после десяти секунд ожидания для
человека такая же задержка, как успех.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.services.availability_monitor import availability_monitor


class UpdateTimingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        started = time.monotonic()
        try:
            return await handler(event, data)
        finally:
            availability_monitor.record_update(time.monotonic() - started)
