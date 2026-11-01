"""Админка роутер-ссылок: Настройки → Роутер → «Стоимость» и «Сквад».

Две настройки, обе живут в ``system_settings`` и правятся на ходу: цена одной
локации и список сквадов, из которых ссылки вообще продаются. Пока список пуст
или цена нулевая, функция у пользователей не показывается — так оператор,
добавивший сквад и забывший цену, не начнёт раздавать ключи бесплатно.

Имя сквада вводится руками (как просил владелец) и сверяется со списком панели:
опечатка иначе выглядела бы как «функция не работает».
"""

from __future__ import annotations

import html

import structlog
from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.localization.texts import get_texts
from app.services.router_link_service import (
    get_enabled_squad_names,
    get_price_kopeks,
    list_panel_squad_names,
    set_enabled_squad_names,
    set_price_kopeks,
)
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler


logger = structlog.get_logger(__name__)

PANEL_CALLBACK = 'admin_router_link'
_PRICE_CALLBACK = 'admin_router_link_price'
_SQUADS_CALLBACK = 'admin_router_link_squads'
_SQUAD_ADD_CALLBACK = 'admin_router_link_squad_add'
_SQUAD_DELETE_PREFIX = 'admin_router_link_squad_del:'


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


def _panel_keyboard(texts) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.t('ADMIN_ROUTER_PRICE_BUTTON', '💰 Стоимость'), callback_data=_PRICE_CALLBACK
                ),
                InlineKeyboardButton(
                    text=texts.t('ADMIN_ROUTER_SQUADS_BUTTON', '🧩 Сквад'), callback_data=_SQUADS_CALLBACK
                ),
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_submenu_settings')],
        ]
    )


def _squads_keyboard(texts, squads: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=texts.t('ADMIN_ROUTER_SQUAD_DELETE', '🗑 {name}').format(name=name),
                callback_data=f'{_SQUAD_DELETE_PREFIX}{index}',
            )
        ]
        for index, name in enumerate(squads)
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.t('ADMIN_ROUTER_SQUAD_ADD', '➕ Добавить сквад'), callback_data=_SQUAD_ADD_CALLBACK
            )
        ]
    )
    rows.append([InlineKeyboardButton(text=texts.BACK, callback_data=PANEL_CALLBACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _cancel_keyboard(texts, callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.t('ADMIN_ROUTER_CANCEL', '⬅️ Отмена'), callback_data=callback_data)]
        ]
    )


async def _panel_text(texts, db: AsyncSession) -> str:
    price_kopeks = await get_price_kopeks(db)
    squads = await get_enabled_squad_names(db)
    price = texts.format_price(price_kopeks) if price_kopeks > 0 else texts.t('ADMIN_ROUTER_PRICE_UNSET', 'не задана')
    squads_line = ', '.join(html.escape(name) for name in squads) or texts.t('ADMIN_ROUTER_SQUADS_UNSET', 'не заданы')
    status = (
        texts.t('ADMIN_ROUTER_STATUS_ON', '🟢 продаётся')
        if price_kopeks > 0 and squads
        else texts.t('ADMIN_ROUTER_STATUS_OFF', '🔴 выключено (нужны и цена, и сквад)')
    )
    return texts.t(
        'ADMIN_ROUTER_PANEL',
        '📶 <b>Роутер</b>\n\n'
        'Продажа vless-ссылки на одну локацию для прошивки в роутер.\n\n'
        '💰 Стоимость: <b>{price}</b>\n'
        '🧩 Сквады: <b>{squads}</b>\n'
        'Статус: {status}\n\n'
        'Пользователь видит только локации тех сквадов, в которых он состоит.',
    ).format(price=price, squads=squads_line, status=status)


@admin_required
@error_handler
async def show_router_panel(callback: types.CallbackQuery, db_user: User, db: AsyncSession) -> None:
    texts = get_texts(db_user.language)
    await _edit_text_or_replace(callback, await _panel_text(texts, db), _panel_keyboard(texts))
    await callback.answer()


@admin_required
@error_handler
async def start_price_edit(callback: types.CallbackQuery, state: FSMContext, db_user: User, db: AsyncSession) -> None:
    texts = get_texts(db_user.language)
    current = await get_price_kopeks(db)
    await state.set_state(AdminStates.editing_router_link_price)
    await _edit_text_or_replace(
        callback,
        texts.t(
            'ADMIN_ROUTER_PRICE_PROMPT',
            '💰 <b>Стоимость роутер-ссылки</b>\n\n'
            'Текущая: <b>{current}</b>\n\n'
            'Пришлите цену в рублях за одну локацию (например <code>199</code>). '
            '<code>0</code> выключает продажу.',
        ).format(current=texts.format_price(current)),
        _cancel_keyboard(texts, PANEL_CALLBACK),
    )
    await callback.answer()


@admin_required
@error_handler
async def save_price(message: types.Message, state: FSMContext, db_user: User, db: AsyncSession) -> None:
    texts = get_texts(db_user.language)
    raw = (message.text or '').strip().replace(',', '.')
    try:
        rubles = float(raw)
    except ValueError:
        await message.answer(
            texts.t('ADMIN_ROUTER_PRICE_INVALID', 'Нужно число в рублях, например 199 или 199.50.'),
            reply_markup=_cancel_keyboard(texts, PANEL_CALLBACK),
        )
        return
    if rubles < 0:
        await message.answer(
            texts.t('ADMIN_ROUTER_PRICE_INVALID', 'Нужно число в рублях, например 199 или 199.50.'),
            reply_markup=_cancel_keyboard(texts, PANEL_CALLBACK),
        )
        return

    await set_price_kopeks(db, round(rubles * 100))
    await state.clear()
    logger.info('Цена роутер-ссылки изменена', admin_id=db_user.id, price_kopeks=round(rubles * 100))
    await message.answer(await _panel_text(texts, db), reply_markup=_panel_keyboard(texts), parse_mode='HTML')


@admin_required
@error_handler
async def show_squads(callback: types.CallbackQuery, db_user: User, db: AsyncSession) -> None:
    texts = get_texts(db_user.language)
    squads = await get_enabled_squad_names(db)
    await _edit_text_or_replace(
        callback,
        texts.t(
            'ADMIN_ROUTER_SQUADS_SCREEN',
            '🧩 <b>Сквады роутер-ссылок</b>\n\n'
            'Ссылки продаются только из этих сквадов, и только тем, кто в них состоит.\n\n'
            'Сейчас включены: <b>{squads}</b>',
        ).format(squads=', '.join(html.escape(name) for name in squads) or '—'),
        _squads_keyboard(texts, squads),
    )
    await callback.answer()


@admin_required
@error_handler
async def start_squad_add(callback: types.CallbackQuery, state: FSMContext, db_user: User, db: AsyncSession) -> None:
    texts = get_texts(db_user.language)
    try:
        available = await list_panel_squad_names()
    except Exception as error:
        logger.warning('Не удалось получить список сквадов панели', error=str(error))
        available = []

    hint = (
        texts.t('ADMIN_ROUTER_SQUAD_AVAILABLE', 'Сквады панели: {names}').format(
            names=', '.join(html.escape(name) for name in available)
        )
        if available
        else texts.t('ADMIN_ROUTER_SQUAD_NO_PANEL', 'Список сквадов панели недоступен — имя сверю при сохранении.')
    )
    await state.set_state(AdminStates.editing_router_link_squad)
    await _edit_text_or_replace(
        callback,
        texts.t(
            'ADMIN_ROUTER_SQUAD_PROMPT',
            '🧩 <b>Добавление сквада</b>\n\n'
            'Пришлите имя сквада одним сообщением (например <code>game-premium</code>).\n\n{hint}',
        ).format(hint=hint),
        _cancel_keyboard(texts, _SQUADS_CALLBACK),
    )
    await callback.answer()


@admin_required
@error_handler
async def save_squad(message: types.Message, state: FSMContext, db_user: User, db: AsyncSession) -> None:
    texts = get_texts(db_user.language)
    name = (message.text or '').strip()
    if not name:
        await message.answer(
            texts.t('ADMIN_ROUTER_SQUAD_INVALID', 'Пришлите имя сквада текстом.'),
            reply_markup=_cancel_keyboard(texts, _SQUADS_CALLBACK),
        )
        return

    try:
        available = await list_panel_squad_names()
    except Exception as error:
        logger.warning('Не удалось сверить сквад со панелью', error=str(error))
        available = []

    if available and name.casefold() not in {item.casefold() for item in available}:
        await message.answer(
            texts.t(
                'ADMIN_ROUTER_SQUAD_UNKNOWN',
                'В панели нет сквада <b>{name}</b>. Есть: {names}',
            ).format(name=html.escape(name), names=', '.join(html.escape(item) for item in available)),
            reply_markup=_cancel_keyboard(texts, _SQUADS_CALLBACK),
            parse_mode='HTML',
        )
        return

    squads = await get_enabled_squad_names(db)
    if name.casefold() not in {item.casefold() for item in squads}:
        squads.append(name)
        await set_enabled_squad_names(db, squads)
        logger.info('Сквад роутер-ссылок добавлен', admin_id=db_user.id, squad=name)

    await state.clear()
    await message.answer(
        texts.t(
            'ADMIN_ROUTER_SQUADS_SCREEN',
            '🧩 <b>Сквады роутер-ссылок</b>\n\n'
            'Ссылки продаются только из этих сквадов, и только тем, кто в них состоит.\n\n'
            'Сейчас включены: <b>{squads}</b>',
        ).format(squads=', '.join(html.escape(item) for item in squads) or '—'),
        reply_markup=_squads_keyboard(texts, squads),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def delete_squad(callback: types.CallbackQuery, db_user: User, db: AsyncSession) -> None:
    """Удаление по индексу: имя сквада в ``callback_data`` не влезает по длине."""
    texts = get_texts(db_user.language)
    squads = await get_enabled_squad_names(db)
    try:
        index = int((callback.data or '').split(':', 1)[1])
    except (IndexError, ValueError):
        await callback.answer(texts.t('ADMIN_ROUTER_SQUAD_INVALID', 'Пришлите имя сквада текстом.'), show_alert=True)
        return

    if 0 <= index < len(squads):
        removed = squads.pop(index)
        await set_enabled_squad_names(db, squads)
        logger.info('Сквад роутер-ссылок удалён', admin_id=db_user.id, squad=removed)

    await show_squads(callback, db_user, db)


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(show_router_panel, F.data == PANEL_CALLBACK)
    dp.callback_query.register(start_price_edit, F.data == _PRICE_CALLBACK)
    dp.callback_query.register(show_squads, F.data == _SQUADS_CALLBACK)
    dp.callback_query.register(start_squad_add, F.data == _SQUAD_ADD_CALLBACK)
    dp.callback_query.register(delete_squad, F.data.startswith(_SQUAD_DELETE_PREFIX))

    dp.message.register(save_price, AdminStates.editing_router_link_price)
    dp.message.register(save_squad, AdminStates.editing_router_link_squad)
