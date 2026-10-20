"""Кастомные эмодзи подставляются на выходе, а не в сотне мест вызова.

Эмодзи раскиданы по locales/*.json и по коду клавиатур; переписывать их поштучно
значит ломать подписи кнопок (разметка в них не работает) и ловить расхождения
между пятью локалями. Поэтому подмена живёт в одном месте — middleware сессии,
через который уходит каждый запрос к Bot API:

* текст и подпись оборачиваются в <tg-emoji> (только когда parse_mode = HTML);
* полотно rich-сообщения (InputRichMessage.html) — тоже: главное меню уходит
  через sendRichMessage, и его текст лежит не в text, а в rich_message.html;
* у инлайн-кнопок ведущий эмодзи подписи переезжает в icon_custom_emoji_id.

Выключается флагом CUSTOM_EMOJI_ENABLED или удалением custom_emoji_ids.json.
"""

from __future__ import annotations

from typing import Any

import structlog
from aiogram import Bot
from aiogram.client.default import Default
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.methods import TelegramMethod
from aiogram.types import InlineKeyboardMarkup

from app.utils.custom_emoji import apply_to_html, apply_to_markup, get_custom_emoji_map


logger = structlog.get_logger(__name__)


class CustomEmojiMiddleware(BaseRequestMiddleware):
    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[Any],
        bot: Bot,
        method: TelegramMethod[Any],
    ) -> Any:
        if get_custom_emoji_map():
            try:
                self._decorate(bot, method)
            except Exception:
                # Подмена эмодзи — косметика: сообщение должно уйти в любом случае.
                logger.warning(
                    'Не удалось подставить кастомные эмодзи, отправляем как есть',
                    method=type(method).__name__,
                    exc_info=True,
                )
        return await make_request(bot, method)

    @staticmethod
    def _is_html(bot: Bot, holder: Any) -> bool:
        parse_mode = getattr(holder, 'parse_mode', None)
        if isinstance(parse_mode, Default):
            parse_mode = bot.default.parse_mode
        return isinstance(parse_mode, str) and parse_mode.upper() == 'HTML'

    @classmethod
    def _decorate(cls, bot: Bot, method: TelegramMethod[Any]) -> None:
        for field in ('text', 'caption'):
            value = getattr(method, field, None)
            if isinstance(value, str) and cls._is_html(bot, method):
                setattr(method, field, apply_to_html(value))

        # editMessageMedia: подпись лежит внутри media со своим parse_mode.
        # Типы aiogram заморожены (frozen), поэтому только копия с заменой —
        # присваивание полю роняет запрос через ValidationError.
        media = getattr(method, 'media', None)
        caption = getattr(media, 'caption', None)
        if isinstance(caption, str) and cls._is_html(bot, media):
            method.media = media.model_copy(update={'caption': apply_to_html(caption)})

        # sendRichMessage/editMessageText(rich_message=…): поле html по определению
        # размечено, parse_mode у него нет. Варианты markdown/blocks не трогаем.
        rich = getattr(method, 'rich_message', None)
        rich_html = getattr(rich, 'html', None)
        if isinstance(rich_html, str):
            method.rich_message = rich.model_copy(update={'html': apply_to_html(rich_html)})

        markup = getattr(method, 'reply_markup', None)
        if isinstance(markup, InlineKeyboardMarkup):
            method.reply_markup = apply_to_markup(markup)
