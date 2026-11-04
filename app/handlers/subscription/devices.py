import html as html_mod
import math
from datetime import UTC, datetime

from aiogram import types
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.transaction import create_transaction
from app.database.crud.user import lock_user_for_pricing, subtract_user_balance
from app.database.crud.user_device_alias import (
    ALIAS_MAX_LENGTH,
    attach_aliases_to_devices,
    delete_alias,
    get_alias,
    get_aliases_for_user,
    normalize_alias,
    upsert_alias,
)
from app.database.models import Subscription, TransactionType, User
from app.keyboards.inline import (
    get_app_selection_keyboard,
    get_back_keyboard,
    get_change_devices_keyboard,
    get_confirm_change_devices_keyboard,
    get_connection_guide_keyboard,
    get_device_card_keyboard,
    get_device_delete_confirm_keyboard,
    get_device_management_help_keyboard,
    get_devices_management_keyboard,
    get_devices_reset_all_confirm_keyboard,
    get_insufficient_balance_keyboard,
    get_specific_app_keyboard,
)
from app.localization.texts import get_texts
from app.services.pricing_engine import PricingEngine
from app.services.remnawave_service import RemnaWaveService
from app.services.section_image_service import get_section_media_kwargs
from app.services.subscription_service import SubscriptionService
from app.services.user_cart_service import user_cart_service
from app.states import SubscriptionStates
from app.utils.device_labels import device_display_name, shorten_device_label
from app.utils.formatters import format_datetime
from app.utils.legacy_subscription import is_legacy_subscription
from app.utils.pagination import paginate_list
from app.utils.photo_message import edit_or_answer_photo
from app.utils.pricing_utils import (
    apply_percentage_discount,
    calculate_prorated_price,
)
from app.utils.subscription_utils import (
    get_display_subscription_link,
)

from .common import (
    _get_period_hint_from_subscription,
    get_apps_for_platform_async,
    get_device_name,
    logger,
    render_guide_blocks,
)
from .countries import _get_available_countries


async def _resolve_subscription(callback, db_user, db, state=None):
    """Resolve subscription — delegates to shared resolve_subscription_from_context."""
    from .common import resolve_subscription_from_context

    return await resolve_subscription_from_context(callback, db_user, db, state)


def _get_panel_user_id(subscription, db_user):
    """Get the numeric Remnawave user id for device operations.

    Multi-tariff: each subscription owns its OWN panel user, so use the
    subscription's id and do NOT fall back to the user-level id — the
    fallback would read/enforce HWID devices against another tariff's panel
    user, making the device limit look shared across tariffs (баг с общим
    лимитом «по наименьшему тарифу»). Single-tariff: one panel user per user,
    fall back to it as before.
    """
    if subscription is not None and settings.is_multi_tariff_enabled():
        return getattr(subscription, 'remnawave_id', None)
    return getattr(subscription, 'remnawave_id', None) or db_user.remnawave_id


# Размер страницы списка устройств. Раньше «5» было вписано в четыре места
# (список, карточка, переименование, удаление), и разъехаться им ничто не мешало,
# а индекс из callback'а указал бы тогда на чужое устройство.
DEVICES_PER_PAGE = 5


def _subscription_back_callback(sub_id: int | None) -> str:
    """«Назад» с экранов устройств — на экран подписки, а не в главное меню.

    Терминальные экраны раздела (нет устройств, все сброшены) отдавали
    `back_to_menu` и выбрасывали человека в главное меню из середины флоу.
    """
    return f'sm:{sub_id}' if settings.is_multi_tariff_enabled() and sub_id else 'menu_subscription'


async def get_current_devices_detailed(db_user: User, subscription=None) -> dict:
    try:
        panel_user_id = _get_panel_user_id(subscription, db_user) if subscription else db_user.remnawave_id
        if not panel_user_id:
            return {'count': 0, 'devices': []}

        from app.services.remnawave_service import RemnaWaveService

        service = RemnaWaveService()

        async with service.get_api_client() as api:
            devices_info = await api.get_user_devices(panel_user_id)

            if isinstance(devices_info, dict):
                total_devices = devices_info.get('total', 0)
                devices_list = devices_info.get('devices', [])

                return {'count': total_devices, 'devices': devices_list[:5]}
            return {'count': 0, 'devices': []}

    except Exception as e:
        logger.error('Ошибка получения детальной информации об устройствах', error=e)
        return {'count': 0, 'devices': []}


async def get_servers_display_names(squad_uuids: list[str]) -> str:
    if not squad_uuids:
        return 'Нет серверов'

    try:
        from app.database.crud.server_squad import get_server_squad_by_uuid
        from app.database.database import AsyncSessionLocal

        server_names = []

        async with AsyncSessionLocal() as db:
            for uuid in squad_uuids:
                server = await get_server_squad_by_uuid(db, uuid)
                if server:
                    server_names.append(html_mod.escape(server.display_name))
                    logger.debug('Найден сервер в БД', uuid=uuid, display_name=server.display_name)
                else:
                    logger.warning('Сервер с UUID не найден в БД', uuid=uuid)

        if not server_names:
            countries = await _get_available_countries()
            for uuid in squad_uuids:
                for country in countries:
                    if country['uuid'] == uuid:
                        server_names.append(html_mod.escape(country['name']))
                        logger.debug('Найден сервер в кэше', uuid=uuid, country=country['name'])
                        break

        if not server_names:
            if len(squad_uuids) == 1:
                return '🎯 Тестовый сервер'
            return f'{len(squad_uuids)} стран'

        if len(server_names) > 6:
            displayed = ', '.join(server_names[:6])
            remaining = len(server_names) - 6
            return f'{displayed} и ещё {remaining}'
        return ', '.join(server_names)

    except Exception as e:
        logger.error('Ошибка получения названий серверов', error=e)
        if len(squad_uuids) == 1:
            return '🎯 Тестовый сервер'
        return f'{len(squad_uuids)} стран'


async def get_current_devices_count(db_user: User, subscription=None) -> str:
    try:
        panel_user_id = _get_panel_user_id(subscription, db_user) if subscription else db_user.remnawave_id
        if not panel_user_id:
            return '—'

        from app.services.remnawave_service import RemnaWaveService

        service = RemnaWaveService()

        async with service.get_api_client() as api:
            devices_info = await api.get_user_devices(panel_user_id)

            if isinstance(devices_info, dict):
                total_devices = devices_info.get('total', 0)
                return str(total_devices)
            return '—'

    except Exception as e:
        logger.error('Ошибка получения количества устройств', error=e)
        return '—'


async def handle_change_devices(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    texts = get_texts(db_user.language)
    subscription, sub_id = await _resolve_subscription(callback, db_user, db, state)
    if subscription is None:
        return

    if not subscription or subscription.is_trial:
        await callback.answer(
            texts.t('PAID_FEATURE_ONLY', '⚠️ Эта функция доступна только для платных подписок'),
            show_alert=True,
        )
        return

    if is_legacy_subscription(subscription):
        # Старая подписка (без тарифа при включённых тарифах): докупок нет, сперва переход на тариф.
        await callback.answer(
            texts.t(
                'LEGACY_ADDONS_UNAVAILABLE',
                '⚠️ Сначала перейдите на тариф — докупки для этой подписки недоступны',
            ),
            show_alert=True,
        )
        return

    # Проверяем тариф подписки
    tariff = None
    if subscription.tariff_id:
        from app.database.crud.tariff import get_tariff_by_id

        tariff = await get_tariff_by_id(db, subscription.tariff_id)

    # Для тарифов - проверяем разрешено ли изменение устройств
    tariff_device_price = getattr(tariff, 'device_price_kopeks', None) if tariff else None
    if tariff:
        if tariff_device_price is None or tariff_device_price <= 0:
            await callback.answer(
                texts.t('TARIFF_DEVICES_DISABLED', '⚠️ Изменение устройств недоступно для вашего тарифа'),
                show_alert=True,
            )
            return
    # Для обычных подписок проверяем глобальную настройку
    elif not settings.is_devices_selection_enabled():
        await callback.answer(
            texts.t('DEVICES_SELECTION_DISABLED', '⚠️ Изменение количества устройств недоступно'),
            show_alert=True,
        )
        return

    current_devices = subscription.device_limit

    period_hint_days = _get_period_hint_from_subscription(subscription)
    devices_discount_percent = PricingEngine.get_addon_discount_percent(
        db_user,
        'devices',
        period_hint_days,
    )

    # Для тарифов показываем цену из тарифа
    if tariff:
        price_per_device = tariff_device_price
        price_text = texts.format_price(price_per_device)
        prompt_text = texts.t(
            'CHANGE_DEVICES_PROMPT_TARIFF',
            (
                '📱 <b>Изменение количества устройств</b>\n\n'
                'Текущий лимит: {current_devices} устройств\n'
                'Цена за доп. устройство: {price}/мес\n'
                'Выберите новое количество устройств:\n\n'
                '💡 <b>Важно:</b>\n'
                '• При увеличении - доплата пропорционально оставшемуся времени\n'
                '• При уменьшении - возврат средств не производится'
            ),
        ).format(current_devices=current_devices, price=price_text)
    else:
        prompt_text = texts.t(
            'CHANGE_DEVICES_PROMPT',
            (
                '📱 <b>Изменение количества устройств</b>\n\n'
                'Текущий лимит: {current_devices} устройств\n'
                'Выберите новое количество устройств:\n\n'
                '💡 <b>Важно:</b>\n'
                '• При увеличении - доплата пропорционально оставшемуся времени\n'
                '• При уменьшении - возврат средств не производится'
            ),
        ).format(current_devices=current_devices)

    # В мульти-тарифе кнопка "назад" ведёт к детальному виду подписки
    back_cb = f'sm:{sub_id}' if settings.is_multi_tariff_enabled() and sub_id else 'menu_subscription'

    await callback.message.edit_text(
        prompt_text,
        reply_markup=get_change_devices_keyboard(
            current_devices,
            db_user.language,
            subscription.end_date,
            devices_discount_percent,
            tariff=tariff,
            back_callback=back_cb,
        ),
    )

    await callback.answer()


async def confirm_change_devices(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    texts = get_texts(db_user.language)
    try:
        new_devices_count = int(callback.data.split('_')[2])
    except (ValueError, IndexError):
        await callback.answer(texts.t('INVALID_REQUEST', 'Invalid request'), show_alert=True)
        return
    subscription, sub_id = await _resolve_subscription(callback, db_user, db, state)
    if subscription is None:
        return

    # Проверяем тариф подписки
    tariff = None
    if subscription.tariff_id:
        from app.database.crud.tariff import get_tariff_by_id

        tariff = await get_tariff_by_id(db, subscription.tariff_id)

    # Для тарифов - проверяем разрешено ли изменение устройств
    tariff_device_price = getattr(tariff, 'device_price_kopeks', None) if tariff else None
    if tariff:
        if tariff_device_price is None or tariff_device_price <= 0:
            await callback.answer(
                texts.t('TARIFF_DEVICES_DISABLED', '⚠️ Изменение устройств недоступно для вашего тарифа'),
                show_alert=True,
            )
            return
        price_per_device = tariff_device_price
    else:
        if not settings.is_devices_selection_enabled():
            await callback.answer(
                texts.t('DEVICES_SELECTION_DISABLED', '⚠️ Изменение количества устройств недоступно'),
                show_alert=True,
            )
            return
        price_per_device = settings.PRICE_PER_DEVICE

    current_devices = subscription.device_limit

    if new_devices_count == current_devices:
        await callback.answer(
            texts.t('DEVICES_NO_CHANGE', 'ℹ️ Количество устройств не изменилось'),
            show_alert=True,
        )
        return

    # Используем max_device_limit из тарифа если есть, иначе глобальную настройку
    tariff_max_devices = getattr(tariff, 'max_device_limit', None) if tariff else None
    effective_max = (tariff_max_devices if tariff_max_devices is not None and tariff_max_devices > 0 else None) or (
        settings.MAX_DEVICES_LIMIT if settings.MAX_DEVICES_LIMIT > 0 else None
    )
    if effective_max and new_devices_count > effective_max:
        await callback.answer(
            texts.t(
                'DEVICES_LIMIT_EXCEEDED',
                '⚠️ Превышен максимальный лимит устройств ({limit})',
            ).format(limit=effective_max),
            show_alert=True,
        )
        return

    # По умолчанию ниже включённого в тариф опускать нельзя
    # (ALLOW_DEVICES_BELOW_TARIFF_LIMIT=True возвращает прежний минимум 1).
    from app.utils.subscription_utils import resolve_min_device_limit

    min_devices_count = resolve_min_device_limit(tariff)
    if new_devices_count < min_devices_count:
        await callback.answer(
            texts.t(
                'DEVICES_MIN_LIMIT_REACHED',
                '⚠️ Минимальное количество устройств: {limit}',
            ).format(limit=min_devices_count),
            show_alert=True,
        )
        return

    devices_difference = new_devices_count - current_devices

    if devices_difference > 0:
        additional_devices = devices_difference

        # Устройства в пределах тарифного лимита — бесплатные
        if tariff:
            tariff_included = tariff.device_limit or 0
            if current_devices < tariff_included:
                free_devices = tariff_included - current_devices
                chargeable_devices = max(0, additional_devices - free_devices)
            else:
                chargeable_devices = additional_devices
        elif current_devices < settings.DEFAULT_DEVICE_LIMIT:
            free_devices = settings.DEFAULT_DEVICE_LIMIT - current_devices
            chargeable_devices = max(0, additional_devices - free_devices)
        else:
            chargeable_devices = additional_devices

        devices_price_per_month = chargeable_devices * price_per_device

        # Считаем стоимость по оставшимся дням подписки.
        # Прорейт по фактическому остатку — как трафик/серверы (calculate_prorated_price),
        # без потолка: устройство активно до конца подписки, на продлении доначисляется
        # через pricing_engine.
        now = datetime.now(UTC)
        days_left = max(1, math.ceil((subscription.end_date - now).total_seconds() / 86400))
        period_hint_days = days_left

        devices_discount_percent = PricingEngine.get_addon_discount_percent(
            db_user,
            'devices',
            period_hint_days,
        )
        discounted_per_month, discount_per_month = apply_percentage_discount(
            devices_price_per_month,
            devices_discount_percent,
        )
        price, charged_days = calculate_prorated_price(discounted_per_month, subscription.end_date)
        total_discount = int(discount_per_month * charged_days / 30)
        period_label = f'{charged_days} дн.' if charged_days > 1 else '1 день'

        if price > 0 and db_user.balance_kopeks < price:
            missing_kopeks = price - db_user.balance_kopeks
            required_text = f'{texts.format_price(price)} (за {period_label})'
            message_text = texts.t(
                'ADDON_INSUFFICIENT_FUNDS_MESSAGE',
                (
                    '⚠️ <b>Недостаточно средств</b>\n\n'
                    'Стоимость услуги: {required}\n'
                    'На балансе: {balance}\n'
                    'Не хватает: {missing}\n\n'
                    'Выберите способ пополнения. Сумма подставится автоматически.'
                ),
            ).format(
                required=required_text,
                balance=texts.format_price(db_user.balance_kopeks),
                missing=texts.format_price(missing_kopeks),
            )

            # Сохраняем корзину для автопокупки после пополнения баланса
            await user_cart_service.save_user_cart(
                user_id=db_user.id,
                cart_data={
                    'cart_mode': 'add_devices',
                    # Намерение пополнить ради этой корзины: без него тихая автопокупка после
                    # пополнения пропускает корзину, а кнопка «вернуться» её не знает.
                    'return_to_cart': True,
                    'devices_to_add': devices_difference,
                    'price_kopeks': price,
                },
            )
            logger.info(
                'Сохранена корзина add_devices для пользователя : + устройств, цена коп.',
                telegram_id=db_user.telegram_id,
                devices_difference=devices_difference,
                price=price,
            )

            await callback.message.answer(
                message_text,
                reply_markup=get_insufficient_balance_keyboard(
                    db_user.language,
                    amount_kopeks=missing_kopeks,
                    has_saved_cart=True,
                ),
                parse_mode='HTML',
            )
            await callback.answer()
            return

        action_text = texts.t(
            'DEVICE_CHANGE_ACTION_INCREASE',
            'увеличить до {count}',
        ).format(count=new_devices_count)
        if price > 0:
            cost_text = texts.t(
                'DEVICE_CHANGE_EXTRA_COST',
                'Доплата: {amount} (за {period})',
            ).format(
                amount=texts.format_price(price),
                period=period_label,
                months=period_label,
            )
            if total_discount > 0:
                cost_text += texts.t(
                    'DEVICE_CHANGE_DISCOUNT_INFO',
                    ' (скидка {percent}%: -{amount})',
                ).format(
                    percent=devices_discount_percent,
                    amount=texts.format_price(total_discount),
                )
        else:
            cost_text = texts.t('DEVICE_CHANGE_FREE', 'Бесплатно')

    else:
        price = 0
        action_text = texts.t(
            'DEVICE_CHANGE_ACTION_DECREASE',
            'уменьшить до {count}',
        ).format(count=new_devices_count)
        cost_text = texts.t('DEVICE_CHANGE_NO_REFUND', 'Возврат средств не производится')

    # Проверяем количество подключённых устройств для предупреждения
    devices_warning = ''
    panel_user_id = _get_panel_user_id(subscription, db_user)
    if new_devices_count < current_devices and panel_user_id:
        try:
            service = RemnaWaveService()
            async with service.get_api_client() as api:
                devices_info = await api.get_user_devices(panel_user_id)
                if isinstance(devices_info, dict):
                    connected_count = devices_info.get('total', 0)
                    if connected_count > new_devices_count:
                        devices_warning = texts.t(
                            'DEVICE_CHANGE_RESET_WARNING',
                            (
                                '\n⚠️ <b>Внимание!</b>\n'
                                'У вас подключено {connected} устройств.\n'
                                'При уменьшении лимита до {new} все устройства будут сброшены.\n'
                                'Вам нужно будет заново подключить нужные устройства.\n'
                            ),
                        ).format(connected=connected_count, new=new_devices_count)
        except Exception as e:
            logger.error('Ошибка проверки устройств', error=e)

    confirm_text = texts.t(
        'DEVICE_CHANGE_CONFIRMATION',
        (
            '📱 <b>Подтверждение изменения</b>\n\n'
            'Текущее количество: {current} устройств\n'
            'Новое количество: {new} устройств\n\n'
            'Действие: {action}\n'
            '💰 {cost}\n\n'
            'Подтвердить изменение?'
        ),
    ).format(
        current=current_devices,
        new=new_devices_count,
        action=action_text,
        cost=cost_text,
    )

    if devices_warning:
        confirm_text += devices_warning

    await edit_or_answer_photo(
        callback=callback,
        caption=confirm_text,
        keyboard=get_confirm_change_devices_keyboard(
            new_devices_count,
            price,
            db_user.language,
            back_callback=f'sm:{sub_id}' if settings.is_multi_tariff_enabled() and sub_id else 'menu_subscription',
        ),
        # Подтверждение смены числа устройств — подпункт subscription.devices
        # (кнопка confirm_change_devices_* не матчится детектором — медиа явно).
        **await get_section_media_kwargs(db, 'subscription.devices', language=db_user.language),
    )

    await callback.answer()


async def execute_change_devices(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    callback_parts = callback.data.split('_')
    texts = get_texts(db_user.language)
    try:
        new_devices_count = int(callback_parts[3])
    except (ValueError, IndexError):
        await callback.answer(texts.t('INVALID_REQUEST', 'Invalid request'), show_alert=True)
        return

    db_user = await lock_user_for_pricing(db, db_user.id)
    # Re-resolve after lock since db_user was refreshed
    subscription, _ = await _resolve_subscription(callback, db_user, db, state)
    if not subscription:
        await callback.answer(
            texts.t('NO_ACTIVE_SUBSCRIPTION', '⚠️ У вас нет активной подписки'),
            show_alert=True,
        )
        return
    current_devices = subscription.device_limit

    # Проверяем тариф подписки
    tariff = None
    if subscription.tariff_id:
        from app.database.crud.tariff import get_tariff_by_id

        tariff = await get_tariff_by_id(db, subscription.tariff_id)

    # Для тарифов - проверяем разрешено ли изменение устройств
    if tariff:
        tariff_device_price = getattr(tariff, 'device_price_kopeks', None)
        if tariff_device_price is None or tariff_device_price <= 0:
            await callback.answer(
                texts.t('TARIFF_DEVICES_DISABLED', '⚠️ Изменение устройств недоступно для вашего тарифа'),
                show_alert=True,
            )
            return
        price_per_device = tariff_device_price
    elif not settings.is_devices_selection_enabled():
        await callback.answer(
            texts.t('DEVICES_SELECTION_DISABLED', '⚠️ Изменение количества устройств недоступно'),
            show_alert=True,
        )
        return
    else:
        price_per_device = settings.PRICE_PER_DEVICE

    # По умолчанию ниже включённого в тариф опускать нельзя
    # (ALLOW_DEVICES_BELOW_TARIFF_LIMIT=True возвращает прежний минимум 1).
    from app.utils.subscription_utils import resolve_min_device_limit

    min_devices_count = resolve_min_device_limit(tariff)
    if new_devices_count < min_devices_count:
        await callback.answer(
            texts.t(
                'DEVICES_MIN_LIMIT_REACHED',
                '⚠️ Минимальное количество устройств: {limit}',
            ).format(limit=min_devices_count),
            show_alert=True,
        )
        return

    # Recompute price under lock (callback-baked value may be stale)
    devices_difference = new_devices_count - current_devices
    if devices_difference > 0:
        if tariff:
            tariff_included = tariff.device_limit or 0
            if current_devices < tariff_included:
                free_devices = tariff_included - current_devices
                chargeable_devices = max(0, devices_difference - free_devices)
            else:
                chargeable_devices = devices_difference
        elif current_devices < settings.DEFAULT_DEVICE_LIMIT:
            free_devices = settings.DEFAULT_DEVICE_LIMIT - current_devices
            chargeable_devices = max(0, devices_difference - free_devices)
        else:
            chargeable_devices = devices_difference

        devices_price_per_month = chargeable_devices * price_per_device
        days_left = max(1, math.ceil((subscription.end_date - datetime.now(UTC)).total_seconds() / 86400))
        devices_discount_percent = PricingEngine.get_addon_discount_percent(
            db_user,
            'devices',
            days_left,
        )
        discounted_per_month, _ = apply_percentage_discount(
            devices_price_per_month,
            devices_discount_percent,
        )
        # Прорейт по остатку подписки (как трафик/серверы), без потолка.
        price, _ = calculate_prorated_price(discounted_per_month, subscription.end_date)
    else:
        price = 0

    try:
        if price > 0:
            success = await subtract_user_balance(
                db, db_user, price, f'Изменение количества устройств с {current_devices} до {new_devices_count}'
            )

            if not success:
                await callback.answer(
                    texts.t('PAYMENT_CHARGE_ERROR', '⚠️ Ошибка списания средств'),
                    show_alert=True,
                )
                return

            charged_days = max(1, math.ceil((subscription.end_date - datetime.now(UTC)).total_seconds() / 86400))
            await create_transaction(
                db=db,
                user_id=db_user.id,
                type=TransactionType.SUBSCRIPTION_PAYMENT,
                amount_kopeks=price,
                description=f'Изменение устройств с {current_devices} до {new_devices_count} за {charged_days} дн.',
            )

        # Re-lock subscription after subtract_user_balance committed (released all locks)
        relock_result = await db.execute(
            select(Subscription)
            .where(Subscription.id == subscription.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        subscription = relock_result.scalar_one()

        # Re-validate: prevent double-charge and max-limit violation
        if new_devices_count > current_devices:
            tariff_max_recheck = getattr(tariff, 'max_device_limit', None) if tariff else None
            max_devices = (
                tariff_max_recheck if tariff_max_recheck is not None and tariff_max_recheck > 0 else None
            ) or (settings.MAX_DEVICES_LIMIT if settings.MAX_DEVICES_LIMIT > 0 else None)
            if max_devices and new_devices_count > max_devices:
                if price > 0:
                    user_refund = await db.execute(
                        select(User)
                        .where(User.id == db_user.id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    refund_user = user_refund.scalar_one()
                    refund_user.balance_kopeks += price
                    await db.commit()
                await callback.answer(
                    f'⚠️ Лимит устройств ({max_devices}) превышен. Баланс возвращён.',
                    show_alert=True,
                )
                return
            # Check if concurrent request already applied the same change
            if price > 0 and subscription.device_limit >= new_devices_count:
                user_refund = await db.execute(
                    select(User)
                    .where(User.id == db_user.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                refund_user = user_refund.scalar_one()
                refund_user.balance_kopeks += price
                await db.commit()
                await callback.answer(
                    '⚠️ Изменение уже применено. Баланс возвращён.',
                    show_alert=True,
                )
                return

        subscription.device_limit = new_devices_count
        subscription.updated_at = datetime.now(UTC)

        await db.commit()

        # Реактивируем подписку если она была DISABLED/EXPIRED (например, после LIMITED/EXPIRED в RemnaWave)
        from app.database.crud.subscription import reactivate_subscription

        await reactivate_subscription(db, subscription)

        subscription_service = SubscriptionService()
        await subscription_service.update_remnawave_user(db, subscription)

        # Явно включаем пользователя на панели (PATCH может не снять LIMITED-статус)
        panel_user_id = _get_panel_user_id(subscription, db_user)
        if panel_user_id and subscription.status == 'active':
            await subscription_service.enable_remnawave_user(panel_user_id)

        # При уменьшении лимита - удалить лишние устройства (последние подключённые)
        devices_reset_count = 0
        if new_devices_count < current_devices and panel_user_id:
            try:
                service = RemnaWaveService()
                async with service.get_api_client() as api:
                    devices_info = await api.get_user_devices(panel_user_id)
                    if isinstance(devices_info, dict):
                        devices_list = devices_info.get('devices', [])
                        connected_count = len(devices_list)

                        # Если подключённых устройств больше чем новый лимит - удалить лишние
                        if connected_count > new_devices_count:
                            devices_to_remove = connected_count - new_devices_count
                            logger.info(
                                '🔧 Удаление лишних устройств при уменьшении лимита: подключено новый лимит удаляем',
                                connected_count=connected_count,
                                new_devices_count=new_devices_count,
                                devices_to_remove=devices_to_remove,
                            )

                            # Сортируем по дате (последние в конце) и удаляем последние
                            sorted_devices = sorted(
                                devices_list,
                                key=lambda d: d.get('updatedAt') or d.get('createdAt') or '',
                            )
                            devices_to_delete = sorted_devices[-devices_to_remove:]

                            for device in devices_to_delete:
                                device_hwid = device.get('hwid')
                                if device_hwid:
                                    # remove_device сам логирует и возвращает False вместо исключения
                                    if await api.remove_device(panel_user_id, device_hwid):
                                        devices_reset_count += 1
                                        logger.info('✅ Удалено устройство', device_hwid=device_hwid)
                                    else:
                                        logger.error('Ошибка удаления устройства', device_hwid=device_hwid)
            except Exception as reset_error:
                logger.error('Ошибка удаления устройств при уменьшении лимита', reset_error=reset_error)

        await db.refresh(db_user)
        await db.refresh(subscription)

        try:
            from app.services.admin_notification_service import AdminNotificationService

            notification_service = AdminNotificationService(callback.bot)
            await notification_service.send_subscription_update_notification(
                db, db_user, subscription, 'devices', current_devices, new_devices_count, price
            )
        except Exception as e:
            logger.error('Ошибка отправки уведомления об изменении устройств', error=e)

        if new_devices_count > current_devices:
            success_text = texts.t(
                'DEVICE_CHANGE_INCREASE_SUCCESS',
                '✅ Количество устройств увеличено!\n\n',
            )
            success_text += texts.t(
                'DEVICE_CHANGE_RESULT_LINE',
                '📱 Было: {old} → Стало: {new}\n',
            ).format(old=current_devices, new=new_devices_count)
            if price > 0:
                success_text += texts.t(
                    'DEVICE_CHANGE_CHARGED',
                    '💰 Списано: {amount}',
                ).format(amount=texts.format_price(price))
        else:
            success_text = texts.t(
                'DEVICE_CHANGE_DECREASE_SUCCESS',
                '✅ Количество устройств уменьшено!\n\n',
            )
            success_text += texts.t(
                'DEVICE_CHANGE_RESULT_LINE',
                '📱 Было: {old} → Стало: {new}\n',
            ).format(old=current_devices, new=new_devices_count)
            if devices_reset_count > 0:
                success_text += texts.t(
                    'DEVICE_CHANGE_DEVICES_REMOVED',
                    '\n🗑 Удалено устройств: {count}\n',
                ).format(count=devices_reset_count)
            success_text += texts.t(
                'DEVICE_CHANGE_NO_REFUND_INFO',
                'ℹ️ Возврат средств не производится',
            )

        await edit_or_answer_photo(
            callback=callback,
            caption=success_text,
            keyboard=get_back_keyboard(db_user.language),
            **await get_section_media_kwargs(db, 'subscription.devices', language=db_user.language),
        )

        logger.info(
            '✅ Пользователь изменил количество устройств с на доплата: ₽',
            telegram_id=db_user.telegram_id,
            current_devices=current_devices,
            new_devices_count=new_devices_count,
            price=price / 100,
        )

    except Exception as e:
        logger.error('Ошибка изменения количества устройств', error=e)
        await callback.message.edit_text(texts.ERROR, reply_markup=get_back_keyboard(db_user.language))

    await callback.answer()


async def handle_device_management(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    texts = get_texts(db_user.language)
    subscription, sub_id = await _resolve_subscription(callback, db_user, db, state)
    if subscription is None:
        return

    if not subscription or subscription.is_trial:
        await callback.answer(
            texts.t('PAID_FEATURE_ONLY', '⚠️ Эта функция доступна только для платных подписок'),
            show_alert=True,
        )
        return

    panel_user_id = _get_panel_user_id(subscription, db_user)
    if not panel_user_id:
        await callback.answer(
            texts.t('DEVICE_UUID_NOT_FOUND', '❌ UUID пользователя не найден'),
            show_alert=True,
        )
        return

    try:
        from app.services.remnawave_service import RemnaWaveService

        service = RemnaWaveService()

        async with service.get_api_client() as api:
            devices_info = await api.get_user_devices(panel_user_id)

            if isinstance(devices_info, dict):
                total_devices = devices_info.get('total', 0)
                devices_list = devices_info.get('devices', [])

                if total_devices == 0:
                    await edit_or_answer_photo(
                        callback=callback,
                        caption=texts.t('DEVICE_NONE_CONNECTED', 'ℹ️ У вас нет подключенных устройств'),
                        keyboard=get_back_keyboard(db_user.language, _subscription_back_callback(sub_id)),
                        **await get_section_media_kwargs(db, 'subscription.devices', language=db_user.language),
                    )
                    await callback.answer()
                    return

                await show_devices_page(callback, db_user, devices_list, page=1, sub_id=sub_id)
            else:
                await callback.answer(
                    texts.t(
                        'DEVICE_FETCH_INFO_ERROR',
                        '❌ Ошибка получения информации об устройствах',
                    ),
                    show_alert=True,
                )

    except Exception as e:
        logger.error('Ошибка получения списка устройств', error=e)
        await callback.answer(
            texts.t(
                'DEVICE_FETCH_INFO_ERROR',
                '❌ Ошибка получения информации об устройствах',
            ),
            show_alert=True,
        )

    await callback.answer()


async def _enrich_devices_with_aliases(devices_list: list[dict], user_id: int) -> list[dict]:
    """Attach user-local aliases to each device dict, in-place.

    `local_name` is read from `user_device_aliases` (per-user, per-hwid) and
    falls back to None when the user hasn't set one. Keeping this in a
    dedicated helper lets all show-page callers stay tiny.
    """
    try:
        from app.database.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            aliases = await get_aliases_for_user(db, user_id)
    except Exception as exc:  # pragma: no cover - alias DB read is best-effort
        logger.warning('Failed to load device aliases', user_id=user_id, error=str(exc)[:200])
        aliases = {}
    return attach_aliases_to_devices(devices_list, aliases)


async def show_devices_page(
    callback: types.CallbackQuery, db_user: User, devices_list: list[dict], page: int = 1, sub_id: int | None = None
):
    texts = get_texts(db_user.language)
    devices_per_page = DEVICES_PER_PAGE

    # Все вызовы рендера проходят через show_devices_page, поэтому единое место
    # для прикрепления локальных имён — здесь. Клавиатура и текст подтягивают
    # `local_name` из enriched dict'ов.
    devices_list = await _enrich_devices_with_aliases(devices_list, db_user.id)

    pagination = paginate_list(devices_list, page=page, per_page=devices_per_page)

    devices_text = texts.t(
        'DEVICE_MANAGEMENT_OVERVIEW',
        (
            '🔄 <b>Управление устройствами</b>\n\n'
            '📊 Всего подключено: {total} устройств\n'
            '📄 Страница {page} из {pages}\n\n'
        ),
    ).format(total=len(devices_list), page=pagination.page, pages=pagination.total_pages)

    if pagination.items:
        devices_text += texts.t(
            'DEVICE_MANAGEMENT_CONNECTED_HEADER',
            '<b>Подключенные устройства:</b>\n',
        )
        for device in pagination.items:
            device_info = shorten_device_label(device_display_name(device), limit=35)

            devices_text += texts.t(
                'DEVICE_MANAGEMENT_LIST_ITEM',
                '• {device}\n',
            ).format(device=html_mod.escape(device_info))

    devices_text += texts.t(
        'DEVICE_MANAGEMENT_ACTIONS',
        (
            '\n💡 <b>Действия:</b>\n'
            '• Нажмите на устройство — откроется карточка с переименованием и удалением\n'
            '• Или сбросьте все устройства сразу'
        ),
    )

    await callback.message.edit_text(
        devices_text,
        reply_markup=get_devices_management_keyboard(
            pagination.items,
            pagination,
            db_user.language,
            back_callback=_subscription_back_callback(sub_id),
        ),
    )


async def handle_devices_page(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None):
    page = int(callback.data.split('_')[2])
    texts = get_texts(db_user.language)
    subscription, sub_id = await _resolve_subscription(callback, db_user, db, state)
    panel_user_id = _get_panel_user_id(subscription, db_user) if subscription else db_user.remnawave_id

    try:
        from app.services.remnawave_service import RemnaWaveService

        service = RemnaWaveService()

        async with service.get_api_client() as api:
            devices_info = await api.get_user_devices(panel_user_id)

            if isinstance(devices_info, dict):
                devices_list = devices_info.get('devices', [])
                await show_devices_page(callback, db_user, devices_list, page=page, sub_id=sub_id)
            else:
                await callback.answer(
                    texts.t('DEVICE_FETCH_ERROR', '❌ Ошибка получения устройств'),
                    show_alert=True,
                )

    except Exception as e:
        logger.error('Ошибка перехода на страницу устройств', error=e)
        await callback.answer(
            texts.t('DEVICE_PAGE_LOAD_ERROR', '❌ Ошибка загрузки страницы'),
            show_alert=True,
        )


async def _load_user_devices(panel_user_id, user_id: int) -> list[dict] | None:
    """Устройства из панели + локальные имена. ``None`` — панель не ответила.

    Один загрузчик на карточку устройства и на подтверждение удаления: оба
    экрана должны видеть тот же порядок устройств, что и список, иначе
    индекс из callback'а укажет на чужое устройство.
    """
    try:
        service = RemnaWaveService()
        async with service.get_api_client() as api:
            devices_info = await api.get_user_devices(panel_user_id)
    except Exception as exc:
        logger.error('Ошибка получения списка устройств', error=exc)
        return None

    if not isinstance(devices_info, dict):
        return None

    return await _enrich_devices_with_aliases(devices_info.get('devices', []) or [], user_id)


def _parse_device_callback(callback_data: str) -> tuple[int, int] | None:
    """``<префикс>_<индекс>_<страница>`` → (индекс, страница); ``None`` — мусор."""
    parts = (callback_data or '').split('_')
    if len(parts) < 4:
        return None
    try:
        return int(parts[2]), int(parts[3])
    except ValueError:
        return None


async def _device_from_callback(callback, db_user, db, state):
    """(устройство, индекс, страница, sub_id) для экранов одного устройства.

    Устройство ``None`` — причина уже показана пользователю алертом.
    """
    texts = get_texts(db_user.language)

    parsed = _parse_device_callback(callback.data)
    if parsed is None:
        logger.error('Некорректный формат callback_data устройства', callback_data=callback.data)
        await callback.answer(
            texts.t('DEVICE_RESET_INVALID_REQUEST', '❌ Ошибка: некорректный запрос'),
            show_alert=True,
        )
        return None, 0, 1, None

    device_index, page = parsed
    subscription, sub_id = await _resolve_subscription(callback, db_user, db, state)
    panel_user_id = _get_panel_user_id(subscription, db_user) if subscription else db_user.remnawave_id
    if not panel_user_id:
        await callback.answer(
            texts.t('DEVICE_UUID_NOT_FOUND', '❌ UUID пользователя не найден'),
            show_alert=True,
        )
        return None, device_index, page, sub_id

    devices_list = await _load_user_devices(panel_user_id, db_user.id)
    if devices_list is None:
        await callback.answer(
            texts.t('DEVICE_FETCH_ERROR', '❌ Ошибка получения устройств'),
            show_alert=True,
        )
        return None, device_index, page, sub_id

    pagination = paginate_list(devices_list, page=page, per_page=DEVICES_PER_PAGE)
    if device_index >= len(pagination.items):
        await callback.answer(
            texts.t('DEVICE_RESET_NOT_FOUND', '❌ Устройство не найдено'),
            show_alert=True,
        )
        return None, device_index, page, sub_id

    return pagination.items[device_index], device_index, page, sub_id


def _format_device_moment(raw) -> str | None:
    """Момент из панели в локальном часовом поясе; ``None`` — не разобрали.

    Через ``format_datetime`` нельзя гонять что попало: на непарсящейся строке
    он отдаёт «сейчас», и карточка начинает врать о последней активности.
    """
    if isinstance(raw, datetime):
        return format_datetime(raw)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    return format_datetime(parsed)


def _build_device_card_text(device: dict, texts) -> str:
    """Карточка устройства: имя, платформа, модель, ОС, последняя активность."""
    card = texts.t('DEVICE_CARD_TITLE', '📱 <b>{name}</b>\n\n').format(
        name=html_mod.escape(device_display_name(device))
    )

    platform = str(device.get('platform') or '').strip()
    if platform:
        card += texts.t('DEVICE_CARD_PLATFORM', '🖥 Платформа: {platform}\n').format(platform=html_mod.escape(platform))

    model = str(device.get('deviceModel') or device.get('model') or '').strip()
    if model:
        card += texts.t('DEVICE_CARD_MODEL', '📦 Модель: {model}\n').format(model=html_mod.escape(model))

    os_version = str(device.get('osVersion') or '').strip()
    if os_version:
        card += texts.t('DEVICE_CARD_OS', '⚙️ Версия ОС: {os}\n').format(os=html_mod.escape(os_version))

    last_seen = _format_device_moment(device.get('updatedAt') or device.get('createdAt'))
    if last_seen:
        card += texts.t('DEVICE_CARD_LAST_SEEN', '🕒 Последняя активность: {date}\n').format(
            date=html_mod.escape(last_seen)
        )

    card += texts.t(
        'DEVICE_CARD_ACTIONS',
        (
            '\n💡 <b>Действия:</b>\n'
            '• ✏️ — своё имя для устройства (видно только вам)\n'
            '• ❌ — удалить устройство из подписки'
        ),
    )

    # Если панель не отдала ни модели, ни ОС, заголовок и подсказка слипаются
    # в три перевода строки — лишнюю пустую строку убираем.
    while '\n\n\n' in card:
        card = card.replace('\n\n\n', '\n\n')
    return card


async def show_device_card(callback, db_user, db, device: dict, device_index: int, page: int) -> None:
    """Экран одного устройства — «Переименовать» / «Удалить» / «Назад» к списку."""
    texts = get_texts(db_user.language)
    await edit_or_answer_photo(
        callback=callback,
        caption=_build_device_card_text(device, texts),
        keyboard=get_device_card_keyboard(device_index, page, db_user.language),
        **await get_section_media_kwargs(db, 'subscription.devices', language=db_user.language),
    )


async def handle_device_card(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None):
    """Callback `device_card_<idx>_<page>` — тап по устройству в списке.

    Раньше тап по устройству сбрасывал его сразу, без вопроса; теперь
    открывается карточка, а удаление живёт за подтверждением.
    """
    device, device_index, page, _sub_id = await _device_from_callback(callback, db_user, db, state)
    if device is None:
        return

    await show_device_card(callback, db_user, db, device, device_index, page)
    await callback.answer()


async def handle_device_delete_request(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    """Callback `device_remove_<idx>_<page>` — «Точно удалить устройство X?».

    Само удаление делает `handle_single_device_reset` по `reset_device_...`,
    на который указывает кнопка «Удалить» этого экрана.
    """
    device, device_index, page, _sub_id = await _device_from_callback(callback, db_user, db, state)
    if device is None:
        return

    texts = get_texts(db_user.language)
    caption = texts.t(
        'DEVICE_DELETE_CONFIRM',
        (
            '❓ Точно удалить устройство <b>{name}</b>?\n\n'
            'Оно потеряет доступ к VPN и освободит место в лимите. '
            'Подключить его заново можно в любой момент.'
        ),
    ).format(name=html_mod.escape(device_display_name(device)))

    await edit_or_answer_photo(
        callback=callback,
        caption=caption,
        keyboard=get_device_delete_confirm_keyboard(device_index, page, db_user.language),
        **await get_section_media_kwargs(db, 'subscription.devices', language=db_user.language),
    )
    await callback.answer()


async def handle_all_devices_reset_request(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    """Callback `reset_all_ask_<page>` — «Сбросить все устройства?».

    Сам сброс делает `handle_all_devices_reset_from_management` по
    `reset_all_devices`, на который указывает кнопка «Сбросить все» этого
    экрана: раньше кнопка в списке сбрасывала всё сразу, без вопроса.
    """
    texts = get_texts(db_user.language)

    try:
        page = int(callback.data.split('_')[3])
    except (IndexError, ValueError):
        page = 1

    subscription, _sub_id = await _resolve_subscription(callback, db_user, db, state)
    panel_user_id = _get_panel_user_id(subscription, db_user) if subscription else db_user.remnawave_id
    if not panel_user_id:
        await callback.answer(
            texts.t('DEVICE_UUID_NOT_FOUND', '❌ UUID пользователя не найден'),
            show_alert=True,
        )
        return

    devices_list = await _load_user_devices(panel_user_id, db_user.id)
    if devices_list is None:
        await callback.answer(
            texts.t('DEVICE_LIST_FETCH_ERROR', '❌ Ошибка получения списка устройств'),
            show_alert=True,
        )
        return

    if not devices_list:
        await callback.answer(
            texts.t('DEVICE_NONE_CONNECTED', 'ℹ️ У вас нет подключенных устройств'),
            show_alert=True,
        )
        return

    caption = texts.t(
        'DEVICES_RESET_ALL_CONFIRM',
        (
            '❓ <b>Сбросить все устройства?</b>\n\n'
            'Будет отключено устройств: {count}. Подключить их заново можно '
            'в любой момент — ссылка подписки не меняется.'
        ),
    ).format(count=len(devices_list))

    await edit_or_answer_photo(
        callback=callback,
        caption=caption,
        keyboard=get_devices_reset_all_confirm_keyboard(page, db_user.language),
        **await get_section_media_kwargs(db, 'subscription.devices', language=db_user.language),
    )
    await callback.answer()


async def start_device_rename(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None):
    """Callback `device_rename_<idx>_<page>` — prompts user for the new alias.

    Stores hwid/page/sub_id in FSM state so the text-handler can finalize and
    re-render the same listing page.
    """
    texts = get_texts(db_user.language)
    subscription, sub_id = await _resolve_subscription(callback, db_user, db, state)
    panel_user_id = _get_panel_user_id(subscription, db_user) if subscription else db_user.remnawave_id
    if not panel_user_id:
        await callback.answer(
            texts.t('DEVICE_UUID_NOT_FOUND', '❌ UUID пользователя не найден'),
            show_alert=True,
        )
        return

    try:
        callback_parts = callback.data.split('_')
        if len(callback_parts) < 4:
            await callback.answer(
                texts.t('DEVICE_RENAME_INVALID_REQUEST', '❌ Ошибка: некорректный запрос'),
                show_alert=True,
            )
            return
        device_index = int(callback_parts[2])
        page = int(callback_parts[3])
    except (ValueError, IndexError) as exc:
        logger.error('❌ Ошибка парсинга rename callback_data', callback_data=callback.data, error=exc)
        await callback.answer(
            texts.t('DEVICE_RENAME_PARSE_ERROR', '❌ Ошибка обработки запроса'),
            show_alert=True,
        )
        return

    try:
        from app.services.remnawave_service import RemnaWaveService

        service = RemnaWaveService()
        async with service.get_api_client() as api:
            devices_info = await api.get_user_devices(panel_user_id)
    except Exception as exc:
        logger.error('Ошибка получения устройств перед переименованием', error=exc)
        await callback.answer(
            texts.t('DEVICE_FETCH_INFO_ERROR', '❌ Ошибка получения информации об устройствах'),
            show_alert=True,
        )
        return

    devices_list = (devices_info or {}).get('devices', []) or []
    pagination = paginate_list(devices_list, page=page, per_page=DEVICES_PER_PAGE)
    if device_index >= len(pagination.items):
        await callback.answer(
            texts.t('DEVICE_RENAME_NOT_FOUND', '❌ Устройство не найдено'),
            show_alert=True,
        )
        return

    device = pagination.items[device_index]
    hwid = (device.get('hwid') or '').strip()
    if not hwid:
        await callback.answer(
            texts.t('DEVICE_RENAME_NO_HWID', '❌ У устройства нет идентификатора'),
            show_alert=True,
        )
        return

    # Загружаем текущий alias чтобы юзер видел что меняет
    current = await get_alias(db, db_user.id, hwid)

    if state is not None:
        await state.set_state(SubscriptionStates.renaming_device)
        # rename_index нужен «Отмене», чтобы вернуть пользователя в карточку
        # устройства, из которой он сюда попал, а не в общий список.
        await state.update_data(rename_hwid=hwid, rename_page=page, rename_sub_id=sub_id, rename_index=device_index)

    prompt = texts.t(
        'DEVICE_RENAME_PROMPT',
        (
            '✏️ <b>Новое имя устройства</b>\n\n'
            'Текущее: <code>{current}</code>\n\n'
            'Введите новое имя (до {max_len} символов).\n'
            'Чтобы убрать имя — отправьте <code>-</code> или <code>/clear</code>.\n'
            'Отмена — /cancel'
        ),
    ).format(
        current=html_mod.escape(current or '—'),
        max_len=ALIAS_MAX_LENGTH,
    )
    cancel_keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t('DEVICE_RENAME_CANCEL_BTN', '✖️ Отмена'),
                    callback_data='device_rename_cancel',
                )
            ]
        ]
    )
    await callback.message.edit_text(prompt, reply_markup=cancel_keyboard)
    await callback.answer()


async def process_device_rename(message: types.Message, db_user: User, db: AsyncSession, state: FSMContext):
    """Text input from the user with the new alias (or `-`/`/clear` to delete).

    State carries hwid + the listing page so we can re-render after save.
    """
    texts = get_texts(db_user.language)
    data = await state.get_data() if state else {}
    hwid = data.get('rename_hwid')
    page = int(data.get('rename_page') or 1)
    sub_id = data.get('rename_sub_id')

    if not hwid:
        await message.answer(texts.t('DEVICE_RENAME_STATE_LOST', '❌ Сессия истекла, повторите.'))
        if state is not None:
            await state.clear()
        return

    raw = (message.text or '').strip()
    if raw.lower() in {'/cancel', 'cancel', 'отмена'}:
        await message.answer(texts.t('DEVICE_RENAME_CANCELLED', '✖️ Переименование отменено'))
        # NB: НЕ выходим здесь — проваливаемся ниже, чтобы перерисовать список
        # устройств. Иначе пользователь остаётся без списка и без кнопки «назад».
    elif raw in {'-', '—', '/clear', 'clear'} or not raw:
        await delete_alias(db, db_user.id, hwid)
        await message.answer(texts.t('DEVICE_RENAME_CLEARED', '✅ Имя устройства сброшено к стандартному'))
    else:
        new_alias = normalize_alias(raw)
        if not new_alias:
            await message.answer(
                texts.t('DEVICE_RENAME_EMPTY', '❌ Имя не может быть пустым (или используйте «-» для сброса)')
            )
            return  # оставляем FSM-состояние, чтобы юзер мог повторить ввод
        if len(new_alias) > ALIAS_MAX_LENGTH:
            # Доп. защита; normalize_alias уже режет, но даём явный feedback.
            await message.answer(
                texts.t(
                    'DEVICE_RENAME_TOO_LONG',
                    '⚠️ Имя слишком длинное, сократили до {max_len} символов.',
                ).format(max_len=ALIAS_MAX_LENGTH)
            )
        saved = await upsert_alias(db, db_user.id, hwid, new_alias)
        await message.answer(
            texts.t('DEVICE_RENAME_SAVED', '✅ Имя устройства обновлено: <code>{name}</code>').format(
                name=html_mod.escape(saved)
            ),
            parse_mode='HTML',
        )

    if state is not None:
        await state.clear()

    # Re-fetch + re-render текущей страницы списка, чтобы юзер сразу увидел
    # новое имя. Используем fake callback из последнего message — пишем
    # новый список «как новое сообщение» (edit_text здесь не сработает).
    try:
        subscription = None
        if sub_id:
            result = await db.execute(select(Subscription).where(Subscription.id == sub_id))
            subscription = result.scalar_one_or_none()
        panel_user_id = _get_panel_user_id(subscription, db_user) if subscription else db_user.remnawave_id
        if not panel_user_id:
            return
        from app.services.remnawave_service import RemnaWaveService

        service = RemnaWaveService()
        async with service.get_api_client() as api:
            devices_info = await api.get_user_devices(panel_user_id)
        devices_list = (devices_info or {}).get('devices', []) or []

        # Сообщения от FSM-handler'а — не callback, поэтому отдельный пост.
        devices_list = await _enrich_devices_with_aliases(devices_list, db_user.id)
        pagination = paginate_list(devices_list, page=page, per_page=DEVICES_PER_PAGE)
        back_cb = _subscription_back_callback(sub_id)

        await message.answer(
            texts.t('DEVICE_RENAME_OPEN_LIST', '📱 Откройте список устройств, чтобы продолжить'),
            reply_markup=get_devices_management_keyboard(
                pagination.items, pagination, db_user.language, back_callback=back_cb
            ),
        )
    except Exception as exc:
        logger.warning('Failed to re-render devices list after rename', error=str(exc)[:200])


async def cancel_device_rename(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    """Callback `device_rename_cancel` — «Отмена» в промпте переименования.

    Сбрасывает FSM-состояние и возвращает на экран, с которого пришли:
    в карточку устройства, если её индекс сохранён в state, иначе — в список
    устройств на той же странице. Главное — не оставить тупик без «назад».
    """
    texts = get_texts(db_user.language)
    data = await state.get_data() if state else {}
    page = int(data.get('rename_page') or 1)
    sub_id = data.get('rename_sub_id')
    raw_index = data.get('rename_index')
    device_index = raw_index if isinstance(raw_index, int) else None
    if state is not None:
        await state.clear()

    subscription = None
    if sub_id:
        result = await db.execute(select(Subscription).where(Subscription.id == sub_id))
        subscription = result.scalar_one_or_none()
    panel_user_id = _get_panel_user_id(subscription, db_user) if subscription else db_user.remnawave_id

    if panel_user_id:
        try:
            service = RemnaWaveService()
            async with service.get_api_client() as api:
                devices_info = await api.get_user_devices(panel_user_id)
            devices_list = (devices_info or {}).get('devices', []) or []

            if device_index is not None:
                enriched = await _enrich_devices_with_aliases(list(devices_list), db_user.id)
                pagination = paginate_list(enriched, page=page, per_page=DEVICES_PER_PAGE)
                if device_index < len(pagination.items):
                    await show_device_card(callback, db_user, db, pagination.items[device_index], device_index, page)
                    await callback.answer(texts.t('DEVICE_RENAME_CANCELLED', '✖️ Переименование отменено'))
                    return

            await show_devices_page(callback, db_user, devices_list, page=page, sub_id=sub_id)
            await callback.answer(texts.t('DEVICE_RENAME_CANCELLED', '✖️ Переименование отменено'))
            return
        except Exception as exc:
            logger.warning('Failed to reopen devices list after rename cancel', error=str(exc)[:200])

    # Fallback: список не достали — хотя бы внятно сообщаем и не оставляем пустоту.
    await callback.message.edit_text(
        texts.t('DEVICE_RENAME_CANCELLED', '✖️ Переименование отменено'),
        reply_markup=get_back_keyboard(db_user.language, _subscription_back_callback(sub_id)),
    )
    await callback.answer()


async def handle_single_device_reset(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    texts = get_texts(db_user.language)
    subscription, sub_id = await _resolve_subscription(callback, db_user, db, state)
    panel_user_id = _get_panel_user_id(subscription, db_user) if subscription else db_user.remnawave_id
    try:
        callback_parts = callback.data.split('_')
        if len(callback_parts) < 4:
            logger.error('Некорректный формат callback_data', callback_data=callback.data)
            await callback.answer(
                texts.t('DEVICE_RESET_INVALID_REQUEST', '❌ Ошибка: некорректный запрос'),
                show_alert=True,
            )
            return

        device_index = int(callback_parts[2])
        page = int(callback_parts[3])

        logger.info('🔧 Сброс устройства: index=, page', device_index=device_index, page=page)

    except (ValueError, IndexError) as e:
        logger.error('❌ Ошибка парсинга callback_data', callback_data=callback.data, error=e)
        await callback.answer(
            texts.t('DEVICE_RESET_PARSE_ERROR', '❌ Ошибка обработки запроса'),
            show_alert=True,
        )
        return

    try:
        from app.services.remnawave_service import RemnaWaveService

        service = RemnaWaveService()

        async with service.get_api_client() as api:
            devices_info = await api.get_user_devices(panel_user_id)

            if isinstance(devices_info, dict):
                # Локальные имена подтягиваем до выбора устройства: в алерте
                # должно стоять то же имя, что пользователь видел в карточке
                # и в подтверждении, а не платформенная строка панели.
                devices_list = await _enrich_devices_with_aliases(devices_info.get('devices', []) or [], db_user.id)

                devices_per_page = DEVICES_PER_PAGE
                pagination = paginate_list(devices_list, page=page, per_page=devices_per_page)

                if device_index < len(pagination.items):
                    device = pagination.items[device_index]
                    device_hwid = device.get('hwid')

                    if device_hwid:
                        # remove_device не бросает исключений — сообщаем об отказе явно
                        if not await api.remove_device(panel_user_id, device_hwid):
                            await callback.answer(
                                texts.t('DEVICE_RESET_ERROR', '❌ Ошибка сброса устройства'),
                                show_alert=True,
                            )
                            return

                        device_info = device_display_name(device)

                        await callback.answer(
                            texts.t(
                                'DEVICE_RESET_SUCCESS',
                                '✅ Устройство {device} успешно сброшено!',
                            ).format(device=device_info),
                            show_alert=True,
                        )

                        updated_devices_info = await api.get_user_devices(panel_user_id)
                        if isinstance(updated_devices_info, dict):
                            updated_devices = updated_devices_info.get('devices', [])

                            if updated_devices:
                                updated_pagination = paginate_list(
                                    updated_devices, page=page, per_page=devices_per_page
                                )
                                if not updated_pagination.items and page > 1:
                                    page = page - 1

                                await show_devices_page(callback, db_user, updated_devices, page=page, sub_id=sub_id)
                            else:
                                await callback.message.edit_text(
                                    texts.t(
                                        'DEVICE_RESET_ALL_DONE',
                                        'ℹ️ Все устройства сброшены',
                                    ),
                                    reply_markup=get_back_keyboard(
                                        db_user.language, _subscription_back_callback(sub_id)
                                    ),
                                )

                        logger.info(
                            '✅ Пользователь сбросил устройство',
                            telegram_id=db_user.telegram_id,
                            device_info=device_info,
                        )
                    else:
                        await callback.answer(
                            texts.t(
                                'DEVICE_RESET_ID_FAILED',
                                '❌ Не удалось получить ID устройства',
                            ),
                            show_alert=True,
                        )
                else:
                    await callback.answer(
                        texts.t('DEVICE_RESET_NOT_FOUND', '❌ Устройство не найдено'),
                        show_alert=True,
                    )
            else:
                await callback.answer(
                    texts.t('DEVICE_FETCH_ERROR', '❌ Ошибка получения устройств'),
                    show_alert=True,
                )

    except Exception as e:
        logger.error('Ошибка сброса устройства', error=e)
        await callback.answer(
            texts.t('DEVICE_RESET_ERROR', '❌ Ошибка сброса устройства'),
            show_alert=True,
        )


async def handle_all_devices_reset_from_management(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    texts = get_texts(db_user.language)
    subscription, sub_id = await _resolve_subscription(callback, db_user, db, state)
    panel_user_id = _get_panel_user_id(subscription, db_user) if subscription else db_user.remnawave_id

    if not panel_user_id:
        await callback.answer(
            texts.t('DEVICE_UUID_NOT_FOUND', '❌ UUID пользователя не найден'),
            show_alert=True,
        )
        return

    try:
        from app.services.remnawave_service import RemnaWaveService

        service = RemnaWaveService()

        async with service.get_api_client() as api:
            devices_info = await api.get_user_devices(panel_user_id)

            if not isinstance(devices_info, dict):
                await callback.answer(
                    texts.t(
                        'DEVICE_LIST_FETCH_ERROR',
                        '❌ Ошибка получения списка устройств',
                    ),
                    show_alert=True,
                )
                return

            devices_list = devices_info.get('devices', [])

            if not devices_list:
                await callback.answer(
                    texts.t('DEVICE_NONE_CONNECTED', 'ℹ️ У вас нет подключенных устройств'),
                    show_alert=True,
                )
                return

            logger.info('🔧 Найдено устройств для сброса', devices_list_count=len(devices_list))

            success_count = 0
            failed_count = 0

            for device in devices_list:
                device_hwid = device.get('hwid')
                if device_hwid:
                    # remove_device сам логирует и возвращает False вместо исключения
                    if await api.remove_device(panel_user_id, device_hwid):
                        success_count += 1
                        logger.info('✅ Устройство удалено', device_hwid=device_hwid)
                    else:
                        failed_count += 1
                        logger.error('❌ Ошибка удаления устройства', device_hwid=device_hwid)
                else:
                    failed_count += 1
                    logger.warning('⚠️ У устройства нет HWID', device=device)

            if success_count > 0:
                if failed_count == 0:
                    await edit_or_answer_photo(
                        callback=callback,
                        caption=texts.t(
                            'DEVICE_RESET_ALL_SUCCESS_MESSAGE',
                            (
                                '✅ <b>Все устройства успешно сброшены!</b>\n\n'
                                '🔄 Сброшено: {count} устройств\n'
                                '📱 Теперь вы можете заново подключить свои устройства\n\n'
                                "💡 Используйте ссылку из раздела 'Моя подписка' для повторного подключения"
                            ),
                        ).format(count=success_count),
                        keyboard=get_back_keyboard(db_user.language, _subscription_back_callback(sub_id)),
                        parse_mode='HTML',
                        **await get_section_media_kwargs(db, 'subscription.devices', language=db_user.language),
                    )
                    logger.info(
                        '✅ Пользователь успешно сбросил устройств',
                        telegram_id=db_user.telegram_id,
                        success_count=success_count,
                    )
                else:
                    await edit_or_answer_photo(
                        callback=callback,
                        caption=texts.t(
                            'DEVICE_RESET_PARTIAL_MESSAGE',
                            (
                                '⚠️ <b>Частичный сброс устройств</b>\n\n'
                                '✅ Удалено: {success} устройств\n'
                                '❌ Не удалось удалить: {failed} устройств\n\n'
                                'Попробуйте еще раз или обратитесь в поддержку.'
                            ),
                        ).format(success=success_count, failed=failed_count),
                        keyboard=get_back_keyboard(db_user.language, _subscription_back_callback(sub_id)),
                        parse_mode='HTML',
                        **await get_section_media_kwargs(db, 'subscription.devices', language=db_user.language),
                    )
                    logger.warning(
                        '⚠️ Частичный сброс у пользователя /',
                        telegram_id=db_user.telegram_id,
                        success_count=success_count,
                        devices_list_count=len(devices_list),
                    )
            else:
                await edit_or_answer_photo(
                    callback=callback,
                    caption=texts.t(
                        'DEVICE_RESET_ALL_FAILED_MESSAGE',
                        (
                            '❌ <b>Не удалось сбросить устройства</b>\n\n'
                            'Попробуйте еще раз позже или обратитесь в техподдержку.\n\n'
                            'Всего устройств: {total}'
                        ),
                    ).format(total=len(devices_list)),
                    keyboard=get_back_keyboard(db_user.language, _subscription_back_callback(sub_id)),
                    parse_mode='HTML',
                    **await get_section_media_kwargs(db, 'subscription.devices', language=db_user.language),
                )
                logger.error(
                    '❌ Не удалось сбросить ни одного устройства у пользователя', telegram_id=db_user.telegram_id
                )

    except Exception as e:
        logger.error('Ошибка сброса всех устройств', error=e)
        await callback.message.edit_text(
            texts.ERROR, reply_markup=get_back_keyboard(db_user.language, _subscription_back_callback(sub_id))
        )

    await callback.answer()


async def confirm_add_devices(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None):
    devices_count = int(callback.data.split('_')[2])
    texts = get_texts(db_user.language)
    subscription, sub_id = await _resolve_subscription(callback, db_user, db, state)
    if subscription is None:
        return

    # Проверяем тариф подписки
    tariff = None
    if subscription.tariff_id:
        from app.database.crud.tariff import get_tariff_by_id

        tariff = await get_tariff_by_id(db, subscription.tariff_id)

    # Для тарифов - проверяем разрешено ли добавление устройств
    tariff_device_price = getattr(tariff, 'device_price_kopeks', None) if tariff else None
    if tariff:
        if tariff_device_price is None or tariff_device_price <= 0:
            await callback.answer(
                texts.t('TARIFF_DEVICES_DISABLED', '⚠️ Добавление устройств недоступно для вашего тарифа'),
                show_alert=True,
            )
            return
        price_per_device = tariff_device_price
    else:
        if not settings.is_devices_selection_enabled():
            await callback.answer(
                texts.t('DEVICES_SELECTION_DISABLED', '⚠️ Изменение количества устройств недоступно'),
                show_alert=True,
            )
            return
        price_per_device = settings.PRICE_PER_DEVICE

    resume_callback = None

    new_total_devices = subscription.device_limit + devices_count

    # Используем max_device_limit из тарифа если есть, иначе глобальную настройку
    tariff_max_devices = getattr(tariff, 'max_device_limit', None) if tariff else None
    effective_max = tariff_max_devices or (settings.MAX_DEVICES_LIMIT if settings.MAX_DEVICES_LIMIT > 0 else None)
    if effective_max and new_total_devices > effective_max:
        await callback.answer(
            texts.t(
                'DEVICES_LIMIT_EXCEEDED_DETAIL',
                '⚠️ Превышен максимальный лимит устройств ({limit}). У вас: {current}, добавляете: {adding}',
            ).format(limit=effective_max, current=subscription.device_limit, adding=devices_count),
            show_alert=True,
        )
        return

    # Устройства в пределах тарифного лимита — бесплатные
    current_devices = subscription.device_limit or 1
    if tariff:
        tariff_included = tariff.device_limit or 0
        if current_devices < tariff_included:
            free_devices = tariff_included - current_devices
            chargeable_devices = max(0, devices_count - free_devices)
        else:
            chargeable_devices = devices_count
    elif current_devices < settings.DEFAULT_DEVICE_LIMIT:
        free_devices = settings.DEFAULT_DEVICE_LIMIT - current_devices
        chargeable_devices = max(0, devices_count - free_devices)
    else:
        chargeable_devices = devices_count

    devices_price_per_month = chargeable_devices * price_per_device

    # TOCTOU: lock user row before reading promo/discount state
    db_user = await lock_user_for_pricing(db, db_user.id)

    # Проверяем является ли тариф суточным
    is_daily_tariff = tariff and getattr(tariff, 'is_daily', False)

    if is_daily_tariff:
        # Для суточных тарифов считаем по дням (как в кабинете)
        now = datetime.now(UTC)
        days_left = max(1, math.ceil((subscription.end_date - now).total_seconds() / 86400))
        period_hint_days = days_left

        devices_discount_percent = PricingEngine.get_addon_discount_percent(
            db_user,
            'devices',
            period_hint_days,
        )
        discounted_per_month, discount_per_month = apply_percentage_discount(
            devices_price_per_month,
            devices_discount_percent,
        )
        # Прорейт по остатку подписки (как трафик/серверы), без потолка.
        price, charged_days = calculate_prorated_price(discounted_per_month, subscription.end_date)
        total_discount = int(discount_per_month * charged_days / 30)
        period_label = f'{charged_days} дн.' if charged_days > 1 else '1 день'
    else:
        # Для обычных тарифов - по дням (как в кабинете)
        now = datetime.now(UTC)
        days_left = max(1, math.ceil((subscription.end_date - now).total_seconds() / 86400))
        period_hint_days = days_left

        devices_discount_percent = PricingEngine.get_addon_discount_percent(
            db_user,
            'devices',
            period_hint_days,
        )
        discounted_per_month, discount_per_month = apply_percentage_discount(
            devices_price_per_month,
            devices_discount_percent,
        )
        # Прорейт по остатку подписки (как трафик/серверы), без потолка.
        price, charged_days = calculate_prorated_price(discounted_per_month, subscription.end_date)
        total_discount = int(discount_per_month * charged_days / 30)
        period_label = f'{charged_days} дн.' if charged_days > 1 else '1 день'

    logger.info(
        'Добавление устройств',
        devices_count=devices_count,
        discounted_per_month=discounted_per_month / 100,
        period_label=period_label,
        price=price / 100,
        total_discount=total_discount / 100,
    )

    if price > 0 and db_user.balance_kopeks < price:
        missing_kopeks = price - db_user.balance_kopeks
        required_text = f'{texts.format_price(price)} (за {period_label})'
        message_text = texts.t(
            'ADDON_INSUFFICIENT_FUNDS_MESSAGE',
            (
                '⚠️ <b>Недостаточно средств</b>\n\n'
                'Стоимость услуги: {required}\n'
                'На балансе: {balance}\n'
                'Не хватает: {missing}\n\n'
                'Выберите способ пополнения. Сумма подставится автоматически.'
            ),
        ).format(
            required=required_text,
            balance=texts.format_price(db_user.balance_kopeks),
            missing=texts.format_price(missing_kopeks),
        )

        # Сохраняем корзину для автопокупки после пополнения баланса
        await user_cart_service.save_user_cart(
            user_id=db_user.id,
            cart_data={
                'cart_mode': 'add_devices',
                # Намерение пополнить ради этой корзины: без него тихая автопокупка после
                # пополнения пропускает корзину, а кнопка «вернуться» её не знает.
                'return_to_cart': True,
                'devices_to_add': devices_count,
                'price_kopeks': price,
            },
        )
        logger.info(
            'Сохранена корзина add_devices для пользователя : + устройств, цена коп.',
            telegram_id=db_user.telegram_id,
            devices_count=devices_count,
            price=price,
        )

        await callback.message.edit_text(
            message_text,
            reply_markup=get_insufficient_balance_keyboard(
                db_user.language,
                resume_callback=resume_callback,
                amount_kopeks=missing_kopeks,
                has_saved_cart=True,
            ),
            parse_mode='HTML',
        )
        await callback.answer()
        return

    try:
        success = await subtract_user_balance(
            db, db_user, price, f'Добавление {devices_count} устройств на {period_label}'
        )

        if not success:
            await callback.answer('⚠️ Ошибка списания средств', show_alert=True)
            return

        # Re-lock subscription after subtract_user_balance committed (released all locks)
        relock_result = await db.execute(
            select(Subscription)
            .where(Subscription.id == subscription.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        subscription = relock_result.scalar_one()

        # Re-validate max device limit after re-lock
        actual_current = subscription.device_limit or 1
        actual_new = actual_current + devices_count
        tariff_max_recheck = getattr(tariff, 'max_device_limit', None) if tariff else None
        max_devices = tariff_max_recheck or (settings.MAX_DEVICES_LIMIT if settings.MAX_DEVICES_LIMIT > 0 else None)
        if max_devices and actual_new > max_devices:
            # Concurrent purchase exceeded limit — refund
            user_refund = await db.execute(
                select(User).where(User.id == db_user.id).with_for_update().execution_options(populate_existing=True)
            )
            refund_user = user_refund.scalar_one()
            refund_user.balance_kopeks += price
            await db.commit()
            await callback.answer(
                f'⚠️ Лимит устройств ({max_devices}) превышен. Баланс возвращён.',
                show_alert=True,
            )
            return

        subscription.device_limit = actual_new
        subscription.updated_at = datetime.now(UTC)
        await db.commit()

        # Реактивируем подписку если она была DISABLED/EXPIRED (например, после LIMITED/EXPIRED в RemnaWave)
        from app.database.crud.subscription import reactivate_subscription

        await reactivate_subscription(db, subscription)

        subscription_service = SubscriptionService()
        await subscription_service.update_remnawave_user(db, subscription)

        # Явно включаем пользователя на панели (PATCH может не снять LIMITED-статус)
        panel_user_id = _get_panel_user_id(subscription, db_user)
        if panel_user_id and subscription.status == 'active':
            await subscription_service.enable_remnawave_user(panel_user_id)

        await create_transaction(
            db=db,
            user_id=db_user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=price,
            description=f'Добавление {devices_count} устройств на {period_label}',
        )

        await db.refresh(db_user)
        await db.refresh(subscription)

        # Отправляем уведомление админам о докупке устройств
        try:
            from app.services.admin_notification_service import AdminNotificationService

            notification_service = AdminNotificationService(callback.bot)
            old_device_limit = subscription.device_limit - devices_count
            await notification_service.send_subscription_update_notification(
                db, db_user, subscription, 'devices', old_device_limit, subscription.device_limit, price
            )
        except Exception as e:
            logger.error('Ошибка отправки уведомления о докупке устройств', error=e)

        success_text = (
            '✅ Устройства успешно добавлены!\n\n'
            f'📱 Добавлено: {devices_count} устройств\n'
            f'Новый лимит: {subscription.device_limit} устройств\n'
        )
        success_text += f'💰 Списано: {texts.format_price(price)} (за {period_label})'
        if total_discount > 0:
            success_text += f' (скидка {devices_discount_percent}%: -{texts.format_price(total_discount)})'

        await edit_or_answer_photo(
            callback=callback,
            caption=success_text,
            keyboard=get_back_keyboard(db_user.language),
            **await get_section_media_kwargs(db, 'subscription.devices', language=db_user.language),
        )

        logger.info(
            '✅ Пользователь добавил устройств за ₽',
            telegram_id=db_user.telegram_id,
            devices_count=devices_count,
            price=price / 100,
        )

    except Exception as e:
        logger.error('Ошибка добавления устройств', error=e)
        await callback.message.edit_text(texts.ERROR, reply_markup=get_back_keyboard(db_user.language))

    await callback.answer()


async def handle_reset_devices(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    await handle_device_management(callback, db_user, db, state)


async def confirm_reset_devices(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    await handle_device_management(callback, db_user, db, state)


async def handle_device_guide(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None):
    device_type = callback.data.split('_')[2]
    texts = get_texts(db_user.language)
    subscription, sub_id = await _resolve_subscription(callback, db_user, db, state)
    if subscription is None:
        return
    subscription_link = get_display_subscription_link(subscription)

    if not subscription_link:
        await callback.answer(
            texts.t('SUBSCRIPTION_LINK_UNAVAILABLE', '❌ Ссылка подписки недоступна'),
            show_alert=True,
        )
        return

    apps = await get_apps_for_platform_async(device_type, db_user.language)

    hide_subscription_link = settings.should_hide_subscription_link()

    if not apps:
        await callback.answer(
            texts.t('SUBSCRIPTION_DEVICE_APPS_NOT_FOUND', '❌ Приложения для этого устройства не найдены'),
            show_alert=True,
        )
        return

    featured_app = next((app for app in apps if app.get('isFeatured', False)), apps[0])
    featured_app_id = featured_app.get('id')
    other_apps = [app for app in apps if isinstance(app, dict) and app.get('id') and app.get('id') != featured_app_id]

    other_app_names = ', '.join(
        html_mod.escape(str(app.get('name')).strip())
        for app in other_apps
        if isinstance(app.get('name'), str) and app.get('name').strip()
    )

    if hide_subscription_link:
        link_section = (
            texts.t('SUBSCRIPTION_DEVICE_LINK_TITLE', '🔗 <b>Ссылка подписки:</b>')
            + '\n'
            + texts.t(
                'SUBSCRIPTION_LINK_HIDDEN_NOTICE',
                'ℹ️ Ссылка подписки доступна по кнопкам ниже или в разделе "Моя подписка".',
            )
            + '\n\n'
        )
    else:
        link_section = (
            texts.t('SUBSCRIPTION_DEVICE_LINK_TITLE', '🔗 <b>Ссылка подписки:</b>')
            + f'\n<code>{html_mod.escape(subscription_link)}</code>\n\n'
        )

    guide_text = (
        texts.t(
            'SUBSCRIPTION_DEVICE_GUIDE_TITLE',
            '📱 <b>Настройка для {device_name}</b>',
        ).format(device_name=html_mod.escape(get_device_name(device_type, db_user.language)))
        + '\n\n'
        + link_section
        + texts.t(
            'SUBSCRIPTION_DEVICE_FEATURED_APP',
            '📋 <b>Рекомендуемое приложение:</b> {app_name}',
        ).format(app_name=html_mod.escape(featured_app.get('name', '')))
    )

    if other_app_names:
        guide_text += '\n\n' + texts.t(
            'SUBSCRIPTION_DEVICE_OTHER_APPS',
            '📦 <b>Другие приложения:</b> {app_list}',
        ).format(app_list=other_app_names)
        guide_text += '\n' + texts.t(
            'SUBSCRIPTION_DEVICE_OTHER_APPS_HINT',
            'Нажмите кнопку "Другие приложения" ниже, чтобы выбрать приложение.',
        )

    blocks_text = render_guide_blocks(featured_app.get('blocks', []), db_user.language)
    if blocks_text:
        guide_text += '\n\n' + blocks_text

    guide_text += '\n\n' + texts.t('SUBSCRIPTION_DEVICE_HOW_TO_TITLE', '💡 <b>Как подключить:</b>')
    guide_text += '\n' + '\n'.join(
        [
            texts.t(
                'SUBSCRIPTION_DEVICE_HOW_TO_STEP1',
                '1. Установите приложение по ссылке выше',
            ),
            texts.t(
                'SUBSCRIPTION_DEVICE_HOW_TO_STEP2',
                '2. Нажмите кнопку "Подключиться" ниже',
            ),
            texts.t(
                'SUBSCRIPTION_DEVICE_HOW_TO_STEP3',
                '3. Откройте приложение и вставьте ссылку',
            ),
            texts.t(
                'SUBSCRIPTION_DEVICE_HOW_TO_STEP4',
                '4. Подключитесь к серверу',
            ),
        ]
    )

    await callback.message.edit_text(
        guide_text,
        reply_markup=get_connection_guide_keyboard(
            subscription_link,
            featured_app,
            device_type,
            db_user.language,
            has_other_apps=bool(other_apps),
            sub_id=sub_id,
        ),
        parse_mode='HTML',
    )
    await callback.answer()


async def handle_app_selection(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    device_type = callback.data.split('_')[2]
    texts = get_texts(db_user.language)

    apps = await get_apps_for_platform_async(device_type, db_user.language)

    if not apps:
        await callback.answer(
            texts.t('SUBSCRIPTION_DEVICE_APPS_NOT_FOUND', '❌ Приложения для этого устройства не найдены'),
            show_alert=True,
        )
        return

    app_text = (
        texts.t(
            'SUBSCRIPTION_APPS_TITLE',
            '📱 <b>Приложения для {device_name}</b>',
        ).format(device_name=html_mod.escape(get_device_name(device_type, db_user.language)))
        + '\n\n'
        + texts.t('SUBSCRIPTION_APPS_PROMPT', 'Выберите приложение для подключения:')
    )

    await callback.message.edit_text(
        app_text, reply_markup=get_app_selection_keyboard(device_type, apps, db_user.language), parse_mode='HTML'
    )
    await callback.answer()


async def handle_specific_app_guide(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    parts = callback.data.split('_', 2)
    if len(parts) < 3:
        await callback.answer('Invalid callback data', show_alert=True)
        return
    _, device_type, app_id = parts
    texts = get_texts(db_user.language)
    subscription, sub_id = await _resolve_subscription(callback, db_user, db, state)
    if subscription is None:
        return

    subscription_link = get_display_subscription_link(subscription)

    if not subscription_link:
        await callback.answer(
            texts.t('SUBSCRIPTION_LINK_UNAVAILABLE', '❌ Ссылка подписки недоступна'),
            show_alert=True,
        )
        return

    apps = await get_apps_for_platform_async(device_type, db_user.language)
    app = next((a for a in apps if a.get('id') == app_id), None) if apps else None

    if not app:
        await callback.answer(
            texts.t('SUBSCRIPTION_APP_NOT_FOUND', '❌ Приложение не найдено'),
            show_alert=True,
        )
        return

    hide_subscription_link = settings.should_hide_subscription_link()

    if hide_subscription_link:
        link_section = (
            texts.t('SUBSCRIPTION_DEVICE_LINK_TITLE', '🔗 <b>Ссылка подписки:</b>')
            + '\n'
            + texts.t(
                'SUBSCRIPTION_LINK_HIDDEN_NOTICE',
                'ℹ️ Ссылка подписки доступна по кнопкам ниже или в разделе "Моя подписка".',
            )
            + '\n\n'
        )
    else:
        link_section = (
            texts.t('SUBSCRIPTION_DEVICE_LINK_TITLE', '🔗 <b>Ссылка подписки:</b>')
            + f'\n<code>{html_mod.escape(subscription_link)}</code>\n\n'
        )

    guide_text = (
        texts.t(
            'SUBSCRIPTION_SPECIFIC_APP_TITLE',
            '📱 <b>{app_name} - {device_name}</b>',
        ).format(
            app_name=html_mod.escape(app.get('name', '')),
            device_name=html_mod.escape(get_device_name(device_type, db_user.language)),
        )
        + '\n\n'
        + link_section
    )

    blocks_text = render_guide_blocks(app.get('blocks', []), db_user.language)
    if blocks_text:
        guide_text += blocks_text + '\n\n'

    await callback.message.edit_text(
        guide_text,
        reply_markup=get_specific_app_keyboard(
            subscription_link,
            app,
            device_type,
            db_user.language,
            sub_id=sub_id,
        ),
        parse_mode='HTML',
    )
    await callback.answer()


async def show_device_connection_help(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    subscription, sub_id = await _resolve_subscription(callback, db_user, db, state)
    if subscription is None:
        return
    subscription_link = get_display_subscription_link(subscription)

    if not subscription_link:
        await callback.answer('❌ Ссылка подписки недоступна', show_alert=True)
        return

    help_text = f"""
📱 <b>Как подключить устройство заново</b>

После сброса устройства вам нужно:

<b>1. Получить ссылку подписки:</b>
📋 Скопируйте ссылку ниже или найдите её в разделе "Моя подписка"

<b>2. Настроить VPN приложение:</b>
• Откройте ваше VPN приложение
• Найдите функцию "Добавить подписку" или "Import"
• Вставьте скопированную ссылку

<b>3. Подключиться:</b>
• Выберите сервер
• Нажмите "Подключить"

<b>🔗 Ваша ссылка подписки:</b>
<code>{html_mod.escape(subscription_link)}</code>

💡 <b>Совет:</b> Сохраните эту ссылку - она понадобится для подключения новых устройств
"""

    await callback.message.edit_text(
        help_text, reply_markup=get_device_management_help_keyboard(db_user.language), parse_mode='HTML'
    )
    await callback.answer()
