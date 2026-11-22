"""Решения наблюдателя: когда поднимать тревогу и чей это сбой.

Главный случай, ради которого всё затевалось (2026-11-20): бот молчит, а
`/health` отвечает мгновенно — процесс жив, но путь до Telegram оборван и
апдейты копятся на стороне Telegram. Простая проверка «отвечает ли /health»
такое пропускает, поэтому решение принимается по возрасту последнего успешного
контакта с Telegram.

Отдельно закреплено, что тревога называет виновника: Telegram виден
наблюдателю — значит дело в боте; не виден — значит внешний сбой.
"""

from __future__ import annotations

import pytest

from watchdog.checker import (
    FAILURES_BEFORE_ALERT,
    INTERVAL_SECONDS,
    SLOW_P95_MS,
    TELEGRAM_STALE_SECONDS,
    check,
    describe_alert,
    describe_recovery,
    describe_startup,
)


def _health(**availability) -> dict:
    """Ответ /health со здоровыми показателями, поверх которых кладутся правки."""
    telegram = {
        'seconds_since_ok': 5.0,
        'last_latency_ms': 120,
        'consecutive_failures': 0,
        'last_error': None,
        'rejected': False,
    }
    updates = {'window_seconds': 300, 'in_window': 20, 'p95_ms': 300, 'max_ms': 800, 'seconds_since_last': 4.0}
    telegram.update(availability.pop('telegram', {}))
    updates.update(availability.pop('updates', {}))
    assert not availability, f'неизвестные поля: {availability}'
    return {'status': 'ok', 'availability': {'telegram': telegram, 'updates': updates}}


# ─── всё в порядке ───


def test_healthy_bot_raises_nothing():
    verdict = check(_health(), telegram_reachable=True)

    assert verdict.healthy
    assert verdict.reason is None


def test_single_slow_update_is_not_an_alarm():
    """Одно медленное нажатие — не повод тревожить: p95 считаем по выборке."""
    verdict = check(_health(updates={'in_window': 1, 'p95_ms': SLOW_P95_MS * 10}), telegram_reachable=True)

    assert verdict.healthy


# ─── бот молчит ───


def test_dead_health_endpoint_is_an_incident():
    verdict = check(None, telegram_reachable=True)

    assert not verdict.healthy
    assert '/health' in verdict.reason
    assert verdict.telegram_reachable is True


def test_stalled_path_to_telegram_is_caught_despite_healthy_endpoint():
    """Тот самый случай: процесс отвечает, а до Telegram не доходит."""
    verdict = check(
        _health(telegram={'seconds_since_ok': TELEGRAM_STALE_SECONDS + 1, 'consecutive_failures': 7}),
        telegram_reachable=True,
    )

    assert not verdict.healthy
    assert 'Telegram' in verdict.reason


def test_contact_never_happened_counts_as_stalled():
    """После старта бот ещё не дотянулся до Telegram — это тоже сбой."""
    verdict = check(_health(telegram={'seconds_since_ok': None}), telegram_reachable=True)

    assert not verdict.healthy
    assert 'ни разу' in verdict.reason


def test_fresh_contact_just_under_the_threshold_is_fine():
    verdict = check(_health(telegram={'seconds_since_ok': TELEGRAM_STALE_SECONDS - 1}), telegram_reachable=True)

    assert verdict.healthy


# ─── бот отвечает медленно ───


def test_slow_processing_is_an_incident():
    verdict = check(_health(updates={'p95_ms': SLOW_P95_MS + 1, 'in_window': 50}), telegram_reachable=True)

    assert not verdict.healthy
    assert 'медленно' in verdict.reason


def test_telegram_rejection_is_reported_separately():
    """Неверный токен — не сетевая заминка: повторы его не лечат."""
    verdict = check(
        _health(telegram={'rejected': True, 'last_error': 'Unauthorized'}),
        telegram_reachable=True,
    )

    assert not verdict.healthy
    assert 'отклоняет' in verdict.reason
    assert 'Unauthorized' in verdict.reason


# ─── чей это сбой ───


@pytest.mark.parametrize(
    ('reachable', 'expected'),
    [
        (True, 'дело в боте'),
        (False, 'внешний сбой'),
    ],
)
def test_alert_names_the_culprit(reachable: bool, expected: str):
    verdict = check(None, telegram_reachable=reachable)

    assert expected in describe_alert(verdict, failures=3)


def test_unreachable_telegram_is_named_in_the_reason():
    verdict = check(None, telegram_reachable=False)

    assert 'Telegram недоступен' in verdict.reason
    assert verdict.telegram_reachable is False


# ─── восстановление ───


def test_recovery_reports_the_downtime():
    text = describe_recovery(downtime_seconds=185, reason='бот не отвечает на /health')

    assert '3 мин 5 с' in text
    assert 'бот не отвечает' in text


def test_short_downtime_is_reported_in_seconds():
    assert '40 с' in describe_recovery(downtime_seconds=40, reason=None)


# ─── сообщение о запуске ───


def test_startup_message_reports_parameters_and_bot_state():
    """Одно сообщение при запуске: за чем следим и что с ботом прямо сейчас."""
    text = describe_startup(check(_health(), telegram_reachable=True), timestamp='22.11.2026 14:03:00')

    assert 'Наблюдатель доступности' in text
    assert f'раз в {INTERVAL_SECONDS} с' in text
    assert f'дольше {TELEGRAM_STALE_SECONDS} с' in text
    assert f'{SLOW_P95_MS} мс' in text
    assert f'{FAILURES_BEFORE_ALERT} промахов' in text
    assert 'отвечает' in text
    assert '22.11.2026 14:03:00' in text


def test_startup_message_tells_when_the_bot_is_already_down():
    """Наблюдатель поднялся, а бот лежит — это нужно видеть сразу, а не через три минуты."""
    text = describe_startup(check(None, telegram_reachable=True), timestamp='22.11.2026 14:03:00')

    assert 'не отвечает' in text
    assert '/health' in text


def test_startup_is_announced_on_every_start():
    """Признак «уже сообщили» живёт в памяти процесса, а не в файле состояния:
    иначе перезапуск контейнера и деплой проходили бы молча."""
    import pathlib as _pathlib

    source = (_pathlib.Path(__file__).resolve().parents[2] / 'watchdog' / 'checker.py').read_text(encoding='utf-8')
    main_body = source[source.index('def main()') :]

    assert 'startup_announced = False' in main_body
    assert 'startup_announced' not in source[: source.index('def main()')], 'в состоянии на диске его быть не должно'
    assert "'startup_announced'" not in source, 'в файл состояния признак не пишется'
