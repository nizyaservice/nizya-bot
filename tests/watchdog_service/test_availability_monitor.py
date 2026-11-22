"""Показатели доступности внутри бота: канарейка и время обработки.

Их читает внешний наблюдатель, поэтому важно не «что-то отдаётся», а что
именно: возраст последнего успешного контакта с Telegram (по нему видно
оборванный polling, который `/health` сам по себе не показывает) и p95 времени
обработки апдейта (по нему видно «отвечает, но медленно»).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramNetworkError

from app.services.availability_monitor import AvailabilityMonitor


def test_fresh_monitor_reports_no_contact_yet():
    """До первого успеха наблюдатель обязан видеть «контакта не было»."""
    snapshot = AvailabilityMonitor().snapshot()

    assert snapshot['telegram']['seconds_since_ok'] is None
    assert snapshot['telegram']['consecutive_failures'] == 0
    assert snapshot['updates']['p95_ms'] is None


def test_successful_contact_resets_the_failure_streak():
    monitor = AvailabilityMonitor()
    monitor.mark_telegram_failure('timeout')
    monitor.mark_telegram_failure('timeout')

    assert monitor.snapshot()['telegram']['consecutive_failures'] == 2

    monitor.mark_telegram_ok(latency_ms=150)
    snapshot = monitor.snapshot()

    assert snapshot['telegram']['consecutive_failures'] == 0
    assert snapshot['telegram']['seconds_since_ok'] is not None
    assert snapshot['telegram']['last_latency_ms'] == 150
    assert snapshot['telegram']['last_error'] is None


def test_rejection_is_marked_apart_from_a_network_blip():
    """Неверный токен лечится не повторами, а руками — наблюдатель скажет иначе."""
    monitor = AvailabilityMonitor()

    monitor.mark_telegram_failure('Unauthorized', rejected=True)

    assert monitor.snapshot()['telegram']['rejected'] is True


def test_long_error_text_is_trimmed():
    """Текст ошибки уходит в JSON и в тревогу — простыня там не нужна."""
    monitor = AvailabilityMonitor()

    monitor.mark_telegram_failure('x' * 1000)

    assert len(monitor.snapshot()['telegram']['last_error']) <= 200


def test_p95_grows_with_slow_updates():
    monitor = AvailabilityMonitor()
    for _ in range(19):
        monitor.record_update(0.05)
    monitor.record_update(4.0)

    snapshot = monitor.snapshot()

    assert snapshot['updates']['in_window'] == 20
    assert snapshot['updates']['processed_total'] == 20
    assert snapshot['updates']['p95_ms'] >= 4000, 'выброс обязан попасть в p95'
    assert snapshot['updates']['max_ms'] == 4000


def test_old_measurements_leave_the_window(monkeypatch):
    """Окно живёт минутами: вчерашние тормоза не должны держать тревогу."""
    import app.services.availability_monitor as module

    clock = {'now': 1000.0}
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock['now'])

    monitor = AvailabilityMonitor()
    monitor.record_update(9.0)

    clock['now'] += module.TIMING_WINDOW_SECONDS + 1
    snapshot = monitor.snapshot()

    assert snapshot['updates']['in_window'] == 0
    assert snapshot['updates']['p95_ms'] is None
    assert snapshot['updates']['processed_total'] == 1, 'общий счётчик не обнуляется'


# ─── канарейка ───


@pytest.mark.asyncio
async def test_canary_records_network_failure(monkeypatch):
    import app.services.availability_monitor as module

    monkeypatch.setattr(module.asyncio, 'sleep', AsyncMock(side_effect=StopAsyncIteration))
    monitor = AvailabilityMonitor()
    bot = type('Bot', (), {'get_me': AsyncMock(side_effect=TelegramNetworkError(method=None, message='timeout'))})()

    with pytest.raises(StopAsyncIteration):
        await monitor.run_canary(bot)

    snapshot = monitor.snapshot()
    assert snapshot['telegram']['consecutive_failures'] == 1
    assert snapshot['telegram']['rejected'] is False
    assert snapshot['telegram']['seconds_since_ok'] is None


@pytest.mark.asyncio
async def test_canary_records_success_with_latency(monkeypatch):
    import app.services.availability_monitor as module

    monkeypatch.setattr(module.asyncio, 'sleep', AsyncMock(side_effect=StopAsyncIteration))
    monitor = AvailabilityMonitor()
    bot = type('Bot', (), {'get_me': AsyncMock(return_value=object())})()

    with pytest.raises(StopAsyncIteration):
        await monitor.run_canary(bot)

    snapshot = monitor.snapshot()
    assert snapshot['telegram']['seconds_since_ok'] is not None
    assert snapshot['telegram']['last_latency_ms'] is not None


@pytest.mark.asyncio
async def test_canary_marks_rejection_without_retrying_it(monkeypatch):
    import app.services.availability_monitor as module

    monkeypatch.setattr(module.asyncio, 'sleep', AsyncMock(side_effect=StopAsyncIteration))
    monitor = AvailabilityMonitor()
    bot = type('Bot', (), {'get_me': AsyncMock(side_effect=RuntimeError('Unauthorized'))})()

    with pytest.raises(StopAsyncIteration):
        await monitor.run_canary(bot)

    assert monitor.snapshot()['telegram']['rejected'] is True


# ─── связка с ботом ───


def test_metrics_are_wired_into_the_bot():
    """Без мидлвары и канарейки показатели всегда были бы пустыми."""
    import pathlib

    repo = pathlib.Path(__file__).resolve().parents[2]
    bot_source = (repo / 'app' / 'bot.py').read_text(encoding='utf-8')
    main_source = (repo / 'main.py').read_text(encoding='utf-8')
    health_source = (repo / 'app' / 'webserver' / 'unified_app.py').read_text(encoding='utf-8')

    assert 'dp.update.outer_middleware(UpdateTimingMiddleware())' in bot_source
    assert 'availability_monitor.run_canary(bot)' in main_source
    assert "'availability': availability_monitor.snapshot()" in health_source
