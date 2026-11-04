"""Кнопка из сообщения рассылки: экран назначения уходит новым сообщением.

Мидлвара снимает метку ``bc:`` с ``callback_data`` и на время обработки
апдейта включает флаг «отправить экран новым сообщением»
(см. ``app/utils/screen_delivery.py``). Сами хендлеры разделов остаются
зарегистрированными на свои обычные callback'и — ни нового маршрута, ни
правок в чужих флоу.

Поэтому мидлвара обязана быть **outer**: она переписывает ``callback_data``
до того, как dispatcher сверит его с фильтрами. Будь она inner, нажатие
``bc:menu_promocode`` не попало бы ни в один фильтр и не сделало бы ничего.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject

from app.utils.screen_delivery import force_new_screen, unwrap_broadcast_callback


class BroadcastButtonMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)

        original = unwrap_broadcast_callback(event.data)
        if original is None:
            return await handler(event, data)

        patched = event.model_copy(update={'data': original})
        # model_copy переносит приватные поля pydantic, но контекст бота —
        # то, без чего экран не уедет вообще, поэтому проставляем явно.
        bot = data.get('bot')
        if bot is not None:
            patched = patched.as_(bot)

        with force_new_screen():
            return await handler(patched, data)
