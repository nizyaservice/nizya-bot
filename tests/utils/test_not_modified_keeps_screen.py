"""«message is not modified» не должен пересоздавать экран.

Так Telegram отвечает на повторный тап по той же кнопке: сообщение уже
выглядит так, как мы его отправляем. Фоллбек на этот случай удалял сообщение
и присылал его заново — экран с баннером терял картинку и уезжал в конец чата,
хотя делать было нечего. Весь остальной репозиторий трактует эту ошибку как
«ничего не делаем» (``@error_handler`` в ``app/utils/decorators.py``).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.utils import message_patch as mp
from app.utils import photo_message as pm


NOT_MODIFIED = TelegramBadRequest(method=None, message='Bad Request: message is not modified')
OTHER_ERROR = TelegramBadRequest(method=None, message='Bad Request: wrong file identifier')


def _callback(edit_error: Exception | None) -> SimpleNamespace:
    message = SimpleNamespace(
        photo=[SimpleNamespace(file_id='screen_photo')],
        caption='экран',
        edit_media=AsyncMock(side_effect=edit_error),
        delete=AsyncMock(),
        answer=AsyncMock(),
        answer_photo=AsyncMock(),
    )
    return SimpleNamespace(message=message, from_user=SimpleNamespace(id=1, language_code='ru'), answer=AsyncMock())


@pytest.mark.asyncio
async def test_edit_media_not_modified_keeps_message(monkeypatch):
    monkeypatch.setattr(pm.settings, 'ENABLE_LOGO_MODE', True)
    callback = _callback(NOT_MODIFIED)

    await pm.edit_or_answer_photo(callback=callback, caption='экран', keyboard=None, media='banner_file_id')

    callback.message.delete.assert_not_called()
    callback.message.answer.assert_not_called()
    callback.message.answer_photo.assert_not_called()


@pytest.mark.asyncio
async def test_other_bad_request_still_falls_back_to_text(monkeypatch):
    """Остальные ошибки Telegram по-прежнему пересоздают экран."""
    monkeypatch.setattr(pm.settings, 'ENABLE_LOGO_MODE', True)
    # Фоллбек отправляет фото и кеширует его file_id в модульной глобали —
    # мок-file_id из теста не должен утечь в остальные тесты.
    monkeypatch.setattr(pm, '_cache_logo_file_id', lambda result: None)
    callback = _callback(OTHER_ERROR)

    await pm.edit_or_answer_photo(callback=callback, caption='экран', keyboard=None, media='banner_file_id')

    callback.message.delete.assert_awaited()
    assert callback.message.answer.await_count or callback.message.answer_photo.await_count


@pytest.mark.asyncio
async def test_patched_edit_text_not_modified_keeps_photo(monkeypatch, tmp_path):
    """Патченный ``Message.edit_text`` на фото-сообщении: та же защита."""
    monkeypatch.setattr(mp.settings, 'ENABLE_LOGO_MODE', True)
    # Логотипа нет на диске: медиа берётся из самого сообщения, тест не зависит
    # от файлов репозитория.
    monkeypatch.setattr(mp, 'LOGO_PATH', tmp_path / 'нет-такого.png')

    message = SimpleNamespace(
        photo=[SimpleNamespace(file_id='screen_photo')],
        text=None,
        chat=SimpleNamespace(id=-100),
        from_user=SimpleNamespace(id=1, language_code='ru'),
        edit_media=AsyncMock(side_effect=NOT_MODIFIED),
        delete=AsyncMock(),
        answer=AsyncMock(),
    )

    result = await mp._edit_with_photo(message, 'экран')

    assert result is None
    message.delete.assert_not_called()
    message.answer.assert_not_called()


def test_not_modified_predicate_ignores_other_errors():
    assert mp.is_not_modified_error(NOT_MODIFIED)
    assert not mp.is_not_modified_error(OTHER_ERROR)
    assert not mp.is_not_modified_error(ValueError('message is not modified'))
