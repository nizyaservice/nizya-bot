"""Подмена стандартных эмодзи на кастомные из премиум-набора бренда.

Telegram рисует кастомный эмодзи двумя разными способами, и оба здесь нужны:

* в тексте сообщения — тег ``<tg-emoji emoji-id="…">😀</tg-emoji>`` при parse_mode
  HTML (внутри тега обязан стоять обычный эмодзи: его увидят те, кому кастомный
  не доступен);
* на инлайн-кнопке — поле ``icon_custom_emoji_id``, потому что текст кнопки
  разметку не поддерживает. Иконка рисуется ПЕРЕД подписью, поэтому ведущий
  юникод-эмодзи из подписи убирается — иначе значок задвоится.

Карта «эмодзи → id» лежит в ``custom_emoji_ids.json`` в корне проекта. Эмодзи,
которых в карте нет, остаются как были.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import structlog
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


logger = structlog.get_logger(__name__)

CUSTOM_EMOJI_MAP_PATH = Path(__file__).resolve().parents[2] / 'custom_emoji_ids.json'

VARIATION_SELECTOR = '️'

# Куски текста между HTML-тегами: внутрь тегов и их атрибутов лезть нельзя.
_TAG_SPLIT_RE = re.compile(r'(<[^>]*>)')
# Уже готовый кастомный эмодзи — второй раз оборачивать нечего.
_TG_EMOJI_BLOCK_RE = re.compile(r'<tg-emoji\b[^>]*>.*?</tg-emoji>', re.DOTALL | re.IGNORECASE)


@lru_cache(maxsize=1)
def get_custom_emoji_map() -> dict[str, str]:
    """{эмодзи: custom_emoji_id}. Пустая карта = функция выключена."""
    try:
        raw = json.loads(CUSTOM_EMOJI_MAP_PATH.read_text(encoding='utf-8'))
    except FileNotFoundError:
        logger.info('Custom emoji map not found, standard emoji are used', path=str(CUSTOM_EMOJI_MAP_PATH))
        return {}
    except (OSError, ValueError) as error:
        logger.warning('Unable to read custom emoji map', path=str(CUSTOM_EMOJI_MAP_PATH), error=str(error))
        return {}

    mapping: dict[str, str] = {}
    for emoji, emoji_id in raw.items():
        if isinstance(emoji, str) and isinstance(emoji_id, str) and emoji and emoji_id.isdigit():
            mapping[emoji] = emoji_id
            # Вариант без VS16 пишут так же часто, как с ним: '⭐' и '⭐️' — один значок.
            mapping.setdefault(emoji.replace(VARIATION_SELECTOR, ''), emoji_id)
    return mapping


@lru_cache(maxsize=1)
def _emoji_pattern() -> re.Pattern[str] | None:
    mapping = get_custom_emoji_map()
    if not mapping:
        return None
    # Длинные варианты первыми: '⚠️' должен выиграть у '⚠'.
    alternatives = sorted(mapping, key=len, reverse=True)
    return re.compile('|'.join(re.escape(e) for e in alternatives))


def reset_cache() -> None:
    """Сбросить кеш карты (после правки файла и в тестах)."""
    get_custom_emoji_map.cache_clear()
    _emoji_pattern.cache_clear()


def _wrap_plain_chunk(chunk: str, pattern: re.Pattern[str], mapping: dict[str, str]) -> str:
    return pattern.sub(lambda m: f'<tg-emoji emoji-id="{mapping[m.group(0)]}">{m.group(0)}</tg-emoji>', chunk)


def apply_to_html(text: str | None) -> str | None:
    """Обернуть известные эмодзи в <tg-emoji>. Только для parse_mode=HTML.

    Содержимое самих тегов и уже обёрнутые эмодзи не трогаются, поэтому повторный
    прогон того же текста (например, при редактировании сообщения) ничего не меняет.
    """
    pattern = _emoji_pattern()
    if not text or pattern is None:
        return text

    mapping = get_custom_emoji_map()
    result: list[str] = []
    position = 0
    # Готовые <tg-emoji>…</tg-emoji> переносим как есть, всё между ними — обрабатываем.
    for block in _TG_EMOJI_BLOCK_RE.finditer(text):
        result.append(_wrap_outside_tags(text[position : block.start()], pattern, mapping))
        result.append(block.group(0))
        position = block.end()
    result.append(_wrap_outside_tags(text[position:], pattern, mapping))
    return ''.join(result)


def _wrap_outside_tags(fragment: str, pattern: re.Pattern[str], mapping: dict[str, str]) -> str:
    parts = _TAG_SPLIT_RE.split(fragment)
    # Нечётные позиции — сами теги (<b>, <a href="…">), их содержимое неприкосновенно.
    return ''.join(part if index % 2 else _wrap_plain_chunk(part, pattern, mapping) for index, part in enumerate(parts))


def split_leading_emoji(text: str | None) -> tuple[str | None, str | None]:
    """(custom_emoji_id, текст без ведущего эмодзи) либо (None, исходный текст)."""
    pattern = _emoji_pattern()
    if not text or pattern is None:
        return None, text

    match = pattern.match(text.lstrip())
    if not match:
        return None, text

    stripped = text.lstrip()[match.end() :].lstrip()
    if not stripped:
        # Подпись из одного эмодзи: без текста кнопка станет пустой — оставляем как есть.
        return None, text
    return get_custom_emoji_map()[match.group(0)], stripped


def apply_to_markup(markup: InlineKeyboardMarkup | None) -> InlineKeyboardMarkup | None:
    """Ведущий эмодзи подписи кнопки -> icon_custom_emoji_id.

    Кнопки, у которых иконка уже задана, и кнопки без известного эмодзи
    возвращаются нетронутыми.
    """
    if markup is None or _emoji_pattern() is None or not markup.inline_keyboard:
        return markup

    changed = False
    rows: list[list[InlineKeyboardButton]] = []
    for row in markup.inline_keyboard:
        new_row: list[InlineKeyboardButton] = []
        for button in row:
            if button.icon_custom_emoji_id:
                new_row.append(button)
                continue
            emoji_id, text = split_leading_emoji(button.text)
            if not emoji_id:
                new_row.append(button)
                continue
            new_row.append(button.model_copy(update={'text': text, 'icon_custom_emoji_id': emoji_id}))
            changed = True
        rows.append(new_row)

    return InlineKeyboardMarkup(inline_keyboard=rows) if changed else markup
