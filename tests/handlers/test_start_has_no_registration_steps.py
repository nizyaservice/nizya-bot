"""После /start человек попадает прямо в главное меню.

Шагов регистрации больше нет: ни правил с политикой, ни оферты, ни вопроса о
реферальном коде, ни выбора языка. Это не выключенные настройками экраны, а
удалённый код — настройки `SKIP_RULES_ACCEPT`/`SKIP_REFERRAL_CODE` были в
прод-`.env` со значением false и перебили бы любое новое значение по умолчанию.

Тест держит именно это: обработчиков шагов нет, их колбэки из `start.py`
исчезли, а язык нового аккаунта берётся из конфига, а не спрашивается.
"""

from __future__ import annotations

import pathlib

import pytest

import app.states as states_module
from app.handlers import start


SOURCE = pathlib.Path(start.__file__).read_text(encoding='utf-8')

# Обработчики шагов и их помощники.
REMOVED_HANDLERS = (
    'process_rules_accept',
    'process_privacy_policy_accept',
    'process_language_selection',
    'process_referral_code_input',
    'process_referral_code_skip',
    'handle_potential_referral_code',
    '_prompt_language_selection',
    '_show_privacy_policy_after_rules',
    '_continue_registration_after_rules',
    '_continue_registration_after_language',
    'get_referral_code_keyboard',
)

# Колбэки кнопок, на которых держались шаги.
REMOVED_CALLBACKS = (
    'rules_accept',
    'rules_decline',
    'privacy_policy_accept',
    'privacy_policy_decline',
    'referral_skip',
    'language_select:',
)


@pytest.mark.parametrize('name', REMOVED_HANDLERS)
def test_step_handler_is_gone(name: str):
    assert not hasattr(start, name), f'{name} снова появился — шаг регистрации вернулся'


@pytest.mark.parametrize('callback_data', REMOVED_CALLBACKS)
def test_step_callback_is_not_referenced(callback_data: str):
    assert callback_data not in SOURCE, f'в start.py снова встречается колбэк {callback_data}'


def test_start_goes_straight_to_completion():
    """Единственный путь из /start — завершение регистрации."""
    start_of_handler = SOURCE.index('async def cmd_start(')
    body = SOURCE[start_of_handler:]

    assert 'await complete_registration(message, state, db)' in body


def test_registration_states_are_gone():
    """Состояний «в процессе регистрации» нет: процесс стал одним шагом.

    На них держались поблажки в мидлварах (auth, channel_checker) — незачем
    пропускать незарегистрированного куда-либо, кроме /start и проверки канала.
    """
    assert not hasattr(states_module, 'RegistrationStates')


def test_new_account_language_comes_from_config(monkeypatch):
    monkeypatch.setattr(start.settings, 'DEFAULT_LANGUAGE', 'ru')
    assert start.default_registration_language() == 'ru'

    # Региональный суффикс срезается: в локалях лежат короткие коды.
    monkeypatch.setattr(start.settings, 'DEFAULT_LANGUAGE', 'en-US')
    assert start.default_registration_language() == 'en'

    # Пустое значение не должно превращаться в язык '': берём язык загрузчика.
    monkeypatch.setattr(start.settings, 'DEFAULT_LANGUAGE', '')
    assert start.default_registration_language() == start.DEFAULT_LANGUAGE
