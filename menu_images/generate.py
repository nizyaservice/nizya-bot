#!/usr/bin/env python3
"""Генератор SVG-баннеров разделов бота ApertuRU VPN.

Единый фирменный стиль: диафрагма (апертура), бирюзовый #3BC9DB на глубоком
чёрном, тонкие линии. Иконки — Tabler Icons (outline), лежат в svg/icons/.

Запуск:  python3 menu_images/generate.py
Результат: menu_images/svg/<section>.svg (исходники, правь и перегенерируй).
PNG рендерится отдельно: rsvg-convert -w 1280 svg/<section>.svg -o <section>.png
"""

from __future__ import annotations

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

# section_key -> (icon, title_ru, subtitle_en)
SECTIONS: dict[str, tuple[str, str, str]] = {
    'profile': ('user', 'ПРОФИЛЬ', 'PROFILE'),
    'balance': ('wallet', 'БАЛАНС', 'BALANCE • TOP-UP'),
    'topup_success': ('circle-check', 'ПОПОЛНЕНО', 'PAYMENT SUCCESS'),
    'my_subscriptions': ('devices-2', 'МОИ ПОДПИСКИ', 'MY SUBSCRIPTIONS'),
    'active_subscription': ('shield-check', 'АКТИВНАЯ ПОДПИСКА', 'ACTIVE SUBSCRIPTION'),
    'promocode': ('discount', 'ПРОМОКОД', 'PROMO CODE'),
    'support': ('headset', 'ПОДДЕРЖКА', 'SUPPORT'),
    'referrals': ('users-group', 'ПАРТНЁРСКАЯ ПРОГРАММА', 'REFERRAL PROGRAM'),
    'subscription_purchase': ('credit-card', 'ПОКУПКА ПОДПИСКИ', 'PURCHASE'),
    'subscription_upgrade': ('refresh', 'ПРОДЛЕНИЕ ПОДПИСКИ', 'UPGRADE • EXTEND'),
    'subscription_success': ('rosette', 'ПОДПИСКА АКТИВНА', 'SUBSCRIPTION SUCCESS'),
    'info': ('info-circle', 'ИНФОРМАЦИЯ', 'INFORMATION'),
    'faq': ('question', 'ЧАСТЫЕ ВОПРОСЫ', 'FAQ'),
    'rules': ('list-check', 'ПРАВИЛА СЕРВИСА', 'SERVICE RULES'),
    'privacy_policy': ('lock', 'КОНФИДЕНЦИАЛЬНОСТЬ', 'PRIVACY POLICY'),
    'public_offer': ('file-text', 'ПУБЛИЧНАЯ ОФЕРТА', 'PUBLIC OFFER'),
    'language': ('world', 'ВЫБОР ЯЗЫКА', 'LANGUAGE'),
    'server_status': ('server-2', 'СТАТУС СЕРВЕРОВ', 'SERVER STATUS'),
    'trial': ('gift', 'ПРОБНЫЙ ПЕРИОД', 'FREE TRIAL'),
}

W, H = 1500, 500


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


def background() -> str:
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
        f'{aperture_group(W - 200, H / 2, 560, CYAN, 2, 0.055)}'
        f'<rect x="14" y="14" width="{W - 28}" height="{H - 28}" rx="26" '
        f'fill="none" stroke="{CYAN}" stroke-opacity="0.22" stroke-width="2"/>'
    )


def section_svg(key: str, icon: str, title: str, subtitle: str) -> str:
    if len(title) <= 12:
        title_size = 76
    elif len(title) <= 16:
        title_size = 66
    else:
        title_size = 56
    if icon == 'question':
        glyph = (
            f'<text x="218" y="{H / 2 + 62:.0f}" font-family="{FONT}" font-weight="bold" '
            f'font-size="190" fill="{CYAN_LIGHT}" text-anchor="middle">?</text>'
        )
        icon_block = (
            f'<rect x="78" y="{H / 2 - 140}" width="280" height="280" rx="58" '
            f'fill="{CYAN}" fill-opacity="0.07" stroke="url(#accent)" stroke-width="3"/>'
            f'{glyph}'
        )
    else:
        icon_block = (
            f'<rect x="78" y="{H / 2 - 140}" width="280" height="280" rx="58" '
            f'fill="{CYAN}" fill-opacity="0.07" stroke="url(#accent)" stroke-width="3"/>'
            f'{icon_group(icon, 78 + 140, H / 2, 168, CYAN_LIGHT, 1.7)}'
        )
    title_y = H / 2 - 28
    sub_y = H / 2 + 44
    texts = (
        f'<text x="416" y="{title_y:.0f}" font-family="{FONT}" font-weight="bold" '
        f'font-size="{title_size}" fill="#F2F7FA" letter-spacing="2">{title}</text>'
        f'<rect x="418" y="{title_y + 24:.0f}" width="170" height="6" rx="3" fill="url(#accent)"/>'
        f'<text x="416" y="{sub_y:.0f}" font-family="{FONT}" font-weight="bold" '
        f'font-size="32" letter-spacing="3">'
        f'<tspan fill="{CYAN}">Apertu</tspan><tspan fill="#F2F7FA">RU</tspan>'
        f'<tspan fill="#F2F7FA" dx="14">VPN</tspan>'
        f'<tspan fill="{GREY}" dx="18">— {subtitle}</tspan>'
        f'</text>'
        f'<text x="{W - 104}" y="{H - 74}" font-family="{FONT}" font-size="24" '
        f'fill="{GREY}" text-anchor="end" letter-spacing="2">aperturu.top</text>'
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}">{defs()} {background()} {icon_block} {texts}</svg>\n'
    )


def main_menu_svg() -> str:
    cx = W / 2
    icon_size = 240
    icon_y = 160
    wordmark_y = 390
    tagline_y = 458
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">{defs()}
  {background()}
  {aperture_group(cx, icon_y, icon_size, CYAN_LIGHT, 1.6, 1.0)}
  {aperture_group(cx, icon_y, icon_size + 64, CYAN, 1, 0.25)}
  <text x="{cx}" y="{wordmark_y}" font-family="{FONT}" font-weight="bold" font-size="100"
        text-anchor="middle" letter-spacing="6"><tspan fill="{CYAN}">Apertu</tspan><tspan fill="#F2F7FA">RU</tspan><tspan fill="#F2F7FA" dx="20">VPN</tspan></text>
  <text x="{cx}" y="{tagline_y}" font-family="{FONT}" font-size="34" fill="{GREY}"
        text-anchor="middle" letter-spacing="8">БЕЗОПАСНЫЙ ИНТЕРНЕТ БЕЗ ГРАНИЦ</text>
</svg>
'''


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'main_menu.svg').write_text(main_menu_svg(), encoding='utf-8')
    for key, (icon, title, subtitle) in SECTIONS.items():
        (OUT / f'{key}.svg').write_text(section_svg(key, icon, title, subtitle), encoding='utf-8')
    print(f'generated {len(SECTIONS) + 1} svg -> {OUT}')


if __name__ == '__main__':
    main()
