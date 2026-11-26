#!/usr/bin/env python3
"""Иконки портала из логотипа Низи (``vpn_logo.png`` в корне репозитория).

До этого в шапке, во вкладке браузера и на иконке «на экран домой» стоял знак
апертуры из набора Tabler (``menu_images/svg/icons/aperture.svg``) — его легко
принять за марку чужого VPN, и на витрине, которую видят покупатели, ему не
место. Берём настоящий логотип бренда: лицо Низи с баннера бота.

Источник — растровый ``vpn_logo.png`` (он же ``settings.LOGO_FILE``), поэтому
и иконки растровые: векторной версии рисунка в репозитории нет, а обводка
вроде прежнего SVG была бы уже другим знаком, а не логотипом.

Запуск: ``python3 portal/tools/make_logo.py`` (нужен Pillow). Результат
коммитится — на проде ни Pillow, ни ``vpn_logo.png`` нет:

* ``logo.webp`` — круглая метка в шапке (26 CSS-пикселей, запас на retina);
* ``favicon.png`` — вкладка браузера;
* ``apple-touch-icon.png`` — плитка iOS, непрозрачная: систему устраивает
  только сплошной квадрат, скругление она делает сама.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw


REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / 'vpn_logo.png'
OUT = REPO / 'portal' / 'static' / 'img'

#: Лицо Низи на логотипе (1280×720): левый, верхний, правый, нижний край.
#: Кадр тесный нарочно — в шапке знак виден на 26 пикселях, и фигура целиком
#: превращается там в пятно, а чёлка и лаймовые очки узнаются.
FACE = (565, 95, 750, 280)

#: Цвет подложки квадратных иконок. Тот же, что ``--bg`` в portal.css и
#: ``BACKDROP`` в extract_mascots.py: разойдутся — плитка iOS станет заметно
#: светлее или темнее страницы.
BACKDROP = '#0b0f1b'

#: Сторона круглой метки в шапке: 26 CSS-пикселей, четырёхкратный запас.
MARK_SIZE = 104

#: Кольцо вокруг метки, цвет и толщина в CSS-пикселях. Без него тёмный капюшон
#: Низи сливается с тёмной шапкой, и знак читается как пятно.
RING = '#7B4FC9'
RING_WIDTH = 2

#: Иконка вкладки. Больше 64 нет смысла: браузер всё равно ужимает.
FAVICON_SIZE = 64

#: Плитка iOS. Поле вокруг рисунка — доля стороны: система скругляет углы,
#: и без поля лицо режется.
TOUCH_SIZE = 180
TOUCH_PADDING = 0.08


def _rgb(colour: str) -> tuple[int, int, int]:
    value = colour.lstrip('#')
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _face(source: Image.Image, size: int) -> Image.Image:
    """Квадратный кадр с лицом, уменьшенный до ``size``."""
    return source.crop(FACE).resize((size, size), Image.LANCZOS)


def round_mark(source: Image.Image, size: int) -> Image.Image:
    """Круглая метка в фирменном кольце, углы прозрачные — ложится на любой фон.

    Круг и кольцо рисуем с запасом и только потом уменьшаем: и маска, и обводка,
    нарисованные сразу в нужном размере, дают рваный край.
    """
    scale = 4
    side = size * scale
    art = _face(source, side).convert('RGBA')
    mask = Image.new('L', (side, side), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, side - 1, side - 1), fill=255)
    art.putalpha(mask)

    stroke = RING_WIDTH * scale * size // MARK_SIZE or 1
    ImageDraw.Draw(art).ellipse(
        (stroke // 2, stroke // 2, side - 1 - stroke // 2, side - 1 - stroke // 2),
        outline=RING,
        width=stroke,
    )
    return art.resize((size, size), Image.LANCZOS)


def square_icon(source: Image.Image, size: int, padding: float = 0.0) -> Image.Image:
    """Непрозрачный квадрат на фирменном фоне."""
    inset = round(size * padding)
    tile = Image.new('RGB', (size, size), _rgb(BACKDROP))
    tile.paste(_face(source, size - 2 * inset), (inset, inset))
    return tile


def main() -> int:
    if not SOURCE.is_file():
        print(f'нет логотипа: {SOURCE}', file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    logo = Image.open(SOURCE).convert('RGB')

    mark = round_mark(logo, MARK_SIZE)
    mark.save(OUT / 'logo.webp', 'WEBP', quality=92, method=6)

    square_icon(logo, FAVICON_SIZE).save(OUT / 'favicon.png', 'PNG', optimize=True)
    square_icon(logo, TOUCH_SIZE, TOUCH_PADDING).save(OUT / 'apple-touch-icon.png', 'PNG', optimize=True)

    print(f'logo.webp            {MARK_SIZE}x{MARK_SIZE} (круглая метка в шапке)')
    print(f'favicon.png          {FAVICON_SIZE}x{FAVICON_SIZE}')
    print(f'apple-touch-icon.png {TOUCH_SIZE}x{TOUCH_SIZE}')
    print(f'\nфон квадратных иконок: {BACKDROP} (должен совпадать с --bg в portal.css)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
