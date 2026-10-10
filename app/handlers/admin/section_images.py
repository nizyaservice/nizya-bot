import html

import structlog
from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.localization.texts import get_texts
from app.services.section_image_service import (
    SECTION_IMAGE_DEFINITIONS,
    delete_section_image_file_id,
    get_section_image_definition,
    get_section_image_file_id,
    invalidate_section_cache,
    set_section_image_file_id,
)
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler


logger = structlog.get_logger(__name__)


def _get_section_title(texts, section_key: str) -> str:
    definition = get_section_image_definition(section_key)
    if definition is None:
        return section_key
    return texts.t(definition.title_key, definition.title_default)


def _get_section_images_keyboard(language: str) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    rows = [
        [
            InlineKeyboardButton(
                text=_get_section_title(texts, definition.key),
                callback_data=f'admin_section_image:{definition.key}',
            )
        ]
        for definition in SECTION_IMAGE_DEFINITIONS
    ]
    rows.append([InlineKeyboardButton(text=texts.BACK, callback_data='admin_submenu_settings')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _get_section_image_actions_keyboard(language: str, section_key: str, has_photo: bool) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    rows = [
        [
            InlineKeyboardButton(
                text=texts.t('ADMIN_SECTION_IMAGE_EDIT', '🖼️ Установить / изменить'),
                callback_data=f'admin_section_image_edit:{section_key}',
            )
        ]
    ]
    if has_photo:
        rows.append(
            [
                InlineKeyboardButton(
                    text=texts.t('ADMIN_SECTION_IMAGE_DELETE', '🗑️ Удалить изображение'),
                    callback_data=f'admin_section_image_delete:{section_key}',
                )
            ]
        )
    rows.append([InlineKeyboardButton(text=texts.BACK, callback_data='admin_section_images')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _get_section_image_cancel_keyboard(language: str, section_key: str) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.t('ADMIN_SECTION_IMAGE_CANCEL', '⬅️ Отмена'),
                    callback_data=f'admin_section_image_cancel:{section_key}',
                )
            ]
        ]
    )


def _build_section_images_list_text(texts) -> str:
    return (
        f'{texts.t("ADMIN_SECTION_IMAGES_TITLE", "🖼️ <b>Управление изображениями разделов</b>")}\n\n'
        f'{texts.t("ADMIN_SECTION_IMAGES_DESCRIPTION", "Выберите раздел, для которого нужно установить или удалить изображение.")}'
    )


async def _edit_text_or_replace(
    callback: types.CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    *,
    parse_mode: str = 'HTML',
) -> None:
    if callback.message.photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        return

    await callback.message.edit_text(
        text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )


def _build_section_image_detail_text(texts, section_key: str, file_id: str | None, *, success_text: str | None = None) -> str:
    section_title = html.escape(_get_section_title(texts, section_key))
    status_text = (
        texts.t('ADMIN_SECTION_IMAGE_STATUS_SET', 'установлено')
        if file_id
        else texts.t('ADMIN_SECTION_IMAGE_STATUS_EMPTY', 'не установлено')
    )
    lines = [
        texts.t('ADMIN_SECTION_IMAGE_DETAIL_TITLE', '🖼️ <b>Изображение раздела</b>'),
        '',
        f'{texts.t("ADMIN_SECTION_IMAGE_SECTION_LABEL", "Раздел")}: <b>{section_title}</b>',
        f'{texts.t("ADMIN_SECTION_IMAGE_STATUS_LABEL", "Статус")}: <b>{status_text}</b>',
    ]

    if file_id:
        lines.append(f'{texts.t("ADMIN_SECTION_IMAGE_FILE_ID_LABEL", "File ID")}: <code>{html.escape(file_id)}</code>')
    else:
        lines.append(
            texts.t(
                'ADMIN_SECTION_IMAGE_EMPTY_HINT',
                'Для этого раздела будет использоваться стандартное изображение бота или текстовый режим.',
            )
        )

    lines.extend(
        [
            '',
            texts.t(
                'ADMIN_SECTION_IMAGE_RUNTIME_HINT',
                'При обновлении текста раздела бот автоматически возьмёт актуальное изображение из этой настройки.',
            ),
        ]
    )

    if success_text:
        lines.extend(['', success_text])

    return '\n'.join(lines)


async def _render_section_image_detail_callback(
    callback: types.CallbackQuery,
    language: str,
    section_key: str,
    file_id: str | None,
    *,
    success_text: str | None = None,
) -> None:
    texts = get_texts(language)
    caption = _build_section_image_detail_text(texts, section_key, file_id, success_text=success_text)
    keyboard = _get_section_image_actions_keyboard(language, section_key, has_photo=bool(file_id))

    if file_id:
        if callback.message.photo:
            await callback.message.edit_media(
                InputMediaPhoto(media=file_id, caption=caption, parse_mode='HTML'),
                reply_markup=keyboard,
            )
        else:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer_photo(
                photo=file_id,
                caption=caption,
                reply_markup=keyboard,
                parse_mode='HTML',
            )
        return

    if callback.message.photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(
            caption,
            reply_markup=keyboard,
            parse_mode='HTML',
        )
        return

    await callback.message.edit_text(
        caption,
        reply_markup=keyboard,
        parse_mode='HTML',
    )


async def _send_section_image_detail_message(
    message: types.Message,
    language: str,
    section_key: str,
    file_id: str | None,
    *,
    success_text: str | None = None,
) -> None:
    texts = get_texts(language)
    caption = _build_section_image_detail_text(texts, section_key, file_id, success_text=success_text)
    keyboard = _get_section_image_actions_keyboard(language, section_key, has_photo=bool(file_id))

    if file_id:
        await message.answer_photo(
            photo=file_id,
            caption=caption,
            reply_markup=keyboard,
            parse_mode='HTML',
        )
        return

    await message.answer(
        caption,
        reply_markup=keyboard,
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def show_section_images_panel(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    await _edit_text_or_replace(
        callback,
        _build_section_images_list_text(texts),
        reply_markup=_get_section_images_keyboard(db_user.language),
    )
    await callback.answer()


@admin_required
@error_handler
async def show_section_image_detail(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    section_key = callback.data.split(':', 1)[1]
    if get_section_image_definition(section_key) is None:
        await callback.answer('Неизвестный раздел', show_alert=True)
        return

    file_id = await get_section_image_file_id(db, section_key)
    await _render_section_image_detail_callback(callback, db_user.language, section_key, file_id)
    await callback.answer()


@admin_required
@error_handler
async def start_section_image_edit(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
):
    section_key = callback.data.split(':', 1)[1]
    if get_section_image_definition(section_key) is None:
        await callback.answer('Неизвестный раздел', show_alert=True)
        return

    texts = get_texts(db_user.language)
    section_title = html.escape(_get_section_title(texts, section_key))
    await state.set_state(AdminStates.editing_section_image)
    await state.update_data(section_key=section_key)
    await _edit_text_or_replace(
        callback,
        (
            f'{texts.t("ADMIN_SECTION_IMAGE_UPLOAD_TITLE", "🖼️ <b>Загрузка изображения</b>")}\n\n'
            f'{texts.t("ADMIN_SECTION_IMAGE_SECTION_LABEL", "Раздел")}: <b>{section_title}</b>\n\n'
            f'{texts.t("ADMIN_SECTION_IMAGE_UPLOAD_PROMPT", "Отправьте новое изображение как фото. Бот сохранит его file_id и начнёт использовать в выбранном разделе.")}'
        ),
        reply_markup=_get_section_image_cancel_keyboard(db_user.language, section_key),
    )
    await callback.answer()


@admin_required
@error_handler
async def cancel_section_image_edit(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
):
    section_key = callback.data.split(':', 1)[1]
    await state.clear()
    file_id = await get_section_image_file_id(db, section_key)
    await _render_section_image_detail_callback(callback, db_user.language, section_key, file_id)
    await callback.answer()


@admin_required
@error_handler
async def delete_section_image(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    section_key = callback.data.split(':', 1)[1]
    if get_section_image_definition(section_key) is None:
        await callback.answer('Неизвестный раздел', show_alert=True)
        return

    await delete_section_image_file_id(db, section_key)
    await invalidate_section_cache()
    await _render_section_image_detail_callback(
        callback,
        db_user.language,
        section_key,
        None,
        success_text=get_texts(db_user.language).t(
            'ADMIN_SECTION_IMAGE_DELETE_SUCCESS',
            '✅ Изображение удалено. Теперь для раздела будет использоваться стандартный режим.',
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def save_section_image(
    message: types.Message,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
):
    state_data = await state.get_data()
    section_key = state_data.get('section_key')

    if get_section_image_definition(section_key) is None:
        await state.clear()
        await message.answer('Не удалось определить раздел для изображения.')
        return

    if not message.photo:
        await message.answer(
            get_texts(db_user.language).t(
                'ADMIN_SECTION_IMAGE_EXPECT_PHOTO',
                'Отправьте именно фото. Документы и другие типы файлов здесь не поддерживаются.',
            ),
            reply_markup=_get_section_image_cancel_keyboard(db_user.language, section_key),
        )
        return

    file_id = message.photo[-1].file_id
    await set_section_image_file_id(db, section_key, file_id)
    await invalidate_section_cache()
    await state.clear()

    logger.info(
        'Обновлено изображение раздела',
        admin_id=db_user.id,
        section_key=section_key,
        file_id=file_id,
    )

    await _send_section_image_detail_message(
        message,
        db_user.language,
        section_key,
        file_id,
        success_text=get_texts(db_user.language).t(
            'ADMIN_SECTION_IMAGE_SAVE_SUCCESS',
            '✅ Изображение сохранено и будет использовано при следующем открытии раздела.',
        ),
    )


@admin_required
@error_handler
async def reject_non_photo_section_image(
    message: types.Message,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
):
    state_data = await state.get_data()
    section_key = state_data.get('section_key') or ''
    await message.answer(
        get_texts(db_user.language).t(
            'ADMIN_SECTION_IMAGE_EXPECT_PHOTO',
            'Отправьте именно фото. Документы и другие типы файлов здесь не поддерживаются.',
        ),
        reply_markup=_get_section_image_cancel_keyboard(db_user.language, section_key) if section_key else None,
    )


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_section_images_panel, F.data == 'admin_section_images')
    dp.callback_query.register(show_section_image_detail, F.data.startswith('admin_section_image:'))
    dp.callback_query.register(start_section_image_edit, F.data.startswith('admin_section_image_edit:'))
    dp.callback_query.register(delete_section_image, F.data.startswith('admin_section_image_delete:'))
    dp.callback_query.register(cancel_section_image_edit, F.data.startswith('admin_section_image_cancel:'))

    dp.message.register(save_section_image, AdminStates.editing_section_image, F.photo)
    dp.message.register(reject_non_photo_section_image, AdminStates.editing_section_image)
