"""Общий хелпер для текстовых экранов флоу пополнения (зона balance)."""

from aiogram import types

from app.services.section_image_service import SECTION_IMAGE_BALANCE, resolve_cached_section_file_id


async def answer_topup_screen(
    message: types.Message,
    text: str,
    language: str | None = None,
    *,
    parse_mode: str | None = None,
) -> None:
    """Отправляет текстовый экран пополнения с баннером раздела «Баланс» (если картинка задана).

    Для экранов ошибок суммы/способа оплаты: клавиатура — только «назад»,
    автодетект по ней ничего не распознаёт, поэтому баннер вешаем явно.
    """
    from app.keyboards.inline import get_back_keyboard

    keyboard = get_back_keyboard(language) if language else None
    file_id = await resolve_cached_section_file_id(SECTION_IMAGE_BALANCE)
    if file_id:
        await message.answer_photo(file_id, caption=text, reply_markup=keyboard)
    else:
        await message.answer(text, reply_markup=keyboard, parse_mode=parse_mode)
