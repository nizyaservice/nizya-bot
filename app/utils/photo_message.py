import asyncio
from contextlib import suppress

import structlog
from aiogram import types
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.types import InaccessibleMessage, InputMediaPhoto

from app.config import settings
from app.services.section_image_service import remember_file_photo
from app.utils.screen_delivery import force_new_screen_enabled

from .message_patch import (
    LOGO_PATH,
    _cache_logo_file_id,
    append_privacy_hint,
    caption_exceeds_telegram_limit,
    get_logo_media,
    is_not_modified_error,
    is_privacy_restricted_error,
    is_qr_message,
    prepare_privacy_safe_kwargs,
)


logger = structlog.get_logger(__name__)

MAX_RETRIES = 3
RETRY_DELAY = 0.5
_MEDIA_UNSET = object()


async def _auto_section_media(keyboard, telegram_id: int | None = None) -> object:
    """Если по клавиатуре распознаётся секция с загруженной картинкой — её file_id.

    telegram_id (callback.from_user.id) даёт язык юзера: en-юзер получает
    en-вариант баннера и в автодетекте, не только в явных проводках.
    """
    from app.services.section_image_service import get_user_language_cached, resolve_section_media
    from app.utils.menu_section_detect import detect_section_key

    try:
        section_key = detect_section_key(keyboard)
        if section_key:
            language = await get_user_language_cached(telegram_id)
            media = await resolve_section_media(section_key, language)
            if media:
                return media
    except Exception:
        pass
    return _MEDIA_UNSET


def _resolve_media(message: types.Message, media=_MEDIA_UNSET):
    if media is not _MEDIA_UNSET:
        return media
    if isinstance(message, InaccessibleMessage):
        return get_logo_media()
    if settings.ENABLE_LOGO_MODE and not is_qr_message(message):
        return get_logo_media()
    if message.photo and not is_qr_message(message):
        return message.photo[-1].file_id
    return get_logo_media()


def _bot_of(event) -> object | None:
    """Бот из события; ``None``, если контекст бота не проставлен.

    Нужен там, где отвечать приходится новым сообщением, а методов отправки у
    объекта нет — см. ``InaccessibleMessage``. Обращение к ``event.bot`` вне
    контекста (в тестах и у отвязанных моделей) бросает исключение, а не
    отдаёт ``None``, поэтому гасим его здесь.
    """
    try:
        return event.bot
    except Exception:
        return None


def _get_language(callback: types.CallbackQuery) -> str | None:
    try:
        user = callback.from_user
        if user and getattr(user, 'language_code', None):
            return user.language_code
    except AttributeError:
        pass
    return None


def _build_base_kwargs(keyboard: types.InlineKeyboardMarkup | None, parse_mode: str | None):
    kwargs: dict[str, object] = {}
    if parse_mode is not None:
        kwargs['parse_mode'] = parse_mode
    if keyboard is not None:
        kwargs['reply_markup'] = keyboard
    return kwargs


async def safe_edit_or_resend(
    message: types.Message,
    text: str,
    reply_markup: types.InlineKeyboardMarkup | None = None,
) -> None:
    """Безопасно отредактировать текст сообщения или отправить новое при ошибке.

    Если edit_text() не работает (например, для фото-уведомлений или старых сообщений),
    удаляет исходное и отправляет новое сообщение.

    Args:
        message: Целевое сообщение.
        text: Текст для отправки/редактирования.
        reply_markup: Клавиатура (опционально).
    """
    if isinstance(message, InaccessibleMessage):
        # Сообщение боту больше не видно (его удалили): ни edit_text, ни
        # answer у этой модели нет — любое обращение падает AttributeError.
        # Отвечаем новым сообщением в тот же чат.
        bot = _bot_of(message)
        if bot is None:
            logger.warning('Недоступное сообщение без контекста бота — отправить нечем')
            return
        await bot.send_message(chat_id=message.chat.id, text=text, reply_markup=reply_markup)
        return

    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        # Контент не изменился (повторное нажатие кнопки) — ничего не делаем,
        # иначе будем без нужды пересоздавать сообщение и спамить чат.
        if is_not_modified_error(error):
            return
        # Уведомление-фото или недоступное сообщение: edit_text не работает
        # Удаляем исходное и отправляем новое
        with suppress(TelegramAPIError):
            await message.delete()
        await message.answer(text, reply_markup=reply_markup)


async def _answer_text(
    callback: types.CallbackQuery,
    caption: str,
    keyboard: types.InlineKeyboardMarkup | None,
    parse_mode: str | None,
    error: TelegramBadRequest | None = None,
) -> None:
    language = _get_language(callback)
    kwargs = _build_base_kwargs(keyboard, parse_mode)

    if error and is_privacy_restricted_error(error):
        caption = append_privacy_hint(caption, language)
        kwargs = prepare_privacy_safe_kwargs(kwargs)

    kwargs.setdefault('parse_mode', parse_mode or 'HTML')

    await callback.message.answer(
        caption,
        **kwargs,
    )


async def _send_new_screen(
    bot,
    chat_id: int,
    caption: str,
    keyboard: types.InlineKeyboardMarkup,
    parse_mode: str,
    media,
    *,
    has_explicit_media: bool,
    source_message=None,
) -> None:
    """Экран новым сообщением: фото с подписью, иначе текстом.

    Два вызывающих: недоступное сообщение (его удалили, править нечего) и
    кнопка из рассылки (саму рассылку оставляем в чате). Оба шлют через ``bot``
    по ``chat_id``, потому что у ``InaccessibleMessage`` методов отправки нет,
    а у рассылки правка сообщения — ровно то, чего мы избегаем.
    """
    try:
        resolved_media = _resolve_media(source_message, media)
        if resolved_media is not None and (has_explicit_media or (settings.ENABLE_LOGO_MODE and LOGO_PATH.exists())):
            result = await bot.send_photo(
                chat_id=chat_id,
                photo=resolved_media,
                caption=caption,
                reply_markup=keyboard,
                parse_mode=parse_mode,
            )
            if not has_explicit_media:
                _cache_logo_file_id(result)
            remember_file_photo(resolved_media, result)
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=caption,
                reply_markup=keyboard,
                parse_mode=parse_mode,
            )
    except Exception as error:
        logger.warning('Не удалось отправить экран фото, пробуем текстом', error=str(error))
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=caption,
                reply_markup=keyboard,
                parse_mode=parse_mode,
            )
        except Exception as text_error:
            # Раньше тут было молчаливое pass: экран пропадал без следа.
            logger.error('Экран не доехал новым сообщением', error=str(text_error))


async def edit_or_answer_photo(
    callback: types.CallbackQuery,
    caption: str,
    keyboard: types.InlineKeyboardMarkup,
    parse_mode: str | None = 'HTML',
    *,
    force_text: bool = False,
    media=_MEDIA_UNSET,
) -> None:
    resolved_parse_mode = parse_mode or 'HTML'
    has_explicit_media = media is not _MEDIA_UNSET

    # Автодетект секции по клавиатуре: подменю inherits баннер своего раздела
    # (+ язык юзера из callback.from_user — en-юзер видит en-вариант)
    if not has_explicit_media and settings.ENABLE_LOGO_MODE:
        auto_media = await _auto_section_media(keyboard, getattr(callback.from_user, 'id', None))
        if auto_media is not _MEDIA_UNSET:
            media = auto_media
            has_explicit_media = True

    # Сообщение с кнопкой боту больше не видно (его удалили). Ветка отправляла
    # новое сообщение методами самого InaccessibleMessage — а их у этой модели
    # нет, то есть экран не доезжал вовсе: исключение гасилось, человек не
    # получал ничего. Отправляем через bot по chat_id.
    if isinstance(callback.message, InaccessibleMessage):
        bot = _bot_of(callback)
        chat = getattr(callback.message, 'chat', None)
        chat_id = getattr(chat, 'id', None) or getattr(callback.from_user, 'id', None)
        if bot is None or chat_id is None:
            logger.warning('Недоступное сообщение без бота или чата — экран отправить нечем')
            return

        await _send_new_screen(
            bot,
            chat_id,
            caption,
            keyboard,
            resolved_parse_mode,
            media,
            has_explicit_media=has_explicit_media,
            source_message=callback.message,
        )
        return

    # Нажата кнопка в сообщении рассылки: экран назначения уезжает НОВЫМ
    # сообщением, а рассылка остаётся в чате. Шлём сами, а не через
    # message.answer: явный баннер раздела (media) иначе потерялся бы в пользу
    # автодетекта по клавиатуре. См. app/utils/screen_delivery.py.
    if force_new_screen_enabled():
        bot = _bot_of(callback)
        chat = getattr(callback.message, 'chat', None)
        chat_id = getattr(chat, 'id', None) or getattr(callback.from_user, 'id', None)
        if bot is not None and chat_id is not None:
            await _send_new_screen(
                bot,
                chat_id,
                caption,
                keyboard,
                resolved_parse_mode,
                media,
                has_explicit_media=has_explicit_media,
                source_message=callback.message,
            )
            return
        # Без бота или чата отправить новым нечем — правим текущее, как раньше.
        logger.warning('Кнопка рассылки без контекста бота — экран правим на месте')

    # Если режим логотипа выключен или требуется текстовое сообщение — работаем текстом
    if force_text or (not settings.ENABLE_LOGO_MODE and not has_explicit_media):
        try:
            if callback.message.photo:
                await callback.message.delete()
                await _answer_text(callback, caption, keyboard, resolved_parse_mode)
            else:
                await callback.message.edit_text(
                    caption,
                    reply_markup=keyboard,
                    parse_mode=resolved_parse_mode,
                )
        except TelegramForbiddenError:
            logger.debug('Пользователь заблокировал бота, пропускаем')
        except TelegramBadRequest as error:
            if is_not_modified_error(error):
                # Повторный тап по той же кнопке: экран уже актуален.
                return
            try:
                await callback.message.delete()
            except Exception:
                pass
            await _answer_text(callback, caption, keyboard, resolved_parse_mode, error)
        return

    # Если текст слишком длинный для caption — отправим как текст
    if caption_exceeds_telegram_limit(caption):
        try:
            if callback.message.photo:
                await callback.message.delete()
            await _answer_text(callback, caption, keyboard, resolved_parse_mode)
        except TelegramForbiddenError:
            logger.debug('Пользователь заблокировал бота, пропускаем')
        except TelegramBadRequest as error:
            if is_not_modified_error(error):
                return
            await _answer_text(callback, caption, keyboard, resolved_parse_mode, error)
        return

    media = _resolve_media(callback.message, media)

    # Logo/section file unavailable (missing / directory bind-mount) — fall back to text.
    # See #586617: this used to surface as IsADirectoryError on every callback.
    if media is None:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await _answer_text(callback, caption, keyboard, resolved_parse_mode)
        return

    # Retry logic для сетевых ошибок
    for attempt in range(MAX_RETRIES):
        try:
            edited = await callback.message.edit_media(
                InputMediaPhoto(media=media, caption=caption, parse_mode=(parse_mode or 'HTML')),
                reply_markup=keyboard,
            )
            # Основной путь навигации между экранами: без запоминания file_id
            # картинка раздела заново уходила бы в Telegram на каждый переход.
            remember_file_photo(media, edited)
            return  # Успешно — выходим
        except TelegramNetworkError as net_error:
            if attempt < MAX_RETRIES - 1:
                logger.warning(
                    'Сетевая ошибка edit_media, повторная попытка',
                    attempt=attempt + 1,
                    MAX_RETRIES=MAX_RETRIES,
                    net_error=net_error,
                )
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                continue
            logger.error('Сетевая ошибка edit_media после попыток', MAX_RETRIES=MAX_RETRIES, net_error=net_error)
            # После всех попыток — фоллбек на текст
            try:
                await callback.message.delete()
            except Exception:
                pass
            await _answer_text(callback, caption, keyboard, resolved_parse_mode)
            return
        except OSError as os_error:
            # Logo file became unreadable mid-flight (deleted/replaced by directory).
            # No point retrying — fall back to text. See #586617.
            logger.error(
                'Не удалось прочитать логотип для edit_media — фоллбек на текст',
                os_error=str(os_error),
            )
            try:
                await callback.message.delete()
            except Exception:
                pass
            await _answer_text(callback, caption, keyboard, resolved_parse_mode)
            return
        except TelegramForbiddenError:
            # Пользователь заблокировал бота — молча игнорируем
            logger.debug('Пользователь заблокировал бота, пропускаем edit_media')
            return
        except TelegramBadRequest as error:
            if is_not_modified_error(error):
                # Экран уже такой: фоллбек ниже удалил бы сообщение с баннером
                # и прислал тот же текст заново в конец чата.
                return
            if is_privacy_restricted_error(error):
                try:
                    await callback.message.delete()
                except Exception:
                    pass
                await _answer_text(callback, caption, keyboard, resolved_parse_mode, error)
                return
            # Фоллбек: если не удалось обновить фото — отправим текст
            try:
                await callback.message.delete()
            except Exception:
                pass
            logo_media = media if has_explicit_media else get_logo_media()
            if logo_media is None:
                await _answer_text(callback, caption, keyboard, resolved_parse_mode)
                return
            try:
                # Отправим как фото с логотипом
                result = await callback.message.answer_photo(
                    photo=logo_media,
                    caption=caption,
                    reply_markup=keyboard,
                    parse_mode=resolved_parse_mode,
                )
                _cache_logo_file_id(result)
            except (TelegramBadRequest, TelegramForbiddenError) as photo_error:
                await _answer_text(callback, caption, keyboard, resolved_parse_mode, photo_error)
            except Exception:
                # Последний фоллбек — обычный текст
                await _answer_text(callback, caption, keyboard, resolved_parse_mode)
            return
