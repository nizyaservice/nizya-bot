#!/usr/bin/env python3
"""Генератор SVG/PNG-баннеров разделов бота ApertuRU VPN (16:9, 1280x720).

Единый фирменный стиль: диафрагма (апертура), бирюзовый #3BC9DB на глубоком
чёрном, тонкие линии. Иконки — Tabler Icons (outline), лежат в svg/icons/.

Иерархия «раздел → подпункты» (ключ БД ui_section_image.<key>):

  menu_images/main_menu.png            ключ 'main_menu' (главное меню, отдельный макет)
  menu_images/vpn_logo.png             общий логотип (тот же макет, рендер 1920)
  menu_images/<section>/_main.png      ключ '<section>' (главный баннер раздела)
  menu_images/<section>/<sub>.png      ключ '<section>.<sub>' (баннер подпункта)

SVG-исходники зеркальны: svg/main_menu.svg, svg/<section>/_main.svg,
svg/<section>/<sub>.svg. Папки subscription/ и referral/ существуют только
для подпунктов (ключи 'subscription.*', 'referral.*'); сами разделы
называются my_subscriptions и referrals.

Дизайн подпункта: над большим заголовком подпункта — маленькая «плашка»
с иконкой раздела и подписью «РАЗДЕЛ ›»; строка бренда без тире-суффикса.
Главный баннер раздела (_main) — прежний макет с подзаголовком.

Запуск:  python3 menu_images/generate.py
Скрипт сам рендерит PNG через rsvg-convert (-w 1280; vpn_logo — 1920).
"""

from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from html import escape
from pathlib import Path

BASE = Path(__file__).resolve().parent
ICONS = BASE / 'svg' / 'icons'
OUT = BASE / 'svg'

CYAN = '#3BC9DB'
CYAN_DARK = '#0E7490'
CYAN_LIGHT = '#7DE7F5'
BG_TOP = '#04070C'
BG_BOTTOM = '#0A1219'
GREY = '#8AA0AD'

FONT = 'DejaVu Sans, Arial, sans-serif'
FONT_BOLD_FILE = '/usr/share/fonts/TTF/DejaVuSans-Bold.ttf'

# section -> {'main': (icon, title_ru, subtitle_en), 'subs': {sub: (icon, title_ru)}}
# Папка без 'main' (subscription, referral) — только подпункты; сам раздел
# живёт под другим ключом (my_subscriptions / referrals).
SECTIONS: dict[str, dict] = {
    'profile': {'main': ('user', 'ПРОФИЛЬ', 'PROFILE')},
    'balance': {
        'main': ('wallet', 'БАЛАНС', 'BALANCE • TOP-UP'),
        'subs': {
            'history': ('history', 'ИСТОРИЯ ОПЕРАЦИЙ'),
            'cards': ('credit-card', 'СОХРАНЁННЫЕ КАРТЫ'),
            'topup': ('credit-card', 'ПОПОЛНЕНИЕ'),
        },
    },
    'topup_success': {'main': ('circle-check', 'ПОПОЛНЕНО', 'PAYMENT SUCCESS')},
    'my_subscriptions': {'main': ('devices-2', 'МОИ ПОДПИСКИ', 'MY SUBSCRIPTIONS')},
    'active_subscription': {'main': ('shield-check', 'АКТИВНАЯ ПОДПИСКА', 'ACTIVE SUBSCRIPTION')},
    'promocode': {'main': ('discount', 'ПРОМОКОД', 'PROMO CODE')},
    'support': {'main': ('headset', 'ПОДДЕРЖКА', 'SUPPORT')},
    'referrals': {'main': ('users-group', 'ПАРТНЁРСКАЯ ПРОГРАММА', 'REFERRAL PROGRAM')},
    'subscription_purchase': {'main': ('credit-card', 'ПОКУПКА ПОДПИСКИ', 'PURCHASE')},
    'subscription_upgrade': {'main': ('refresh', 'ПРОДЛЕНИЕ ПОДПИСКИ', 'UPGRADE • EXTEND')},
    'subscription_success': {'main': ('rosette', 'ПОДПИСКА АКТИВНА', 'SUBSCRIPTION SUCCESS')},
    'subscription': {
        'subs': {
            'devices': ('devices-2', 'УСТРОЙСТВА'),
            'traffic': ('chart-arrows', 'ДОП. ТРАФИК'),
            'autopay': ('calendar-repeat', 'АВТОПРОДЛЕНИЕ'),
            'extend': ('clock-plus', 'ПРОДЛЕНИЕ ПОДПИСКИ'),
            'revoke': ('refresh-off', 'ПЕРЕВЫПУСК'),
        },
    },
    'info': {'main': ('info-circle', 'ИНФОРМАЦИЯ', 'INFORMATION')},
    'faq': {'main': ('question', 'ЧАСТЫЕ ВОПРОСЫ', 'FAQ')},
    'rules': {'main': ('list-check', 'ПРАВИЛА СЕРВИСА', 'SERVICE RULES')},
    'privacy_policy': {'main': ('lock', 'КОНФИДЕНЦИАЛЬНОСТЬ', 'PRIVACY POLICY')},
    'public_offer': {'main': ('file-text', 'ПУБЛИЧНАЯ ОФЕРТА', 'PUBLIC OFFER')},
    'language': {'main': ('world', 'ВЫБОР ЯЗЫКА', 'LANGUAGE')},
    'server_status': {'main': ('server-2', 'СТАТУС СЕРВЕРОВ', 'SERVER STATUS')},
    'trial': {'main': ('gift', 'ПРОБНЫЙ ПЕРИОД', 'FREE TRIAL')},
    'referral': {
        'subs': {
            'list': ('list', 'СПИСОК РЕФЕРАЛОВ'),
            'analytics': ('chart-bar', 'АНАЛИТИКА'),
            'withdrawal': ('cash', 'ВЫВОД СРЕДСТВ'),
        },
    },
}

# Плашка подпункта: (иконка раздела, надпись «РАЗДЕЛ ›») для родительской папки.
SUBSECTION_META: dict[str, tuple[str, str]] = {
    'balance': ('wallet', 'БАЛАНС'),
    'subscription': ('devices-2', 'ПОДПИСКИ'),
    'referral': ('users-group', 'ПАРТНЁРКА'),
}

W, H = 1280, 720

# Отступ текстового блока от правой рамки (рамка: x=14..W-14).
TEXT_MAX_END = 1170


def icon_paths(name: str) -> list[str]:
    root = ET.parse(ICONS / f'{name}.svg').getroot()
    return [child.attrib['d'] for child in root if child.tag.endswith('}path') and 'd' in child.attrib]


def icon_group(name: str, cx: float, cy: float, size: float, stroke: str, width: float, opacity: float = 1.0) -> str:
    """Иконка Tabler (viewBox 24) вписана в квадрат size×size с центром cx,cy."""
    scale = size / 24.0
    x = cx - size / 2
    y = cy - size / 2
    paths = ''.join(f'<path d="{escape(d)}"/>' for d in icon_paths(name))
    return (
        f'<g transform="translate({x:.1f},{y:.1f}) scale({scale:.4f})" '
        f'fill="none" stroke="{stroke}" stroke-width="{width / scale:.3f}" '
        f'stroke-linecap="round" stroke-linejoin="round" opacity="{opacity}">{paths}</g>'
    )


def aperture_group(cx: float, cy: float, size: float, stroke: str, width: float, opacity: float) -> str:
    return icon_group('aperture', cx, cy, size, stroke, width, opacity)


def defs() -> str:
    return f'''
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{BG_TOP}"/>
      <stop offset="1" stop-color="{BG_BOTTOM}"/>
    </linearGradient>
    <linearGradient id="accent" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="{CYAN}"/>
      <stop offset="1" stop-color="{CYAN_LIGHT}"/>
    </linearGradient>
    <radialGradient id="glow" cx="0.22" cy="0.15" r="0.9">
      <stop offset="0" stop-color="{CYAN}" stop-opacity="0.14"/>
      <stop offset="0.5" stop-color="{CYAN}" stop-opacity="0.04"/>
      <stop offset="1" stop-color="{CYAN}" stop-opacity="0"/>
    </radialGradient>
  </defs>'''


def background(wm_cx: float, wm_cy: float, wm_size: float) -> str:
    """Фон: градиент, свечение, диагональная сетка, водяной знак-апертура, рамка.

    Водяной знак задаётся явно (в 16:9 правый край занят текстом, поэтому его
    уводят в свободный угол каждого макета).
    """
    lines = []
    for i in range(-H, W, 120):
        lines.append(f'<line x1="{i}" y1="{H}" x2="{i + H}" y2="0"/>')
    grid = (
        f'<g stroke="{CYAN}" stroke-opacity="0.05" stroke-width="1">'
        + ''.join(lines)
        + '</g>'
    )
    return (
        f'<rect width="{W}" height="{H}" fill="url(#bg)"/>'
        f'<rect width="{W}" height="{H}" fill="url(#glow)"/>'
        f'{grid}'
        f'{aperture_group(wm_cx, wm_cy, wm_size, CYAN, 2, 0.055)}'
        f'<rect x="14" y="14" width="{W - 28}" height="{H - 28}" rx="26" '
        f'fill="none" stroke="{CYAN}" stroke-opacity="0.22" stroke-width="2"/>'
    )


# --- подбор размера заголовка по реальной ширине глифов ----------------------

try:
    from PIL import ImageFont

    _REF_SIZE = 100
    _REF_FONT = ImageFont.truetype(FONT_BOLD_FILE, _REF_SIZE)

    def text_width(text: str, size: float, letter_spacing: float) -> float:
        """Ширина строки в px (тот же DejaVu Sans Bold, что рендерит rsvg)."""
        return _REF_FONT.getlength(text) * size / _REF_SIZE + letter_spacing * len(text)

except Exception:  # pragma: no cover - fallback без PIL

    def text_width(text: str, size: float, letter_spacing: float) -> float:
        return size * 0.72 * len(text) + letter_spacing * len(text)


TITLE_LADDER = (76, 68, 64, 60, 56, 54, 50, 48, 46, 44, 42, 40, 38, 36)
TITLE_LS = 1


def title_size_for(title: str, text_x: float) -> int:
    """Наибольший размер с лестницы, при котором заголовок не выходит за TEXT_MAX_END."""
    budget = TEXT_MAX_END - text_x
    for size in TITLE_LADDER:
        if text_width(title, size, TITLE_LS) <= budget:
            return size
    return TITLE_LADDER[-1]


def chip_block(icon: str, chip_x: float, chip_size: float) -> tuple[str, float]:
    """Квадратный чип с иконкой; возвращает (svg, центр по x)."""
    chip_y = H / 2 - chip_size / 2
    chip_cx = chip_x + chip_size / 2
    if icon == 'question':
        glyph = (
            f'<text x="{chip_cx:.0f}" y="{H / 2 + 68:.0f}" font-family="{FONT}" font-weight="bold" '
            f'font-size="200" fill="{CYAN_LIGHT}" text-anchor="middle">?</text>'
        )
        block = (
            f'<rect x="{chip_x}" y="{chip_y:.0f}" width="{chip_size}" height="{chip_size}" rx="60" '
            f'fill="{CYAN}" fill-opacity="0.07" stroke="url(#accent)" stroke-width="3"/>'
            f'{glyph}'
        )
    else:
        block = (
            f'<rect x="{chip_x}" y="{chip_y:.0f}" width="{chip_size}" height="{chip_size}" rx="60" '
            f'fill="{CYAN}" fill-opacity="0.07" stroke="url(#accent)" stroke-width="3"/>'
            f'{icon_group(icon, chip_cx, H / 2, 170, CYAN_LIGHT, 1.7)}'
        )
    return block, chip_cx


def brand_tspans(suffix: str | None = None) -> str:
    """Строка бренда: «ApertuRU VPN» (+ «— суффикс» для главных баннеров разделов)."""
    parts = (
        f'<tspan fill="{CYAN}">Apertu</tspan><tspan fill="#F2F7FA">RU</tspan>'
        f'<tspan fill="#F2F7FA" dx="12">VPN</tspan>'
    )
    if suffix:
        parts += f'<tspan fill="{GREY}" dx="16">— {escape(suffix)}</tspan>'
    return parts


def section_svg(icon: str, title: str, subtitle: str) -> str:
    """Главный баннер раздела: чип + большой заголовок + бренд-строка с суффиксом."""
    chip_x, chip_size = 72, 284
    text_x = 416
    title_size = title_size_for(title, text_x)
    title_y = H / 2 - 30
    bar_y = title_y + 32
    sub_y = title_y + 106
    icon_block, _chip_cx = chip_block(icon, chip_x, chip_size)
    texts = (
        f'<text x="{text_x}" y="{title_y:.0f}" font-family="{FONT}" font-weight="bold" '
        f'font-size="{title_size}" fill="#F2F7FA" letter-spacing="{TITLE_LS}">{escape(title)}</text>'
        f'<rect x="{text_x + 2}" y="{bar_y:.0f}" width="170" height="6" rx="3" fill="url(#accent)"/>'
        f'<text x="{text_x}" y="{sub_y:.0f}" font-family="{FONT}" font-weight="bold" '
        f'font-size="26" letter-spacing="2">{brand_tspans(subtitle)}</text>'
        f'<text x="{W - 104}" y="{H - 64}" font-family="{FONT}" font-size="24" '
        f'fill="{GREY}" text-anchor="end" letter-spacing="2">aperturu.top</text>'
    )
    # водяной знак — правый нижний угол, целиком внутри рамки, ниже строки бренда
    bg = background(W - 160, 570, 230)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}">{defs()} {bg} {icon_block} {texts}</svg>\n'
    )


# --- плашка подпункта ---------------------------------------------------------

PILL_Y = 228
PILL_H = 56
PILL_LS = 3
PILL_FONT = 27


def subsection_svg(icon: str, title: str, section_icon: str, section_label: str) -> str:
    """Баннер подпункта: чип с иконкой подпункта + плашка «РАЗДЕЛ ›» над большим
    заголовком. Иерархия читается визуально: подпункт — крупно, раздел — мелко."""
    chip_x, chip_size = 72, 284
    text_x = 416
    title_size = title_size_for(title, text_x)

    # плачка-«таблетка»: мини-иконка раздела + «РАЗДЕЛ ›»
    label = f'{section_label} ›'
    pill_cy = PILL_Y + PILL_H / 2
    icon_cx = text_x + 36
    text_start = text_x + 64
    pill_w = 64 + text_width(label, PILL_FONT, PILL_LS) + 26
    pill = (
        f'<rect x="{text_x}" y="{PILL_Y}" width="{pill_w:.0f}" height="{PILL_H}" rx="{PILL_H / 2}" '
        f'fill="{CYAN}" fill-opacity="0.06" stroke="{CYAN}" stroke-opacity="0.28" stroke-width="2"/>'
        f'{icon_group(section_icon, icon_cx, pill_cy, 30, GREY, 1.6)}'
        f'<text x="{text_start}" y="{pill_cy + 9.5:.1f}" font-family="{FONT}" font-weight="bold" '
        f'font-size="{PILL_FONT}" fill="{GREY}" letter-spacing="{PILL_LS}">{escape(label)}</text>'
    )

    title_y = 400
    bar_y = title_y + 32
    brand_y = title_y + 102
    icon_block, _chip_cx = chip_block(icon, chip_x, chip_size)
    texts = (
        f'{pill}'
        f'<text x="{text_x}" y="{title_y:.0f}" font-family="{FONT}" font-weight="bold" '
        f'font-size="{title_size}" fill="#F2F7FA" letter-spacing="{TITLE_LS}">{escape(title)}</text>'
        f'<rect x="{text_x + 2}" y="{bar_y:.0f}" width="170" height="6" rx="3" fill="url(#accent)"/>'
        f'<text x="{text_x}" y="{brand_y:.0f}" font-family="{FONT}" font-weight="bold" '
        f'font-size="26" letter-spacing="2">{brand_tspans()}</text>'
        f'<text x="{W - 104}" y="{H - 64}" font-family="{FONT}" font-size="24" '
        f'fill="{GREY}" text-anchor="end" letter-spacing="2">aperturu.top</text>'
    )
    bg = background(W - 160, 570, 230)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}">{defs()} {bg} {icon_block} {texts}</svg>\n'
    )


def main_menu_svg() -> str:
    cx = W / 2
    icon_y = 225
    icon_size = 310
    wordmark_y = 545
    tagline_y = 645
    # водяной знак — правый верхний угол, целиком внутри рамки, над словесным знаком
    bg = background(W - 175, 175, 280)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">{defs()}
  {bg}
  {aperture_group(cx, icon_y, icon_size, CYAN_LIGHT, 1.6, 1.0)}
  {aperture_group(cx, icon_y, icon_size + 64, CYAN, 1, 0.25)}
  <text x="{cx}" y="{wordmark_y}" font-family="{FONT}" font-weight="bold" font-size="108"
        text-anchor="middle" letter-spacing="6"><tspan fill="{CYAN}">Apertu</tspan><tspan fill="#F2F7FA">RU</tspan><tspan fill="#F2F7FA" dx="20">VPN</tspan></text>
  <text x="{cx}" y="{tagline_y}" font-family="{FONT}" font-size="34" fill="{GREY}"
        text-anchor="middle" letter-spacing="8">БЕЗОПАСНЫЙ ИНТЕРНЕТ БЕЗ ГРАНИЦ</text>
</svg>
'''


def render_png(svg_path: Path, png_path: Path, width: int = 1280) -> None:
    png_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ['rsvg-convert', '-w', str(width), str(svg_path), '-o', str(png_path)],
        check=True,
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    svg_files: list[tuple[Path, Path, int]] = []  # (svg, png, render_width)

    # Главное меню (плоский файл) + общий логотип (тот же макет, рендер 1920)
    main_svg = OUT / 'main_menu.svg'
    main_svg.write_text(main_menu_svg(), encoding='utf-8')
    svg_files.append((main_svg, BASE / 'main_menu.png', 1280))
    svg_files.append((main_svg, BASE / 'vpn_logo.png', 1920))

    for sec, spec in SECTIONS.items():
        if 'main' in spec:
            icon, title, subtitle = spec['main']
            svg_path = OUT / sec / '_main.svg'
            svg_path.parent.mkdir(parents=True, exist_ok=True)
            svg_path.write_text(section_svg(icon, title, subtitle), encoding='utf-8')
            svg_files.append((svg_path, BASE / sec / '_main.png', 1280))
        for sub, (icon, title) in spec.get('subs', {}).items():
            sec_icon, sec_label = SUBSECTION_META[sec]
            svg_path = OUT / sec / f'{sub}.svg'
            svg_path.parent.mkdir(parents=True, exist_ok=True)
            svg_path.write_text(subsection_svg(icon, title, sec_icon, sec_label), encoding='utf-8')
            svg_files.append((svg_path, BASE / sec / f'{sub}.png', 1280))

    for svg_path, png_path, width in svg_files:
        render_png(svg_path, png_path, width)

    n_svg = 1 + sum(
        (1 if 'main' in spec else 0) + len(spec.get('subs', {})) for spec in SECTIONS.values()
    )  # + main_menu
    print(f'generated {n_svg} svg -> {OUT}; rendered {len(svg_files)} png (incl. vpn_logo 1920)')


if __name__ == '__main__':
    main()
