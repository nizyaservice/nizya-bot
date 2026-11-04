"""Админка: Настройки → «Продление через СБП» — видимость кнопки в боте.

Один выключатель: показывать ли пользователю «⚡ Автопродление через СБП» в
«Моя подписка» → «Автоплатёж». Он живёт в ``system_settings``, а не в
``PLATEGA_RECURRENT_ENABLED``, потому что значение из окружения перебивает БД
и такой переключатель молча не применялся бы (см. ``sbp_recurring_settings``).

Экран показывает оба слоя — выключатель и состояние самого шлюза, — иначе
оператор, у которого Platega-рекуррент выключен в ``.env``, видел бы «кнопка
показана» и не понимал, почему её нет.
"""

from __future__ import annotations

import structlog
from aiogram import Dispatcher, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.localization.texts import get_texts
from app.services.sbp_recurring_settings import is_button_visible, set_button_visible
from app.utils.decorators import admin_required, error_handler


logger = structlog.get_logger(__name__)

PANEL_CALLBACK = 'admin_sbp_recurring'
_TOGGLE_CALLBACK = 'admin_sbp_recurring_toggle'


async def _edit_text_or_replace(callback: types.CallbackQuery, text: str, keyboard: InlineKeyboardMarkup) -> None:
    """Экран админки — текстовый: под баннером его не отредактировать."""
    if callback.message.photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(text, reply_markup=keyboard, parse_mode='HTML')
        return
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode='HTML')


def _panel_keyboard(texts, visible: bool) -> InlineKeyboardMarkup:
    toggle_text = (
        texts.t('ADMIN_SBP_RECURRING_HIDE_BUTTON', '🙈 Скрыть кнопку')
        if visible
        else texts.t('ADMIN_SBP_RECURRING_SHOW_BUTTON', '👁 Показать кнопку')
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data=_TOGGLE_CALLBACK)],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_submenu_settings')],
        ]
    )


def _panel_text(texts, visible: bool) -> str:
    gateway_on = settings.is_platega_recurrent_enabled()

    button_state = (
        texts.t('ADMIN_SBP_RECURRING_BUTTON_ON', '🟢 показана')
        if visible
        else texts.t('ADMIN_SBP_RECURRING_BUTTON_OFF', '🔴 скрыта')
    )
    gateway_state = (
        texts.t('ADMIN_SBP_RECURRING_GATEWAY_ON', '🟢 включён')
        if gateway_on
        else texts.t('ADMIN_SBP_RECURRING_GATEWAY_OFF', '🔴 выключен (PLATEGA_RECURRENT_ENABLED)')
    )
    if not gateway_on:
        status = texts.t('ADMIN_SBP_RECURRING_STATUS_NO_GATEWAY', '🔴 кнопки нет: выключен шлюз')
    elif visible:
        status = texts.t('ADMIN_SBP_RECURRING_STATUS_VISIBLE', '🟢 пользователи видят кнопку')
    else:
        status = texts.t('ADMIN_SBP_RECURRING_STATUS_HIDDEN', '🔴 кнопка скрыта')

    return texts.t(
        'ADMIN_SBP_RECURRING_PANEL',
        '⚡ <b>Продление через СБП</b>\n\n'
        'Кнопка «Автопродление через СБП» в «Моя подписка» → «Автоплатёж».\n\n'
        '👁 Кнопка: <b>{button}</b>\n'
        '🔌 Platega-рекуррент: <b>{gateway}</b>\n'
        'Статус: {status}\n\n'
        'Выключатель прячет только подключение. Уже подключённые автопродления '
        'продолжают списываться, и тем, у кого привязка жива, кнопка остаётся '
        'видна — иначе отменить списания было бы нечем.',
    ).format(button=button_state, gateway=gateway_state, status=status)


@admin_required
@error_handler
async def show_sbp_recurring_panel(callback: types.CallbackQuery, db_user: User, db: AsyncSession) -> None:
    texts = get_texts(db_user.language)
    visible = await is_button_visible(db)
    await _edit_text_or_replace(callback, _panel_text(texts, visible), _panel_keyboard(texts, visible))
    await callback.answer()


@admin_required
@error_handler
async def toggle_sbp_recurring_button(callback: types.CallbackQuery, db_user: User, db: AsyncSession) -> None:
    texts = get_texts(db_user.language)
    visible = not await is_button_visible(db)
    await set_button_visible(db, visible)
    logger.info('Видимость кнопки СБП-автопродления изменена', admin_id=db_user.id, visible=visible)

    saved = (
        texts.t('ADMIN_SBP_RECURRING_SAVED_ON', '✅ Кнопка показана')
        if visible
        else texts.t('ADMIN_SBP_RECURRING_SAVED_OFF', '✅ Кнопка скрыта')
    )
    await _edit_text_or_replace(callback, _panel_text(texts, visible), _panel_keyboard(texts, visible))
    await callback.answer(saved)


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(show_sbp_recurring_panel, F.data == PANEL_CALLBACK)
    dp.callback_query.register(toggle_sbp_recurring_button, F.data == _TOGGLE_CALLBACK)
