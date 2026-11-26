"""На витрине стоит логотип Низи, а не чужая марка.

В шапке портала, во вкладке браузера и на плитке iOS раньше был знак апертуры
из набора Tabler (``menu_images/svg/icons/aperture.svg``): круг с хордами,
который легко принять за логотип чужого VPN. На странице, где человек платит
деньги, это читается как чужой бренд, поэтому иконки собираются из настоящего
логотипа — ``portal/tools/make_logo.py`` режет лицо Низи из ``vpn_logo.png``.

Сторож смотрит на файлы, а не на отрисованную страницу: подмена знака — это
всегда правка статики и шаблона, и ловить её надо там.
"""

from __future__ import annotations

import pathlib


REPO = pathlib.Path(__file__).resolve().parents[2]
PORTAL_IMG = REPO / 'portal' / 'static' / 'img'
BASE_TEMPLATE = REPO / 'portal' / 'templates' / 'base.html'

#: Кусок знака апертуры — хорда, которой нет в обычном круге. По ней чужую
#: марку видно в любом SVG, как бы ни назвали файл.
APERTURE_CHORD = '6.551 4.938'


def test_portal_icons_are_not_the_aperture_mark() -> None:
    for path in sorted(PORTAL_IMG.rglob('*.svg')):
        assert APERTURE_CHORD not in path.read_text(encoding='utf-8'), f'{path.name}: знак апертуры'


def test_header_and_tab_use_the_nizya_logo() -> None:
    markup = BASE_TEMPLATE.read_text(encoding='utf-8')

    assert '/static/img/logo.webp' in markup, 'метка в шапке'
    assert '/static/img/favicon.png' in markup, 'иконка вкладки'
    assert 'logo.svg' not in markup and 'favicon.svg' not in markup, 'вернулась старая марка'


def test_generated_icons_are_committed() -> None:
    """На проде нет ни Pillow, ни vpn_logo.png — иконки живут в репозитории."""
    for name in ('logo.webp', 'favicon.png', 'apple-touch-icon.png'):
        icon = PORTAL_IMG / name
        assert icon.is_file(), f'{name} не собран (python3 portal/tools/make_logo.py)'
        assert icon.stat().st_size > 0, f'{name} пустой'
