"""Имя устройства HWID для экранов бота — одно место на все рендеры.

Строка собиралась в трёх местах (кнопка списка, текст списка, сообщение об
удалении) тремя почти одинаковыми фрагментами и расходилась: кнопка резала
имя на 22 символах, текст — на 35, а сообщение об удалении игнорировало
локальный alias и показывало платформенную строку. Теперь формат один, а
обрезка — отдельная функция и только там, где она правда нужна.
"""

from __future__ import annotations


# Telegram не ограничивает текст inline-кнопки жёстко и переносит длинный
# лейбл на несколько строк, поэтому режем только по длине алиаса
# (ALIAS_MAX_LENGTH = 64) — чтобы платформенная строка не раздувала кнопку
# сильнее, чем пользователь может сделать это сам.
DEVICE_LABEL_LIMIT = 64


def device_display_name(device: dict) -> str:
    """Имя устройства: заданный пользователем alias приоритетнее платформы.

    ``local_name`` проставляет ``attach_aliases_to_devices()`` до рендера;
    без него остаётся «<платформа> - <модель>».
    """
    local_name = (device.get('local_name') or '').strip()
    if local_name:
        return local_name

    platform = (device.get('platform') or '').strip() or 'Unknown'
    model = (device.get('deviceModel') or device.get('model') or '').strip() or 'Unknown'
    return f'{platform} - {model}'


def shorten_device_label(name: str, limit: int = DEVICE_LABEL_LIMIT) -> str:
    """Обрезает имя до ``limit`` символов, добавляя многоточие."""
    text = (name or '').strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + '…'
