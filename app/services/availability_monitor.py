"""Показатели доступности бота для внешнего наблюдателя.

Зачем это нужно отдельно от обычного healthcheck: в эпизоде 2026-11-20 бот
молчал, а `/health` отвечал за 24 мс — процесс был жив, но путь до Telegram
оборвался, апдейты копились на стороне Telegram и пришли пачкой после
восстановления. Такой сбой видно только по **возрасту последнего успешного
контакта с Telegram**, поэтому здесь считаются две вещи:

* **канарейка** — раз в ``CANARY_INTERVAL_SECONDS`` дёргает Telegram той же
  сессией бота (тот же маршрут, прокси и пул соединений, иначе проверка
  зелёная, когда боту плохо) и запоминает время последнего успеха;
* **время обработки апдейтов** — скользящее окно последних замеров, из него
  считается p95: «бот отвечает, но медленно» иначе не отличить от нормы.

Состояние держим в памяти процесса: наблюдатель читает его из ``/health``, а
переживать перезапуск бота ему незачем — перезапуск сам по себе и есть событие.

Отдельно: ``seconds_since_last_update`` отдаётся как справка, а не как повод
для тревоги. Ночью апдейтов нет, и алерт по нему был бы ложным.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

import structlog
from aiogram.exceptions import TelegramNetworkError


logger = structlog.get_logger(__name__)

#: Как часто канарейка проверяет путь до Telegram.
CANARY_INTERVAL_SECONDS = 30

#: Размер окна замеров обработки апдейтов. 500 хватает на всплеск трафика, а
#: память держит на уровне пары килобайт.
TIMING_WINDOW_SIZE = 500

#: Замеры старше этого срока в p95 не участвуют: нас интересует «сейчас», а не
#: средняя температура за сутки.
TIMING_WINDOW_SECONDS = 300


class AvailabilityMonitor:
    """Счётчики доступности: контакт с Telegram и время обработки апдейтов."""

    def __init__(self) -> None:
        self._started_at = time.monotonic()
        self._telegram_ok_at: float | None = None
        self._telegram_latency_ms: int | None = None
        self._telegram_failures = 0
        self._telegram_error: str | None = None
        #: Отказ по существу (например, неверный токен) — не сетевая заминка,
        #: повторы его не лечат, и наблюдатель должен сказать это другими словами.
        self._telegram_rejected = False
        self._updates: deque[tuple[float, float]] = deque(maxlen=TIMING_WINDOW_SIZE)
        self._updates_total = 0
        self._last_update_at: float | None = None

    # ─── запись ───

    def mark_telegram_ok(self, latency_ms: int) -> None:
        self._telegram_ok_at = time.monotonic()
        self._telegram_latency_ms = latency_ms
        self._telegram_failures = 0
        self._telegram_error = None
        self._telegram_rejected = False

    def mark_telegram_failure(self, error: str, *, rejected: bool = False) -> None:
        self._telegram_failures += 1
        self._telegram_error = error[:200]
        self._telegram_rejected = rejected

    def record_update(self, duration_seconds: float) -> None:
        now = time.monotonic()
        self._updates.append((now, duration_seconds))
        self._updates_total += 1
        self._last_update_at = now

    # ─── чтение ───

    def snapshot(self) -> dict[str, Any]:
        """Срез для ``/health``. Все сроки — в секундах, время обработки — в мс."""
        now = time.monotonic()
        durations = sorted(duration for at, duration in self._updates if now - at <= TIMING_WINDOW_SECONDS)

        return {
            'telegram': {
                # None означает «успешного контакта ещё не было» — наблюдатель
                # трактует это так же, как просроченный контакт.
                'seconds_since_ok': None if self._telegram_ok_at is None else round(now - self._telegram_ok_at, 1),
                'last_latency_ms': self._telegram_latency_ms,
                'consecutive_failures': self._telegram_failures,
                'last_error': self._telegram_error,
                'rejected': self._telegram_rejected,
                'canary_interval_seconds': CANARY_INTERVAL_SECONDS,
            },
            'updates': {
                'window_seconds': TIMING_WINDOW_SECONDS,
                'in_window': len(durations),
                'processed_total': self._updates_total,
                'p95_ms': _percentile_ms(durations, 0.95),
                'max_ms': None if not durations else int(durations[-1] * 1000),
                # Справка, не повод для тревоги: ночью апдейтов нет.
                'seconds_since_last': None if self._last_update_at is None else round(now - self._last_update_at, 1),
            },
            'uptime_seconds': int(now - self._started_at),
        }

    # ─── канарейка ───

    async def run_canary(self, bot) -> None:
        """Раз в ``CANARY_INTERVAL_SECONDS`` проверять путь до Telegram.

        Своей же сессией бота и дешёвым методом ``getMe``: лимитов Telegram два
        запроса в минуту не трогают, а маршрут проверяется тот самый.
        """
        logger.info('🐤 Канарейка Telegram запущена', interval_seconds=CANARY_INTERVAL_SECONDS)
        while True:
            started = time.monotonic()
            try:
                await bot.get_me()
            except TelegramNetworkError as error:
                self.mark_telegram_failure(str(error))
                logger.warning(
                    'Канарейка: Telegram не ответил',
                    consecutive_failures=self._telegram_failures,
                    error=str(error)[:200],
                )
            except Exception as error:
                # Телеграм ответил отказом (неверный токен, бот заблокирован):
                # повторять незачем, но знать об этом наблюдателю нужно.
                self.mark_telegram_failure(str(error), rejected=True)
                logger.warning('Канарейка: Telegram отклонил запрос', error=str(error)[:200])
            else:
                self.mark_telegram_ok(int((time.monotonic() - started) * 1000))

            await asyncio.sleep(CANARY_INTERVAL_SECONDS)


def _percentile_ms(sorted_durations: list[float], share: float) -> int | None:
    """Перцентиль из отсортированных длительностей, в миллисекундах."""
    if not sorted_durations:
        return None
    index = min(len(sorted_durations) - 1, int(len(sorted_durations) * share))
    return int(sorted_durations[index] * 1000)


#: Один на процесс: пишут мидлвара и канарейка, читает ``/health``.
availability_monitor = AvailabilityMonitor()
