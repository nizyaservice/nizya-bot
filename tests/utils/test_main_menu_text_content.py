"""Состав текста главного меню: тип подписки, тариф, отсчёт, баланс.

С главного экрана убраны дата окончания, расход трафика, лимит устройств и
ссылка «Подключить»: подключение живёт на кнопке «Подключить VPN», детали —
на экране «Моя подписка». Дата дублировала относительный отсчёт, который
нагляднее.

Текст главного меню строится двумя путями — rich-сообщением и классическим
фолбэком, — и оба должны показывать одно и то же.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.utils.rich_menu as rich_mod
from app.handlers.menu import _get_subscription_status, strip_end_date_line
from app.localization.texts import get_texts


def _subscription(status: str = 'trial', days: int = 3, **overrides):
    now = datetime.now(UTC)
    values = {
        'actual_status': status,
        'is_trial': status == 'trial',
        'start_date': now - timedelta(days=1),
        'end_date': now + timedelta(days=days),
        'traffic_used_gb': 1.5,
        'traffic_limit_gb': 10,
        'device_limit': 1,
        'tariff_id': 7,
        'subscription_url': 'https://sub.example/abc',
        'subscription_crypto_link': None,
        'id': 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _user(subscription=None):
    return SimpleNamespace(
        id=1,
        full_name='Тест',
        first_name='Тест',
        last_name=None,
        username=None,
        balance_kopeks=0,
        subscription=subscription,
        auth_type=None,
        is_trial_already_used=lambda: True,
    )


# ---- strip_end_date_line ----


@pytest.mark.parametrize(
    ('status_text', 'expected'),
    [
        ('🎁 Тестовая подписка\n📅 до 25.10.2026 (3 дн.)', '🎁 Тестовая подписка'),
        ('💎 Активна\n📅 до 01.01.2027 (70 дн.)', '💎 Активна'),
        ('🔴 Истекла\n📅 25.10.2026', '🔴 Истекла'),
        # Предупреждения — не дата, остаются на месте.
        ('💎 Активна\n⚠️ истекает завтра!', '💎 Активна\n⚠️ истекает завтра!'),
        ('💎 Активна', '💎 Активна'),
    ],
)
def test_strip_end_date_line(status_text, expected) -> None:
    assert strip_end_date_line(status_text) == expected


def test_strip_end_date_line_works_for_every_locale() -> None:
    """Маркер 📅 одинаков во всех локалях — иначе дата осталась бы на экране."""
    user = _user(_subscription(status='active', days=70, is_trial=False))

    for language in ('ru', 'en', 'ua', 'fa', 'zh'):
        texts = get_texts(language)
        stripped = strip_end_date_line(_get_subscription_status(user, texts))
        assert '📅' not in stripped, f'{language}: строка с датой осталась'
        assert stripped.strip(), f'{language}: статус опустел целиком'


async def _build_menu(monkeypatch, *, telegram_id: int = 1, tariff_name: str = 'Nizya Standard') -> str:
    """Полный rich-HTML главного меню с заглушками для внешних источников."""
    monkeypatch.setattr(
        rich_mod, 'get_tariff_by_id', AsyncMock(return_value=SimpleNamespace(name=tariff_name, is_daily=False))
    )
    monkeypatch.setattr(type(rich_mod.settings), 'is_tariffs_mode', lambda self: True)
    monkeypatch.setattr(type(rich_mod.settings), 'is_multi_tariff_enabled', lambda self: False)
    monkeypatch.setattr(rich_mod, 'get_random_active_message', AsyncMock(return_value=None))
    monkeypatch.setattr(rich_mod, 'build_promo_offer_hint', AsyncMock(return_value=None))
    monkeypatch.setattr(rich_mod, 'build_test_access_hint', AsyncMock(return_value=None))

    user = _user(_subscription())
    user.telegram_id = telegram_id
    return await rich_mod.build_main_menu_rich_html(user, get_texts('ru'), MagicMock())


# ---- rich-блок подписки ----


@pytest.fixture
def rich_block(monkeypatch):
    async def build(subscription, tariff_name='Nizya Standard'):
        monkeypatch.setattr(
            rich_mod,
            'get_tariff_by_id',
            AsyncMock(return_value=SimpleNamespace(name=tariff_name, is_daily=False)),
        )
        monkeypatch.setattr(type(rich_mod.settings), 'is_tariffs_mode', lambda self: True)
        return await rich_mod._build_single_subscription_block(_user(subscription), get_texts('ru'), MagicMock())

    return build


async def test_block_keeps_type_tariff_and_countdown(rich_block) -> None:
    block = await rich_block(_subscription())

    assert 'Тестовая подписка' in block
    assert 'Nizya Standard' in block
    assert 'истекает' in block


@pytest.mark.parametrize('removed', ['Трафик', 'Устройства', 'Подключить'])
async def test_block_drops_traffic_devices_and_connect(rich_block, removed) -> None:
    block = await rich_block(_subscription())

    assert removed not in block


async def test_block_has_no_subscription_link(rich_block) -> None:
    """Ссылка на подписку с главного экрана ушла вместе с кнопкой «Подключить»."""
    block = await rich_block(_subscription())

    assert 'sub.example' not in block


async def test_block_drops_the_end_date(rich_block) -> None:
    block = await rich_block(_subscription())

    assert '📅' not in block


# ---- telegram-id пользователя ----


def _id_line_position(html_out: str) -> tuple[int, int, int]:
    """Позиции блока подписки, строки с id и баланса — для проверки порядка."""
    return (html_out.index('</blockquote>'), html_out.index('Ваш id'), html_out.index('Баланс'))


async def test_rich_menu_shows_copyable_user_id(monkeypatch) -> None:
    """id выводится под блоком подписки и копируется нажатием.

    <code> — штатный способ Telegram «нажми, чтобы скопировать»; именно id
    первым делом спрашивает поддержка, поэтому он должен копироваться, а не
    выделяться вручную.
    """
    html_out = await _build_menu(monkeypatch, telegram_id=987654321)

    assert '<code>987654321</code>' in html_out
    subscription_end, user_id_at, balance_at = _id_line_position(html_out)
    assert subscription_end < user_id_at < balance_at, 'строка с id должна стоять между подпиской и балансом'


def test_user_id_emoji_comes_from_the_brand_pack() -> None:
    """🆔 есть в паке — мидлвара подменит его на фирменный."""
    import json
    from pathlib import Path

    pack = json.loads((Path(__file__).resolve().parents[2] / 'custom_emoji_ids.json').read_text(encoding='utf-8'))
    template = get_texts('ru').t('MAIN_MENU_USER_ID', '')

    assert '🆔' in template
    assert '🆔' in pack


def test_user_id_key_exists_in_every_locale() -> None:
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for directory in (root / 'app' / 'localization' / 'locales', root / 'locales'):
        for language in ('ru', 'en', 'ua', 'fa', 'zh'):
            value = json.loads((directory / f'{language}.json').read_text(encoding='utf-8'))['MAIN_MENU_USER_ID']
            assert '{user_id}' in value, f'{directory.name}/{language}: потерян плейсхолдер'
            assert '<code>' in value, f'{directory.name}/{language}: id перестал копироваться'
