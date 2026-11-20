"""Token-authoritative bot identity helpers."""

from __future__ import annotations

import asyncio

import structlog
from aiogram.exceptions import TelegramNetworkError

from app.config import settings


logger = structlog.get_logger(__name__)

#: Паузы между попытками get_me() на старте. Сеть к api.telegram.org умеет
#: отваливаться на десятки секунд, а в `.env` username обычно не задан — без
#: повторов одна заминка оставляла бы ссылки на бота пустыми до перезапуска.
GET_ME_RETRY_DELAYS = (2, 5, 10)


async def _get_me_with_retries(bot):
    """Спросить get_me(), переживая короткий сбой сети. ``None`` — не вышло."""
    for attempt in range(len(GET_ME_RETRY_DELAYS) + 1):
        try:
            return await bot.get_me()
        except TelegramNetworkError as error:
            if attempt == len(GET_ME_RETRY_DELAYS):
                logger.warning(
                    'Telegram не ответил на get_me() за все попытки; BOT_USERNAME остаётся из конфига',
                    attempts=attempt + 1,
                    error=str(error),
                )
                return None
            delay = GET_ME_RETRY_DELAYS[attempt]
            logger.warning('Telegram не ответил на get_me(), повторим', delay_seconds=delay, error=str(error))
            await asyncio.sleep(delay)
        except Exception as error:
            # Неверный токен и прочие отказы Telegram повторять незачем: ответ не
            # изменится. Полноценно об этом скажет первый же getUpdates.
            logger.warning('get_me() отклонён Telegram; используем BOT_USERNAME из конфига', error=str(error))
            return None
    return None


async def sync_bot_username(bot) -> None:
    """Sync settings.BOT_USERNAME from the live bot (token-authoritative).

    Telegram derives the username from BOT_TOKEN, so get_me() is the source of truth. A
    manually-set BOT_USERNAME env survives a token/bot swap and goes stale — which made
    gift-claim links keep pointing at the OLD bot (Telegram bug #650370) while referral
    links (which call get_me() live) were already correct. The bot and the cabinet run in
    one process sharing this settings instance, so syncing here fixes every
    get_bot_username() caller.

    Best-effort by design: это единственный get_me() на старте, и уронить им
    запуск нельзя — сбой сети оставляет значение из конфига, а бот поднимается
    и начинает работать, как только Telegram ответит.
    """
    me = await _get_me_with_retries(bot)
    if me is None:
        return
    if me.username and me.username != settings.BOT_USERNAME:
        logger.info('Синхронизирован BOT_USERNAME из get_me()', old=settings.BOT_USERNAME, new=me.username)
        settings.BOT_USERNAME = me.username
