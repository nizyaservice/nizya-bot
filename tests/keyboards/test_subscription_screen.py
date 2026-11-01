"""Экран «Моя подписка»: состав текста и клавиатуры.

Экран «Настройки» свёрнут — его содержимое поднято на уровень выше: на месте
кнопки настроек стоит «Управление устройствами», перевыпуск идёт отдельной
строкой во всю ширину. Докупка слотов устройств убрана вместе с экраном.

Из текста убраны блок-цитата с описанием тарифа, строки «Серверы» и
«Устройства» и список подключённых устройств: расход и устройства
пользователь смотрит в приложении, а управлять ими идёт по кнопке.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.keyboards.inline as kb


LOCALE_DIRS = (
    Path(__file__).resolve().parents[2] / 'app' / 'localization' / 'locales',
    Path(__file__).resolve().parents[2] / 'locales',
)
LANGS = ('ru', 'en', 'ua', 'fa', 'zh')
TEMPLATE_KEYS = ('SUBSCRIPTION_OVERVIEW_TEMPLATE', 'SUBSCRIPTION_DAILY_OVERVIEW_TEMPLATE')

# Подсказка вместо цифр расхода — своя на каждом языке, но «Happ» в ней везде.
TRAFFIC_HINT = 'Happ/Incy'


def _callbacks(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


def _rows(markup) -> list[list[str]]:
    return [[b.callback_data or 'url' for b in row] for row in markup.inline_keyboard]


def _subscription(**overrides):
    tariff = SimpleNamespace(is_daily=False, is_free=False, device_price_kopeks=0, can_topup_traffic=lambda: True)
    values = {
        'id': 1,
        'tariff': tariff,
        'tariff_id': 7,
        'actual_status': 'active',
        'status': 'active',
        'traffic_limit_gb': 10,
        'subscription_url': 'https://sub.example/x',
        'subscription_crypto_link': None,
        'is_trial': False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _paid_keyboard(**overrides):
    return kb.get_subscription_keyboard(
        language='ru', has_subscription=True, is_trial=False, subscription=_subscription(**overrides)
    )


# ---- клавиатура платной подписки ----


def test_manage_devices_replaces_the_settings_button() -> None:
    callbacks = _callbacks(_paid_keyboard())

    assert 'subscription_manage_devices' in callbacks
    assert 'subscription_settings' not in callbacks


def test_rows_follow_the_agreed_order() -> None:
    """Ряд продления/смены тарифа переехал в главное меню — первым идут устройства.

    В одиночном тарифном режиме [Продлить тариф] [Сменить тариф] показывает
    главное меню (см. tests/keyboards/test_main_menu_layout.py), и дублировать
    их здесь не нужно.
    """
    rows = _rows(_paid_keyboard())

    assert rows[1] == ['subscription_manage_devices', 'subscription_autopay']
    assert 'subscription_extend' not in _callbacks(_paid_keyboard())
    assert 'instant_switch' not in _callbacks(_paid_keyboard())


def test_multi_tariff_keeps_renew_and_switch_on_the_screen(monkeypatch) -> None:
    """В мультитарифе ряд остаётся здесь: кнопка должна знать, какую подписку продлевать."""
    from app.config import Settings

    monkeypatch.setattr(Settings, 'is_multi_tariff_enabled', lambda self: True)
    rows = _rows(_paid_keyboard())

    assert rows[1] == ['subscription_extend', 'instant_switch']


def test_autopay_stays_away_from_daily_and_legacy_tariffs() -> None:
    """У суточного тарифа автоплатежа нет: он списывается сам.

    Автоплатёж переехал в ряд с устройствами, и этот ряд строится вне ветки
    обычного тарифа — легко было раздать кнопку тем, у кого её не должно быть.
    """
    daily = SimpleNamespace(is_daily=True, is_free=False, device_price_kopeks=0, can_topup_traffic=lambda: True)
    keyboard = kb.get_subscription_keyboard(
        language='ru',
        has_subscription=True,
        is_trial=False,
        subscription=_subscription(tariff=daily, is_daily_paused=False),
    )

    assert 'subscription_autopay' not in _callbacks(keyboard)


def test_revoke_takes_a_full_row_below(monkeypatch) -> None:
    monkeypatch.setattr(type(kb.settings), 'is_subscription_revoke_enabled', lambda self: True)
    rows = _rows(_paid_keyboard())

    devices_at = next(i for i, r in enumerate(rows) if 'subscription_manage_devices' in r)

    assert rows[devices_at + 1] == ['subscription_revoke']


def test_revoke_row_respects_its_setting(monkeypatch) -> None:
    monkeypatch.setattr(type(kb.settings), 'is_subscription_revoke_enabled', lambda self: False)

    assert 'subscription_revoke' not in _callbacks(_paid_keyboard())


def test_device_slot_purchase_is_gone() -> None:
    """Докупка слотов жила только на экране настроек и ушла вместе с ним."""
    assert 'subscription_change_devices' not in _callbacks(_paid_keyboard())


def test_trial_still_has_no_device_management() -> None:
    """Для пробника управление устройствами закрыто — решение не менялось.

    Хендлер отказывает триалу отдельно (devices.py, PAID_FEATURE_ONLY),
    так что кнопка здесь была бы тупиком.
    """
    trial = kb.get_subscription_keyboard(
        language='ru', has_subscription=True, is_trial=True, subscription=_subscription(is_trial=True)
    )
    callbacks = _callbacks(trial)

    assert 'subscription_manage_devices' not in callbacks
    assert 'subscription_settings' not in callbacks


# ---- текст экрана ----


@pytest.mark.parametrize('directory', LOCALE_DIRS, ids=('base', 'override'))
@pytest.mark.parametrize('lang', LANGS)
@pytest.mark.parametrize('key', TEMPLATE_KEYS)
def test_overview_template_drops_servers_and_devices(directory, lang, key) -> None:
    template = json.loads((directory / f'{lang}.json').read_text(encoding='utf-8'))[key]

    assert '{servers}' not in template
    assert '{devices_used}' not in template
    assert '{device_limit}' not in template


@pytest.mark.parametrize('directory', LOCALE_DIRS, ids=('base', 'override'))
@pytest.mark.parametrize('lang', LANGS)
@pytest.mark.parametrize('key', TEMPLATE_KEYS)
def test_traffic_line_points_to_the_app(directory, lang, key) -> None:
    template = json.loads((directory / f'{lang}.json').read_text(encoding='utf-8'))[key]

    assert '{traffic}' not in template, 'цифры расхода должны были уступить место подсказке'
    assert TRAFFIC_HINT in template


@pytest.mark.parametrize('directory', LOCALE_DIRS, ids=('base', 'override'))
@pytest.mark.parametrize('lang', LANGS)
@pytest.mark.parametrize('key', TEMPLATE_KEYS)
def test_overview_template_drops_the_type_field(directory, lang, key) -> None:
    """Поле «Тип» (триал/платная) с экрана убрано — статус виден строкой выше."""
    template = json.loads((directory / f'{lang}.json').read_text(encoding='utf-8'))[key]

    assert '{subscription_type}' not in template


# ---- выделение заголовков цитатой ----


def _locale(directory, lang) -> dict:
    return json.loads((directory / f'{lang}.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('directory', LOCALE_DIRS, ids=('base', 'override'))
@pytest.mark.parametrize('lang', LANGS)
@pytest.mark.parametrize('key', TEMPLATE_KEYS)
def test_overview_headers_are_quoted(directory, lang, key) -> None:
    """Ник и заголовок «Информация о подписке» выделены цитатой — только они.

    Остальные строки остаются обычным текстом: цитата здесь разделитель
    разделов, а не оформление всего подряд.
    """
    lines = _locale(directory, lang)[key].split('\n')

    assert lines[0].startswith('<blockquote>') and lines[0].endswith('</blockquote>')
    header = lines.index('') + 1
    assert lines[header].startswith('<blockquote>') and lines[header].endswith('</blockquote>')
    quoted = [line for line in lines if '<blockquote' in line]
    assert len(quoted) == 2, f'цитатой выделено лишнее: {quoted}'


@pytest.mark.parametrize('directory', LOCALE_DIRS, ids=('base', 'override'))
@pytest.mark.parametrize('lang', LANGS)
def test_connect_link_label_is_quoted_and_not_bold(directory, lang) -> None:
    value = _locale(directory, lang)['SUBSCRIPTION_CONNECT_LINK_SECTION']
    label = value.split('\n')[0]

    assert label.startswith('<blockquote>') and label.endswith('</blockquote>')
    assert '<b>' not in value, 'жирный с подписи ссылки убран'
    assert '{subscription_url}' in value


def test_assembled_subscription_message_is_valid_html() -> None:
    """Цитаты не вкладываются друг в друга — Telegram такого не принимает.

    Ссылка в happ-режиме сама приходит блоком-цитатой, а блок тарифа
    подставляется в строку статуса, поэтому баланс тегов стоит проверять
    на собранном сообщении, а не на шаблоне.
    """
    from app.localization.texts import get_texts
    from app.utils.validators import validate_html_tags

    texts = get_texts('ru')
    message = texts.SUBSCRIPTION_OVERVIEW_TEMPLATE.format(
        full_name='Тест',
        balance='0 ₽',
        status_emoji='🟢',
        status_display='Активна',
        warning='',
        tariff_info_block='',
        end_date='01.11.2026',
        time_left='9 дней',
        traffic='',
        servers='',
        devices_used='',
        device_limit='',
        subscription_type='',
    )
    message += '\n\n' + texts.t('SUBSCRIPTION_CONNECT_LINK_SECTION').format(
        subscription_url='<blockquote expandable><code>https://s/x</code></blockquote>'
    )

    is_valid, error = validate_html_tags(message)

    assert is_valid, error
    assert message.count('<blockquote') == message.count('</blockquote>')


# ---- возврат с дочерних экранов ----


def test_revoke_screen_returns_to_my_subscription() -> None:
    """«Назад» с подтверждения перевыпуска ведёт в «Мою подписку».

    Раньше вело на экран «Настройки подписки». Его пункты подняты в «Мою
    подписку», входа на него больше нет — возврат туда высаживал человека на
    страницу, куда он не заходил.
    """
    from app.handlers.subscription.revoke import _build_revoke_confirm_keyboard

    single = _callbacks(_build_revoke_confirm_keyboard('ru', multi_tariff=False))
    multi = _callbacks(_build_revoke_confirm_keyboard('ru', multi_tariff=True))

    assert 'menu_subscription' in single
    assert 'subscription_settings' not in single
    # В мультитарифе возврат к списку подписок — поведение не менялось.
    assert 'my_subscriptions' in multi


def test_no_screen_sends_the_user_back_to_the_folded_settings() -> None:
    """Ни одна кнопка не должна вести на свёрнутый экран настроек.

    Обработчик subscription_settings намеренно остаётся зарегистрированным
    (вдруг понадобится вернуть вход), поэтому ищем именно переходы: значения
    callback_data и значения по умолчанию для back_callback.
    """
    root = Path(__file__).resolve().parents[2] / 'app'
    offenders: list[str] = []
    for path in sorted(root.rglob('*.py')):
        for number, line in enumerate(path.read_text(encoding='utf-8').split('\n'), 1):
            if "'subscription_settings'" not in line:
                continue
            if 'callback_data=' in line or 'back_callback' in line:
                offenders.append(f'{path.relative_to(root.parent)}:{number}')

    assert not offenders, f'ведут на экран без входа: {offenders}'
