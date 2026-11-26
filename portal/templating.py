"""Шаблоны Jinja: один экземпляр на процесс и общий контекст страниц.

Общий контекст (бренд, адрес поддержки, признак «вошёл», текущий путь для
подсветки навигации) собирается здесь, чтобы каждая страница не перечисляла
его заново и не забывала половину.

Фильтры тут же: форматирование — дело показа, и считать рубли из копеек в
хендлерах значило бы повторять одно и то же в каждом разделе.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .sessions import Session


TEMPLATES_DIR = Path(__file__).resolve().parent / 'templates'

#: Коды статусов подписки (SubscriptionStatus в боте) по-русски: в API они
#: приходят как есть, а показывать человеку 'expired' незачем.
STATUS_LABELS = {
    'active': 'активна',
    'trial': 'пробная',
    'expired': 'истекла',
    'disabled': 'отключена',
    'limited': 'ограничена',
    'pending': 'оформляется',
}

#: Каким цветом значка показывать статус: зелёный — всё хорошо, красный —
#: человеку нужно что-то сделать прямо сейчас.
STATUS_TONES = {
    'active': 'ok',
    'trial': 'brand',
    'expired': 'danger',
    'disabled': 'danger',
    'limited': 'warn',
    'pending': 'warn',
}


def format_moment(value: Any) -> str:
    """Дата из API (ISO-строка) в привычном виде.

    Время не показываем: до минут тут никому нет дела, а часовой пояс ответа
    от пояса человека может отличаться.
    """
    if not isinstance(value, str) or not value:
        return '—'
    try:
        return datetime.fromisoformat(value).strftime('%d.%m.%Y')
    except ValueError:
        return value


def status_label(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return '—'
    return STATUS_LABELS.get(value, value)


def status_tone(value: Any) -> str:
    return STATUS_TONES.get(value if isinstance(value, str) else '', 'brand')


def rubles(value: Any) -> str:
    """Копейки в рубли с неразрывными пробелами в разрядах.

    Копейки показываем только когда они есть: «300 ₽» читается легче,
    чем «300,00 ₽», а на дробной сумме соврать нельзя.
    """
    try:
        kopeks = int(value or 0)
    except (TypeError, ValueError):
        return '—'

    sign = '−' if kopeks < 0 else ''
    whole, fraction = divmod(abs(kopeks), 100)
    grouped = f'{whole:,}'.replace(',', ' ')
    tail = f',{fraction:02d}' if fraction else ''
    return f'{sign}{grouped}{tail} ₽'


def spendable(source: Any) -> int:
    """Сколько человек может заплатить, в копейках.

    Берёт ``available_kopeks`` (деньги + бонусные баллы), а когда его в ответе
    нет — денежный кошелёк. Нужен фильтром, а не выражением в шаблоне: у
    словаря без ключа Jinja отдаёт Undefined, и проверка
    ``balance.available_kopeks is not none`` оказывается истинной — страница
    молча показывала ноль вместо суммы.
    """
    data = source if isinstance(source, dict) else {}
    amount = data.get('available_kopeks')
    if amount is None:
        amount = data.get('balance_kopeks')
    try:
        return int(amount or 0)
    except (TypeError, ValueError):
        return 0


def fill_class(value: Any) -> str:
    """Класс ширины полосы: доля в процентах, округлённая до 5.

    Полосу нельзя задать атрибутом style — его запрещает политика портала
    (`style-src 'self'`), поэтому ширины заведены классами в portal.css.
    """
    try:
        percent = float(value or 0)
    except (TypeError, ValueError):
        percent = 0.0
    percent = min(100.0, max(0.0, percent))
    return f'fill-{round(percent / 5) * 5}'


def period_label(days: Any) -> str:
    """Короткая подпись срока для переключателя: «30 дн» длиннее, чем «1 мес».

    Полную подпись («на 3 месяца») считает бот и присылает в ``label``; здесь
    нужен вариант в две-три буквы, который влезет в кнопку.
    """
    try:
        count = int(days or 0)
    except (TypeError, ValueError):
        return '—'

    if count >= 365 and count % 365 == 0:
        years = count // 365
        return f'{years} {plural(years, "год", "года", "лет")}'
    if count >= 30 and count % 30 == 0:
        return f'{count // 30} мес'
    return f'{count} {plural(count, "день", "дня", "дней")}'


def plural(count: Any, one: str, few: str, many: str) -> str:
    """Русское склонение после числа: 1 день, 2 дня, 5 дней."""
    try:
        number = abs(int(count))
    except (TypeError, ValueError):
        return many

    if number % 10 == 1 and number % 100 != 11:
        return one
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return few
    return many


def points(value: Any) -> str:
    """Количество бонусных баллов со словом: «49 баллов», «48,98 балла».

    Балл стоит половину рубля (курс задан в боте, ``app/utils/bonus_points.py``),
    поэтому количество и стоимость — разные числа: без слова рядом с суммой
    количество читалось бы как ещё одни рубли.
    """
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        return '—'

    if amount == int(amount):
        whole = int(amount)
        return f'{whole} {plural(whole, "балл", "балла", "баллов")}'

    return f'{amount:.2f}'.rstrip('0').rstrip('.').replace('.', ',') + ' балла'


_templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
_templates.env.filters['moment'] = format_moment
_templates.env.filters['status'] = status_label
_templates.env.filters['status_tone'] = status_tone
_templates.env.filters['rubles'] = rubles
_templates.env.filters['spendable'] = spendable
_templates.env.filters['fill'] = fill_class
_templates.env.filters['plural'] = plural
_templates.env.filters['points'] = points
_templates.env.filters['period'] = period_label


def render(
    request: Request,
    template: str,
    context: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
    session: Session | None = None,
) -> HTMLResponse:
    """Отрендерить страницу с общим контекстом.

    ``session`` передаётся там, где страница уже достала её из зависимости:
    по ней рисуется шапка и берётся CSRF-токен для форм. Без неё страница
    остаётся публичной — шапка покажет кнопку входа.
    """
    settings = get_settings()
    payload: dict[str, Any] = {
        'brand_name': settings.brand_name,
        'brand_tagline': settings.brand_tagline,
        'support_url': settings.support_url,
        'bot_url': settings.bot_url,
        'session': session,
        'user': session.user if session else None,
        'csrf_token': session.csrf_token if session else '',
        # Путь без параметров: по нему шаблон подсвечивает текущий раздел.
        'path': request.url.path,
    }
    payload.update(context or {})
    return _templates.TemplateResponse(request, template, payload, status_code=status_code)
