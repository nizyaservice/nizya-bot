#!/usr/bin/env python3
"""Нарезка иллюстраций Низи для портала из баннеров разделов бота.

Баннеры (``menu_images/<раздел>.png``, 1024×572) — это рисунок Низи на почти
однородном тёмном фоне, под ним словесный знак «Nizya VPN». Порталу нужен
только рисунок, поэтому скрипт отрезает нижнюю часть со знаком, обрезает поля
по краям рисунка и растушёвывает границы кадра.

Фон не вырезается нарочно: толстовка Низи почти того же цвета, что и фон
(см. ``BACKDROP``), и любое удаление фона по цвету пробивает в ней дыры.
Вместо этого кадр остаётся прямоугольным, его края уходят в прозрачность, а
страница под ним красится ровно в ``BACKDROP`` — шва не видно.

Запуск: ``python3 portal/tools/extract_mascots.py`` (нужен Pillow).
Результат — ``portal/static/img/mascot/*.webp``, он коммитится в репозиторий:
на проде ни Pillow, ни баннеров бота нет.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops


REPO = Path(__file__).resolve().parents[2]
BANNERS = REPO / 'menu_images'
OUT = REPO / 'portal' / 'static' / 'img' / 'mascot'

#: Цвет фона баннеров. Тот же цвет задан в CSS как ``--art-bg``; разойдутся —
#: вокруг иллюстраций появится прямоугольник.
BACKDROP = '#0b0f1b'

#: Докуда рисунок, дальше словесный знак «Nizya VPN» (доля высоты баннера).
ART_BOTTOM = 0.60

#: Поле вокруг найденного рисунка и ширина растушёвки края, в пикселях готовой
#: картинки. Растушёвка широкая нарочно: узкая оставляла заметную границу.
PADDING = 18
FEATHER = 90

#: Ширина готовой картинки: показываем её максимум на ~330 CSS-пикселей,
#: двойной запас хватает экранам с удвоенной плотностью.
TARGET_WIDTH = 660

#: Куда какой баннер идёт. Ключ — имя файла портала, значение — путь баннера.
SOURCES = {
    'hero': 'active_subscription/_main.png',
    'login': 'main_menu.png',
    'subscription': 'my_subscriptions/_main.png',
    'tariffs': 'subscription_purchase/_main.png',
    'devices': 'subscription/devices.png',
    'promocode': 'promocode/_main.png',
    'referral': 'referrals/_main.png',
    'status': 'server_status/_main.png',
    'balance': 'balance/topup.png',
    'purchased': 'subscription_success/_main.png',
    'paid': 'topup_success/_main.png',
    'oops': 'faq/_main.png',
}


def content_box(image: Image.Image) -> tuple[int, int, int, int]:
    """Рамка рисунка: всё, что заметно светлее или цветнее фона."""
    red, green, blue = image.convert('RGB').split()
    brightest = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    darkest = ImageChops.darker(ImageChops.darker(red, green), blue)
    # Два признака: яркость (кожа, блики) и насыщенность (лайм, фиолетовый).
    # Фон даёт мало и того, и другого, а чёрная толстовка видна по контуру.
    lit = brightest.point(lambda value: 255 if value > 70 else 0)
    colored = ImageChops.difference(brightest, darkest).point(lambda value: 255 if value > 45 else 0)
    box = ImageChops.lighter(lit, colored).getbbox()
    if box is None:
        message = 'на баннере не нашлось рисунка'
        raise ValueError(message)
    return box


def levelled(image: Image.Image, backdrop: tuple[int, int, int]) -> Image.Image:
    """Сдвинуть фон кадра ровно в цвет страницы.

    На баннере фон не идеально ровный: под рисунком лежат свечение и
    диагональная сетка, и срез получается на один-два тона светлее страницы —
    вокруг картинки виден аккуратный прямоугольник. Берём настоящий цвет фона
    по рамке кадра (там рисунка заведомо нет) и сдвигаем все каналы на разницу
    с цветом страницы. Сдвиг одинаков для всей картинки, поэтому сам рисунок
    не выцветает — смещается только его общий тон, на те же один-два пункта.
    """
    edge = _edge_colour(image)
    shifted = []
    for channel, actual, target in zip(image.convert('RGB').split(), edge, backdrop, strict=True):
        delta = target - actual
        shifted.append(channel.point([min(255, max(0, value + delta)) for value in range(256)]))
    return Image.merge('RGB', shifted)


def _edge_colour(image: Image.Image, band: int = 4) -> tuple[int, int, int]:
    """Средний цвет рамки кадра — он же цвет фона баннера в этом месте."""
    width, height = image.size
    strips = [
        image.crop((0, 0, width, band)),
        image.crop((0, height - band, width, height)),
        image.crop((0, 0, band, height)),
        image.crop((width - band, 0, width, height)),
    ]
    totals = [0.0, 0.0, 0.0]
    weight = 0
    for strip in strips:
        pixels = strip.width * strip.height
        for index, value in enumerate(strip.convert('RGB').resize((1, 1), Image.BOX).getpixel((0, 0))):
            totals[index] += value * pixels
        weight += pixels
    return tuple(round(total / weight) for total in totals)


def feathered(image: Image.Image, feather: int) -> Image.Image:
    """Прозрачность, линейно гаснущая к краям кадра."""
    width, height = image.size
    ramp = [min(255, round(255 * (offset + 0.5) / feather)) for offset in range(feather)]

    alpha = Image.new('L', (width, height), 255)
    pixels = alpha.load()
    for offset, value in enumerate(ramp):
        for x in range(width):
            pixels[x, offset] = min(pixels[x, offset], value)
            pixels[x, height - 1 - offset] = min(pixels[x, height - 1 - offset], value)
        for y in range(height):
            pixels[offset, y] = min(pixels[offset, y], value)
            pixels[width - 1 - offset, y] = min(pixels[width - 1 - offset, y], value)

    result = image.convert('RGBA')
    result.putalpha(alpha)
    return result


def extract(source: Path, destination: Path) -> tuple[int, int]:
    banner = Image.open(source).convert('RGB')
    width, height = banner.size

    art = banner.crop((0, 0, width, int(height * ART_BOTTOM)))
    left, top, right, bottom = content_box(art)
    left = max(0, left - PADDING)
    top = max(0, top - PADDING)
    right = min(art.width, right + PADDING)
    bottom = min(art.height, bottom + PADDING)

    cropped = art.crop((left, top, right, bottom))
    scale = TARGET_WIDTH / cropped.width
    resized = cropped.resize((TARGET_WIDTH, max(1, round(cropped.height * scale))), Image.LANCZOS)
    aligned = levelled(resized, _rgb(BACKDROP))

    feathered(aligned, FEATHER).save(destination, 'WEBP', quality=86, method=6)
    return resized.size


def _rgb(colour: str) -> tuple[int, int, int]:
    value = colour.lstrip('#')
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def main() -> int:
    if not BANNERS.is_dir():
        print(f'нет каталога баннеров: {BANNERS}', file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    for name, relative in sorted(SOURCES.items()):
        source = BANNERS / relative
        if not source.is_file():
            print(f'пропущен {name}: нет {source}', file=sys.stderr)
            continue
        destination = OUT / f'{name}.webp'
        size = extract(source, destination)
        print(f'{name:<12} {relative:<36} -> {destination.name} {size[0]}x{size[1]}')

    print(f'\nфон иллюстраций: {BACKDROP} (должен совпадать с --art-bg в portal.css)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
