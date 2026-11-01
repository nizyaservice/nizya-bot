"""Покупка vless-ссылки для роутера: экраны выбора локации, оплаты и показа.

Ссылка появляется в сообщении только после успешного списания. До оплаты
хендлер не запрашивает её у панели вовсе, и в ``callback_data`` ездит только
UUID хоста — то есть в трафике бота ключа до оплаты просто нет. Повторный
показ уже оплаченной локации бесплатен: право хранится в базе, ключ каждый
раз берётся из панели заново.
"""

from __future__ import annotations

import html

import structlog
from aiogram import Dispatcher, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.keyboards.inline import get_insufficient_balance_keyboard
from app.localization.texts import get_texts
from app.services.router_link_service import (
    Availability,
    RouterLocation,
    get_available_locations,
    get_price_kopeks,
    purchase_location,
    purchased_host_uuids,
    resolve_link,
)
from app.services.section_image_service import SECTION_IMAGE_MY_SUBSCRIPTIONS, get_section_media_kwargs
from app.utils.decorators import error_handler
from app.utils.photo_message import edit_or_answer_photo


logger = structlog.get_logger(__name__)

ROUTER_LINK_CALLBACK = 'router_link'
_BUY_PREFIX = 'router_link_buy:'
_PAY_PREFIX = 'router_link_pay:'
_SHOW_PREFIX = 'router_link_show:'

#: Причина пустого списка → ключ пояснения. Человеку важно понять, это у него
#: тариф не тот или у сервиса что-то сломалось.
_REASON_TEXTS = {
    'disabled': (
        'ROUTER_LINK_UNAVAILABLE',
        '📶 <b>Ссылка для роутера</b>\n\nСейчас эта услуга не продаётся. Напишите в поддержку, если она вам нужна.',
    ),
    'no_subscription': (
        'ROUTER_LINK_NEEDS_SUBSCRIPTION',
        (
            '📶 <b>Ссылка для роутера</b>\n\n'
            'Ссылка выдаётся из вашей подписки, поэтому нужна действующая подписка. '
            'Оформите её — и локации появятся здесь.'
        ),
    ),
    'no_squads': (
        'ROUTER_LINK_NOT_IN_TARIFF',
        (
            '📶 <b>Ссылка для роутера</b>\n\n'
            'Для вашего тарифа роутер-локации не предусмотрены. Поддержка подскажет, какой тариф их открывает.'
        ),
    ),
    'panel_error': (
        'ROUTER_LINK_PANEL_ERROR',
        '📶 <b>Ссылка для роутера</b>\n\nНе удалось получить список локаций. Попробуйте ещё раз через минуту.',
    ),
}


def _back_keyboard(texts) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts.BACK, callback_data='menu_subscription')]]
    )


def _list_keyboard(texts, availability: Availability, purchased: set[str], price_kopeks: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for location in availability.locations:
        if location.host_uuid in purchased:
            text = texts.t('ROUTER_LINK_LOCATION_PAID', '✅ {name}').format(name=location.remark)
            callback = f'{_SHOW_PREFIX}{location.host_uuid}'
        else:
            text = texts.t('ROUTER_LINK_LOCATION_BUY', '{name} — {price}').format(
                name=location.remark, price=texts.format_price(price_kopeks)
            )
            callback = f'{_BUY_PREFIX}{location.host_uuid}'
        rows.append([InlineKeyboardButton(text=text, callback_data=callback)])
    rows.append([InlineKeyboardButton(text=texts.BACK, callback_data='menu_subscription')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _link_message(texts, location: RouterLocation, link: str, *, just_paid: bool) -> str:
    """Экран с ключом. Ссылка экранируется: в ней ``&`` из query-параметров."""
    header = (
        texts.t('ROUTER_LINK_PAID_TITLE', '✅ <b>Оплачено. Ссылка для роутера</b>')
        if just_paid
        else texts.t('ROUTER_LINK_SHOW_TITLE', '📶 <b>Ссылка для роутера</b>')
    )
    return texts.t(
        'ROUTER_LINK_RESULT',
        '{header}\n\n'
        '📍 Локация: <b>{name}</b>\n\n'
        '<blockquote expandable><code>{link}</code></blockquote>\n\n'
        'Скопируйте ссылку и добавьте её в VPN-клиент роутера как vless-конфигурацию. '
        'Ссылка остаётся доступной в этом разделе, пока действует подписка.',
    ).format(header=header, name=html.escape(location.remark), link=html.escape(link))


@error_handler
async def show_router_link_menu(callback: types.CallbackQuery, db_user: User, db: AsyncSession) -> None:
    """Список локаций: имена и цена, без адресов и без ключей."""
    texts = get_texts(db_user.language)
    availability = await get_available_locations(db, db_user)

    if not availability.locations:
        key, default = _REASON_TEXTS.get(availability.reason, _REASON_TEXTS['disabled'])
        await edit_or_answer_photo(
            callback=callback,
            caption=texts.t(key, default),
            keyboard=_back_keyboard(texts),
            parse_mode='HTML',
            **await get_section_media_kwargs(db, SECTION_IMAGE_MY_SUBSCRIPTIONS, language=db_user.language),
        )
        await callback.answer()
        return

    price_kopeks = await get_price_kopeks(db)
    purchased = await purchased_host_uuids(db, db_user.id)
    caption = texts.t(
        'ROUTER_LINK_MENU',
        '📶 <b>Ссылка для роутера</b>\n\n'
        'Выберите локацию — после оплаты бот покажет vless-ссылку, которую можно прописать в роутер.\n\n'
        '💰 Стоимость локации: <b>{price}</b>\n'
        '💳 Ваш баланс: {balance}',
    ).format(
        price=texts.format_price(price_kopeks),
        balance=texts.format_price(db_user.balance_kopeks or 0),
    )
    await edit_or_answer_photo(
        callback=callback,
        caption=caption,
        keyboard=_list_keyboard(texts, availability, purchased, price_kopeks),
        parse_mode='HTML',
        **await get_section_media_kwargs(db, SECTION_IMAGE_MY_SUBSCRIPTIONS, language=db_user.language),
    )
    await callback.answer()


def _find_location(availability: Availability, host_uuid: str) -> RouterLocation | None:
    return next((item for item in availability.locations if item.host_uuid == host_uuid), None)


@error_handler
async def confirm_router_link(callback: types.CallbackQuery, db_user: User, db: AsyncSession) -> None:
    """Подтверждение покупки: локация, цена, баланс. Ключа здесь ещё нет."""
    texts = get_texts(db_user.language)
    host_uuid = (callback.data or '').split(':', 1)[-1]

    availability = await get_available_locations(db, db_user)
    location = _find_location(availability, host_uuid)
    if location is None:
        await callback.answer(
            texts.t('ROUTER_LINK_GONE_ALERT', 'Эта локация больше недоступна'),
            show_alert=True,
        )
        await show_router_link_menu(callback, db_user, db)
        return

    price_kopeks = await get_price_kopeks(db)
    balance = db_user.balance_kopeks or 0
    caption = texts.t(
        'ROUTER_LINK_CONFIRM',
        '📶 <b>Ссылка для роутера</b>\n\n'
        '📍 Локация: <b>{name}</b>\n'
        '💰 К оплате: <b>{price}</b>\n\n'
        '💳 Ваш баланс: {balance}\n'
        'После оплаты: {after}\n\n'
        'Ссылка откроется сразу после списания.',
    ).format(
        name=html.escape(location.remark),
        price=texts.format_price(price_kopeks),
        balance=texts.format_price(balance),
        after=texts.format_price(max(0, balance - price_kopeks)),
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.t('ROUTER_LINK_PAY_BUTTON', '💎 Оплатить {price}').format(
                        price=texts.format_price(price_kopeks)
                    ),
                    callback_data=f'{_PAY_PREFIX}{location.host_uuid}',
                )
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data=ROUTER_LINK_CALLBACK)],
        ]
    )
    await edit_or_answer_photo(
        callback=callback,
        caption=caption,
        keyboard=keyboard,
        parse_mode='HTML',
        **await get_section_media_kwargs(db, SECTION_IMAGE_MY_SUBSCRIPTIONS, language=db_user.language),
    )
    await callback.answer()


@error_handler
async def pay_router_link(callback: types.CallbackQuery, db_user: User, db: AsyncSession) -> None:
    """Списывает деньги и показывает ключ. Ключ запрашивается только здесь."""
    texts = get_texts(db_user.language)
    host_uuid = (callback.data or '').split(':', 1)[-1]

    outcome = await purchase_location(db, db_user, host_uuid)

    if outcome.status in {'unavailable', 'disabled'}:
        await callback.answer(
            texts.t('ROUTER_LINK_GONE_ALERT', 'Эта локация больше недоступна'),
            show_alert=True,
        )
        await show_router_link_menu(callback, db_user, db)
        return

    if outcome.status == 'insufficient':
        await edit_or_answer_photo(
            callback=callback,
            caption=texts.t(
                'ROUTER_LINK_INSUFFICIENT',
                '💰 <b>Недостаточно средств</b>\n\n'
                'Для покупки ссылки нужно <b>{price}</b>, не хватает {missing}.\n'
                'Пополните баланс и вернитесь к выбору локации.',
            ).format(
                price=texts.format_price(outcome.price_kopeks),
                missing=texts.format_price(outcome.missing_kopeks),
            ),
            keyboard=get_insufficient_balance_keyboard(
                db_user.language,
                resume_callback=ROUTER_LINK_CALLBACK,
                amount_kopeks=outcome.missing_kopeks,
                resume_text=texts.t('ROUTER_LINK_RESUME_BUTTON', '📶 Вернуться к локациям'),
            ),
            parse_mode='HTML',
            **await get_section_media_kwargs(db, SECTION_IMAGE_MY_SUBSCRIPTIONS, language=db_user.language),
        )
        await callback.answer()
        return

    if outcome.status == 'charge_failed':
        await callback.answer(
            texts.t('ROUTER_LINK_CHARGE_FAILED_ALERT', 'Не удалось списать средства, попробуйте ещё раз'),
            show_alert=True,
        )
        return

    if outcome.status == 'link_missing' or outcome.link is None:
        await edit_or_answer_photo(
            callback=callback,
            caption=texts.t(
                'ROUTER_LINK_NOT_READY',
                '📶 <b>Доступ оплачен</b>\n\n'
                'Ключ этой локации сейчас не отдаётся панелью. Доступ уже закреплён за вами: '
                'откройте раздел чуть позже — ссылка появится. Если не появится, напишите в поддержку.',
            ),
            keyboard=_back_keyboard(texts),
            parse_mode='HTML',
            **await get_section_media_kwargs(db, SECTION_IMAGE_MY_SUBSCRIPTIONS, language=db_user.language),
        )
        await callback.answer()
        return

    await edit_or_answer_photo(
        callback=callback,
        caption=_link_message(texts, outcome.location, outcome.link, just_paid=outcome.status == 'ok'),
        keyboard=_back_keyboard(texts),
        parse_mode='HTML',
        **await get_section_media_kwargs(db, SECTION_IMAGE_MY_SUBSCRIPTIONS, language=db_user.language),
    )
    await callback.answer()


@error_handler
async def show_router_link(callback: types.CallbackQuery, db_user: User, db: AsyncSession) -> None:
    """Повторный показ уже оплаченной локации — бесплатно."""
    texts = get_texts(db_user.language)
    host_uuid = (callback.data or '').split(':', 1)[-1]

    if host_uuid not in await purchased_host_uuids(db, db_user.id):
        await confirm_router_link(callback, db_user, db)
        return

    availability = await get_available_locations(db, db_user)
    location = _find_location(availability, host_uuid)
    link = await resolve_link(db, db_user, location) if location else None

    if location is None or link is None:
        await edit_or_answer_photo(
            callback=callback,
            caption=texts.t(
                'ROUTER_LINK_NOT_READY',
                '📶 <b>Доступ оплачен</b>\n\n'
                'Ключ этой локации сейчас не отдаётся панелью. Доступ уже закреплён за вами: '
                'откройте раздел чуть позже — ссылка появится. Если не появится, напишите в поддержку.',
            ),
            keyboard=_back_keyboard(texts),
            parse_mode='HTML',
            **await get_section_media_kwargs(db, SECTION_IMAGE_MY_SUBSCRIPTIONS, language=db_user.language),
        )
        await callback.answer()
        return

    await edit_or_answer_photo(
        callback=callback,
        caption=_link_message(texts, location, link, just_paid=False),
        keyboard=_back_keyboard(texts),
        parse_mode='HTML',
        **await get_section_media_kwargs(db, SECTION_IMAGE_MY_SUBSCRIPTIONS, language=db_user.language),
    )
    await callback.answer()


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(show_router_link_menu, F.data == ROUTER_LINK_CALLBACK)
    dp.callback_query.register(confirm_router_link, F.data.startswith(_BUY_PREFIX))
    dp.callback_query.register(pay_router_link, F.data.startswith(_PAY_PREFIX))
    dp.callback_query.register(show_router_link, F.data.startswith(_SHOW_PREFIX))
