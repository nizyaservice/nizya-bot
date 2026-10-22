"""Длинный текст от администратора: сообщением или приложенным файлом.

Оферта и политика конфиденциальности сообщением помещаются не всегда: у
Telegram потолок 4096 символов на сообщение, а реальный юридический документ
легко выходит за него. Раньше это было тупиком — редактор принимал только
``message.text``, и документ приходилось сокращать под транспорт, а не под
смысл. Поэтому те же экраны принимают ещё и ``.txt``/``.html`` файлом.

Побочно закрыта давняя дыра: обработчики читали ``message.text or ''`` без
проверки, и любое нетекстовое сообщение в режиме редактирования (фото,
стикер, пересланный файл) сохраняло ПУСТОЙ документ, молча стирая текущий.
``read_long_text`` на пустой ввод возвращает ошибку, а не пустую строку.
"""

from __future__ import annotations

import io

import structlog
from aiogram import types


logger = structlog.get_logger(__name__)

# Потолок на файл. Документ на 128 КБ — это примерно 60 тысяч символов
# кириллицы, вчетверо больше самого длинного разумного соглашения.
MAX_DOCUMENT_BYTES = 128 * 1024

ALLOWED_SUFFIXES = ('.txt', '.html', '.htm')


async def read_long_text(message: types.Message, texts) -> tuple[str | None, str | None]:
    """``(текст, ошибка)``: ровно одно из двух не None.

    Ошибка уже локализована и готова к отправке администратору.
    """
    document = message.document

    if document is None:
        text = (message.text or '').strip()
        if not text:
            return None, texts.t(
                'ADMIN_LONG_TEXT_EMPTY',
                '❌ Пустой текст. Отправьте содержимое документа сообщением или файлом .txt.',
            )
        return text, None

    name = (document.file_name or '').lower()
    if not name.endswith(ALLOWED_SUFFIXES):
        return None, texts.t(
            'ADMIN_LONG_TEXT_BAD_FORMAT',
            '❌ Поддерживаются только файлы .txt и .html. Пришлите документ в одном из этих форматов.',
        )

    if (document.file_size or 0) > MAX_DOCUMENT_BYTES:
        return None, texts.t(
            'ADMIN_LONG_TEXT_FILE_TOO_BIG',
            '❌ Файл больше {limit} КБ.',
        ).format(limit=MAX_DOCUMENT_BYTES // 1024)

    buffer = io.BytesIO()
    try:
        await message.bot.download(document, destination=buffer)
    except Exception as error:
        logger.warning('Не удалось скачать файл документа', error=str(error))
        return None, texts.t(
            'ADMIN_LONG_TEXT_DOWNLOAD_FAILED',
            '❌ Не удалось скачать файл. Попробуйте ещё раз.',
        )

    buffer.seek(0)
    raw = buffer.read()

    # utf-8-sig снимает BOM: Блокнот и Word добавляют его молча, а в тексте
    # он виден как мусорный символ в самом начале документа.
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return None, texts.t(
            'ADMIN_LONG_TEXT_BAD_ENCODING',
            '❌ Файл не в UTF-8. Пересохраните его в кодировке UTF-8.',
        )

    text = text.replace('\r\n', '\n').strip()
    if not text:
        return None, texts.t(
            'ADMIN_LONG_TEXT_EMPTY',
            '❌ Пустой текст. Отправьте содержимое документа сообщением или файлом .txt.',
        )
    return text, None
