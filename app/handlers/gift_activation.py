"""Handler for gift subscription activation via inline callback button."""

import html as html_mod

import structlog
from aiogram import Dispatcher, F, types
from aiogram.types import InaccessibleMessage

from app.database.crud.user import get_user_by_telegram_id
from app.database.database import AsyncSessionLocal
from app.localization.texts import get_texts
from app.services.gift_claim_service import (
    GiftClaimAlreadyOwnedError,
    GiftClaimNotActivatableError,
    GiftClaimNotFoundError,
    GiftClaimSelfActivationError,
    claim_bound_gift_for_user,
)
from app.services.guest_purchase_service import GuestPurchaseError
from app.services.section_image_service import SECTION_IMAGE_MY_SUBSCRIPTIONS, get_section_media_kwargs
from app.utils.photo_message import edit_or_answer_photo


logger = structlog.get_logger(__name__)

_GIFT_NOT_FOUND = 'Подарок не найден или недоступен.'


async def handle_gift_activate(callback: types.CallbackQuery) -> None:
    """Handle gift_activate:{purchase_id} callback from Telegram notification."""
    if isinstance(callback.message, InaccessibleMessage):
        await callback.answer('Сообщение устарело. Попробуйте /start.', show_alert=True)
        return

    if not callback.data:
        return

    parts = callback.data.split(':', 1)
    if len(parts) != 2:
        await callback.answer(_GIFT_NOT_FOUND, show_alert=True)
        return

    try:
        purchase_id = int(parts[1])
    except ValueError:
        await callback.answer(_GIFT_NOT_FOUND, show_alert=True)
        return

    await callback.answer()
    async with AsyncSessionLocal() as db:
        user = await get_user_by_telegram_id(db, callback.from_user.id)
        await edit_or_answer_photo(
            callback=callback,
            caption='⏳ Активируем подарок...',
            keyboard=None,
            **await get_section_media_kwargs(
                db, SECTION_IMAGE_MY_SUBSCRIPTIONS, language=user.language if user else None
            ),
        )

        if not user:
            await edit_or_answer_photo(
                callback=callback,
                caption=_GIFT_NOT_FOUND,
                keyboard=None,
                **await get_section_media_kwargs(db, SECTION_IMAGE_MY_SUBSCRIPTIONS, language=user.language),
            )
            return

        texts = get_texts(user.language)

        try:
            purchase = await claim_bound_gift_for_user(
                db,
                claimant_user_id=user.id,
                purchase_id=purchase_id,
            )
        except (GiftClaimNotFoundError, GiftClaimAlreadyOwnedError):
            await edit_or_answer_photo(
                callback=callback,
                caption=texts.t('GIFT_ACTIVATION_NOT_FOUND', _GIFT_NOT_FOUND),
                keyboard=None,
                **await get_section_media_kwargs(db, SECTION_IMAGE_MY_SUBSCRIPTIONS, language=user.language),
            )
            return
        except GiftClaimSelfActivationError:
            await edit_or_answer_photo(
                callback=callback,
                caption=texts.t(
                    'GIFT_ACTIVATION_SELF_CLAIM_ERROR',
                    '⚠️ Нельзя активировать свой собственный подарок.\nОтправьте код другу!',
                ),
                keyboard=None,
                **await get_section_media_kwargs(db, SECTION_IMAGE_MY_SUBSCRIPTIONS, language=user.language),
            )
            return
        except GiftClaimNotActivatableError:
            await edit_or_answer_photo(
                callback=callback,
                caption=texts.t('GIFT_ACTIVATION_NOT_ACTIVATABLE_ERROR', '❌ Этот подарок невозможно активировать.'),
                keyboard=None,
                **await get_section_media_kwargs(db, SECTION_IMAGE_MY_SUBSCRIPTIONS, language=user.language),
            )
            return
        except GuestPurchaseError as exc:
            logger.warning(
                'Gift activation via callback failed',
                purchase_id=purchase_id,
                telegram_id=callback.from_user.id,
                error=exc.message,
            )
            if exc.status_code >= 500:
                await edit_or_answer_photo(
                    callback=callback,
                    caption=texts.t(
                        'GIFT_ACTIVATION_GENERIC_ERROR',
                        'Произошла ошибка при активации. Попробуйте позже.',
                    ),
                    keyboard=None,
                    **await get_section_media_kwargs(db, SECTION_IMAGE_MY_SUBSCRIPTIONS, language=user.language),
                )
            else:
                await edit_or_answer_photo(
                    callback=callback,
                    caption=texts.t(
                        'GIFT_ACTIVATION_FAILED_PREFIX',
                        'Не удалось активировать подарок: {error}',
                    ).format(error=html_mod.escape(exc.message)),
                    keyboard=None,
                    **await get_section_media_kwargs(db, SECTION_IMAGE_MY_SUBSCRIPTIONS, language=user.language),
                )
            return
        except Exception:
            logger.exception(
                'Unexpected error during gift activation via callback',
                purchase_id=purchase_id,
                telegram_id=callback.from_user.id,
            )
            await edit_or_answer_photo(
                callback=callback,
                caption=texts.t(
                    'GIFT_ACTIVATION_GENERIC_ERROR',
                    'Произошла ошибка при активации. Попробуйте позже.',
                ),
                keyboard=None,
                **await get_section_media_kwargs(db, SECTION_IMAGE_MY_SUBSCRIPTIONS, language=user.language),
            )
            return

        tariff_name = html_mod.escape(purchase.tariff.name) if purchase.tariff and purchase.tariff.name else ''
        period_days = purchase.period_days
        period_text = f'{period_days} дн.' if period_days else ''
        tariff_text = f'{tariff_name} — {period_text}' if tariff_name else period_text

        await edit_or_answer_photo(
            callback=callback,
            caption=texts.t(
                'GIFT_ACTIVATION_CALLBACK_SUCCESS_TEXT',
                '✅ <b>Подарок активирован!</b>\n{tariff_text}\n\nВаша подписка обновлена.',
            ).format(
                tariff_text=tariff_text,
            ),
            keyboard=None,
            **await get_section_media_kwargs(db, SECTION_IMAGE_MY_SUBSCRIPTIONS, language=user.language),
        )


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(handle_gift_activate, F.data.startswith('gift_activate:'))
