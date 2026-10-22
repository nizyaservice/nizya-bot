"""Оферта и политика: приём длинного текста сообщением и файлом.

Документ на 5000+ символов сообщением не отправить — у Telegram потолок 4096
символов. Поэтому редакторы принимают ещё и .txt/.html файлом, а лимит на сам
документ поднят до MAX_CONTENT_LENGTH и ограничен показом, а не транспортом.

Здесь же зафиксирована дыра, которая существовала до этого: обработчики читали
``message.text or ''`` без проверки, и любое нетекстовое сообщение в режиме
редактирования молча сохраняло ПУСТОЙ документ поверх действующего.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.handlers.admin.privacy_policy as policy_mod
from app.services.privacy_policy_service import PrivacyPolicyService
from app.services.public_offer_service import PublicOfferService
from app.utils.admin_text_input import MAX_DOCUMENT_BYTES, read_long_text


class _Texts:
    """Заглушка локализации: возвращает дефолт, как и настоящая при пустом ключе."""

    def t(self, key: str, default: str) -> str:
        return default


def _unwrap(fn):
    while hasattr(fn, '__wrapped__'):
        fn = fn.__wrapped__
    return fn


def _document_message(payload: bytes, *, name: str = 'policy.txt', size: int | None = None):
    message = MagicMock()
    message.text = None
    message.document = SimpleNamespace(file_name=name, file_size=size if size is not None else len(payload))

    async def fake_download(document, destination):
        destination.write(payload)

    message.bot = MagicMock()
    message.bot.download = AsyncMock(side_effect=fake_download)
    return message


def _text_message(text: str | None):
    message = MagicMock()
    message.text = text
    message.document = None
    return message


# ---- чтение из сообщения ----


async def test_plain_text_is_taken_as_is() -> None:
    text, error = await read_long_text(_text_message('  <b>Оферта</b>  '), _Texts())

    assert error is None
    assert text == '<b>Оферта</b>'


async def test_document_is_downloaded_and_decoded() -> None:
    body = '<b>Соглашение</b>\n\n1.1. Пункт.'
    text, error = await read_long_text(_document_message(body.encode('utf-8')), _Texts())

    assert error is None
    assert text == body


async def test_document_longer_than_a_telegram_message_is_accepted() -> None:
    """Ради этого всё и делалось: 5000 символов в одно сообщение не влезают."""
    body = 'я' * 5000
    text, error = await read_long_text(_document_message(body.encode('utf-8')), _Texts())

    assert error is None
    assert len(text) == 5000
    assert len(text) <= PrivacyPolicyService.MAX_CONTENT_LENGTH
    assert len(text) <= PublicOfferService.MAX_CONTENT_LENGTH


async def test_bom_is_stripped() -> None:
    """Блокнот и Word дописывают BOM молча — в тексте он виден как мусор в начале."""
    text, error = await read_long_text(_document_message('﻿Текст'.encode()), _Texts())

    assert error is None
    assert text == 'Текст'


async def test_crlf_is_normalised() -> None:
    text, error = await read_long_text(_document_message(b'a\r\nb'), _Texts())

    assert error is None
    assert text == 'a\nb'


# ---- отказы ----


@pytest.mark.parametrize('payload', [None, '', '   \n  '])
async def test_empty_input_is_rejected(payload) -> None:
    text, error = await read_long_text(_text_message(payload), _Texts())

    assert text is None
    assert 'Пустой текст' in error


async def test_empty_document_is_rejected() -> None:
    text, error = await read_long_text(_document_message(b'   \n'), _Texts())

    assert text is None
    assert 'Пустой текст' in error


async def test_unsupported_extension_is_rejected_without_download() -> None:
    message = _document_message('что-то'.encode(), name='policy.pdf')

    text, error = await read_long_text(message, _Texts())

    assert text is None
    assert '.txt' in error
    message.bot.download.assert_not_awaited()


async def test_oversized_file_is_rejected_without_download() -> None:
    message = _document_message(b'x', name='policy.txt', size=MAX_DOCUMENT_BYTES + 1)

    text, error = await read_long_text(message, _Texts())

    assert text is None
    assert str(MAX_DOCUMENT_BYTES // 1024) in error
    message.bot.download.assert_not_awaited()


async def test_non_utf8_file_is_rejected() -> None:
    text, error = await read_long_text(_document_message('Текст'.encode('cp1251')), _Texts())

    assert text is None
    assert 'UTF-8' in error


async def test_download_failure_does_not_crash() -> None:
    message = _document_message(b'ok')
    message.bot.download = AsyncMock(side_effect=RuntimeError('сеть'))

    text, error = await read_long_text(message, _Texts())

    assert text is None
    assert 'скачать' in error


# ---- сквозь обработчик ----


async def test_non_text_message_does_not_wipe_the_policy(monkeypatch) -> None:
    """Регрессия: раньше фото в режиме редактирования сохраняло пустой документ.

    ``message.text`` у фото равен None, прежний код брал ``or ''`` и писал
    пустую строку поверх действующей политики, ничего не сообщая админу.
    """
    save = AsyncMock()
    monkeypatch.setattr(PrivacyPolicyService, 'save_policy', save)

    message = MagicMock()
    message.text = None
    message.document = None
    message.answer = AsyncMock()
    state = MagicMock()
    state.clear = AsyncMock()

    await _unwrap(policy_mod.process_privacy_policy_edit)(
        message, SimpleNamespace(language='ru', telegram_id=1), state, MagicMock()
    )

    save.assert_not_awaited()
    assert 'Пустой текст' in message.answer.await_args.args[0]


async def test_document_reaches_the_service(monkeypatch) -> None:
    save = AsyncMock()
    monkeypatch.setattr(PrivacyPolicyService, 'save_policy', save)

    body = '<b>Политика</b>\n\n' + 'текст ' * 1000  # заведомо длиннее сообщения
    message = _document_message(body.encode('utf-8'))
    message.answer = AsyncMock()
    state = MagicMock()
    state.clear = AsyncMock()

    await _unwrap(policy_mod.process_privacy_policy_edit)(
        message, SimpleNamespace(language='ru', telegram_id=1), state, MagicMock()
    )

    save.assert_awaited_once()
    assert save.await_args.args[2] == body.strip()
