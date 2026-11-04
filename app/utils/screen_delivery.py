"""Экран назначения новым сообщением вместо правки текущего.

Нужно кнопкам в сообщении рассылки: человек нажал «🎫 Промокод» — экран
промокода должен приехать НОВЫМ сообщением, а сама рассылка остаться в чате
(иначе она либо перезаписывалась экраном, либо удалялась — у рассылки с
картинкой ``edit_text`` удаляет сообщение и шлёт новое).

Во всех остальных флоу бот по-прежнему правит текущее сообщение, поэтому это
не настройка и не ветка в хендлерах, а ContextVar на время обработки одного
апдейта: его ставит мидлвара, увидев метку в ``callback_data``, а читают две
точки отрисовки экранов — ``photo_message.edit_or_answer_photo`` и патченный
``Message.edit_text`` (``message_patch._edit_with_photo``). Хендлеры разделов
не меняются вовсе: ни один из них не знает, откуда пришло нажатие.

Метка живёт именно в ``callback_data``, потому что по апдейту узнать «это
сообщение рассылки» нельзя: id рассылочных сообщений нигде не хранятся — их
столько же, сколько получателей.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar


#: Префикс callback'а кнопки рассылки. Короткий: он съедает лимит Telegram.
BROADCAST_CALLBACK_PREFIX = 'bc:'

#: Лимит Telegram на callback_data — 64 байта.
CALLBACK_DATA_LIMIT = 64

_FORCE_NEW_SCREEN: ContextVar[bool] = ContextVar('force_new_screen', default=False)


def force_new_screen_enabled() -> bool:
    """Нужно ли отправить экран новым сообщением вместо правки текущего."""
    return _FORCE_NEW_SCREEN.get()


@contextmanager
def force_new_screen() -> Iterator[None]:
    """Включает отправку новым сообщением на время обработки апдейта."""
    token = _FORCE_NEW_SCREEN.set(True)
    try:
        yield
    finally:
        _FORCE_NEW_SCREEN.reset(token)


def wrap_broadcast_callback(callback_data: str | None) -> str | None:
    """Помечает callback кнопки рассылки префиксом ``bc:``.

    Если с префиксом не влезаем в лимит Telegram, оставляем как было: кнопка
    важнее, чем способ доставки экрана (касается только произвольных кнопок
    из кабинета — у штатных callback'ов запас большой).
    """
    if not callback_data or callback_data.startswith(BROADCAST_CALLBACK_PREFIX):
        return callback_data

    wrapped = f'{BROADCAST_CALLBACK_PREFIX}{callback_data}'
    if len(wrapped.encode('utf-8')) > CALLBACK_DATA_LIMIT:
        return callback_data
    return wrapped


def unwrap_broadcast_callback(callback_data: str | None) -> str | None:
    """Исходный callback без метки рассылки; ``None`` — метки не было."""
    if not callback_data or not callback_data.startswith(BROADCAST_CALLBACK_PREFIX):
        return None
    return callback_data[len(BROADCAST_CALLBACK_PREFIX) :]
