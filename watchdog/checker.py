"""Наблюдатель доступности бота: следит и пишет в Telegram вторым ботом.

Живёт отдельным контейнером рядом с ботом, но **своим процессом**: падение,
перезапуск и зависание бота его не касаются. Оттуда же и ограничение, о
котором надо помнить: если умрёт сама ВПС или её сеть, молчать будут оба, и
тревогу не пришлёт никто. Закрывается это только наблюдателем на другой
машине — см. README.

Зависимостей нет намеренно: стандартная библиотека и всё. Образ ставится из
``python:*-alpine`` без сборки, обновлять нечего, сломаться на `pip install`
во время инцидента тоже нечему.

Что проверяется каждую минуту:

1. ``/health`` бота — отвечает ли процесс вообще;
2. ``availability.telegram.seconds_since_ok`` оттуда — не оборвался ли путь
   бот↔Telegram (в эпизоде 2026-11-20 процесс отвечал за 24 мс, а апдейты
   копились на стороне Telegram: простым пингом такое не видно);
3. ``availability.updates.p95_ms`` — не отвечает ли бот слишком медленно;
4. свой собственный запрос к Telegram — чтобы в тревоге сразу сказать, чей
   это сбой: бота или связи с Telegram у всех.

Тревога уходит после ``FAILURES_BEFORE_ALERT`` подряд плохих проверок (чтобы
единичная заминка не будила ночью), повторно про тот же инцидент не пишет, а
при восстановлении сообщает длительность простоя. Состояние инцидента лежит в
файле: перезапуск наблюдателя не превращается во второй алерт.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or '').strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


HEALTH_URL = (os.environ.get('WATCHDOG_HEALTH_URL') or 'http://bot:8080/health').strip()
INTERVAL_SECONDS = _env_int('WATCHDOG_INTERVAL_SECONDS', 60)
HEALTH_TIMEOUT_SECONDS = _env_int('WATCHDOG_HEALTH_TIMEOUT_SECONDS', 10)
#: Отдельный таймаут для запроса к Telegram: здесь TLS-рукопожатие через
#: полмира, и жёсткий срок давал бы «недоступен» на ровном месте.
TELEGRAM_TIMEOUT_SECONDS = _env_int('WATCHDOG_TELEGRAM_TIMEOUT_SECONDS', 10)
#: Сколько секунд без успешного контакта бота с Telegram считаем обрывом.
#: Канарейка внутри бота ходит раз в 30 с, так что 180 с — это шесть промахов.
TELEGRAM_STALE_SECONDS = _env_int('WATCHDOG_TELEGRAM_STALE_SECONDS', 180)
#: Порог «отвечает слишком медленно» по p95 времени обработки апдейта.
SLOW_P95_MS = _env_int('WATCHDOG_SLOW_P95_MS', 5000)
#: Сколько апдейтов должно быть в окне, чтобы p95 что-то значил.
SLOW_MIN_SAMPLES = _env_int('WATCHDOG_SLOW_MIN_SAMPLES', 5)
FAILURES_BEFORE_ALERT = _env_int('WATCHDOG_FAILURES_BEFORE_ALERT', 3)

ALERT_TOKEN = (os.environ.get('WATCHDOG_ALERT_BOT_TOKEN') or '').strip()
ALERT_CHAT_ID = (os.environ.get('WATCHDOG_ALERT_CHAT_ID') or '').strip()
STATE_PATH = Path((os.environ.get('WATCHDOG_STATE_PATH') or '/state/watchdog.json').strip())

#: Без токена и чата наблюдатель работает как «только лог»: проверки идут,
#: тревоги пишутся в stdout. Полезно на первом запуске и в тестах.
ALERTS_CONFIGURED = bool(ALERT_TOKEN and ALERT_CHAT_ID)


@dataclass
class Verdict:
    """Итог одной проверки."""

    healthy: bool
    #: Короткая причина для человека; ``None`` — всё в порядке.
    reason: str | None = None
    #: Виден ли Telegram самому наблюдателю — отвечает на «чей это сбой».
    telegram_reachable: bool = True


def check(health: dict | None, telegram_reachable: bool) -> Verdict:
    """Решение по одной проверке. Чистая функция — её и проверяют тесты."""
    if health is None:
        if not telegram_reachable:
            return Verdict(
                healthy=False,
                reason='бот не отвечает на /health, и Telegram недоступен с этой машины',
                telegram_reachable=False,
            )
        return Verdict(healthy=False, reason='бот не отвечает на /health', telegram_reachable=True)

    availability = health.get('availability') or {}
    telegram = availability.get('telegram') or {}
    updates = availability.get('updates') or {}

    if telegram.get('rejected'):
        # Отказ по существу: неверный или отозванный токен, бот заблокирован.
        # Повторы это не лечат, и формулировка должна отличаться от сетевой.
        return Verdict(
            healthy=False,
            reason=f'Telegram отклоняет запросы бота: {telegram.get("last_error") or "причина неизвестна"}',
            telegram_reachable=telegram_reachable,
        )

    since_ok = telegram.get('seconds_since_ok')
    if since_ok is None or since_ok > TELEGRAM_STALE_SECONDS:
        age = 'ни разу' if since_ok is None else f'{int(since_ok)} с назад'
        return Verdict(
            healthy=False,
            reason=f'бот не получал ответа от Telegram ({age})',
            telegram_reachable=telegram_reachable,
        )

    p95 = updates.get('p95_ms')
    samples = updates.get('in_window') or 0
    if p95 is not None and samples >= SLOW_MIN_SAMPLES and p95 > SLOW_P95_MS:
        return Verdict(
            healthy=False,
            reason=f'бот отвечает медленно: p95 {int(p95)} мс за последние {updates.get("window_seconds")} с',
            telegram_reachable=telegram_reachable,
        )

    return Verdict(healthy=True)


def describe_alert(verdict: Verdict, failures: int) -> str:
    """Текст тревоги: что случилось и чей это сбой."""
    blame = (
        'Telegram недоступен и с этой машины — похоже на внешний сбой, а не на бота.'
        if not verdict.telegram_reachable
        else 'Telegram с этой машины доступен — значит дело в боте или его хосте.'
    )
    return (
        f'🔴 <b>Бот недоступен</b>\n\n'
        f'Причина: {verdict.reason}\n'
        f'Плохих проверок подряд: {failures}\n\n'
        f'{blame}'
    )


def describe_recovery(downtime_seconds: int, reason: str | None) -> str:
    minutes, seconds = divmod(max(0, downtime_seconds), 60)
    duration = f'{minutes} мин {seconds} с' if minutes else f'{seconds} с'
    tail = f'\nБыло: {reason}' if reason else ''
    return f'🟢 <b>Бот снова отвечает</b>\n\nПростой: {duration}{tail}'


def fetch_health() -> dict | None:
    """Прочитать ``/health`` бота. ``None`` — не ответил или ответил мусором."""
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=HEALTH_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode('utf-8'))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def telegram_reachable() -> bool:
    """Виден ли Telegram самому наблюдателю.

    Токен не нужен: интересует только то, доходит ли запрос до api.telegram.org.
    Любой ответ сервера (включая 404) означает «связь есть».
    """
    try:
        request = urllib.request.Request('https://api.telegram.org/', method='HEAD')
        with urllib.request.urlopen(request, timeout=TELEGRAM_TIMEOUT_SECONDS) as response:
            return response.status < 500
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def send_alert(text: str) -> bool:
    """Отправить сообщение вторым ботом. ``False`` — не доставлено."""
    if not ALERTS_CONFIGURED:
        return False

    data = urllib.parse.urlencode(
        {'chat_id': ALERT_CHAT_ID, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': 'true'}
    ).encode('utf-8')
    url = f'https://api.telegram.org/bot{ALERT_TOKEN}/sendMessage'
    try:
        with urllib.request.urlopen(url, data=data, timeout=TELEGRAM_TIMEOUT_SECONDS) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        print(f'[watchdog] тревогу не удалось отправить: {error}', flush=True)
        return False


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    except OSError as error:
        # Без файла наблюдатель всё равно работает, просто перезапуск может
        # повторить тревогу — это не повод останавливаться.
        print(f'[watchdog] состояние не сохранено: {error}', flush=True)


def main() -> int:
    print(
        f'[watchdog] старт: {HEALTH_URL} раз в {INTERVAL_SECONDS} с; '
        f'обрыв Telegram > {TELEGRAM_STALE_SECONDS} с, медленно > {SLOW_P95_MS} мс, '
        f'тревога после {FAILURES_BEFORE_ALERT} промахов',
        flush=True,
    )
    if not ALERTS_CONFIGURED:
        print(
            '[watchdog] WATCHDOG_ALERT_BOT_TOKEN/WATCHDOG_ALERT_CHAT_ID не заданы: '
            'работаю в режиме «только лог», сообщения в Telegram отправляться не будут',
            flush=True,
        )

    state = load_state()
    failures = int(state.get('failures') or 0)
    alerted = bool(state.get('alerted'))
    incident_started_at = state.get('incident_started_at')
    last_reason = state.get('last_reason')

    while True:
        health = fetch_health()
        verdict = check(health, telegram_reachable=True)
        if not verdict.healthy:
            # Спрашиваем Telegram только когда уже плохо: это запрос наружу, и
            # на здоровых проверках он не нужен. Вердикт пересчитываем — от
            # доступности Telegram зависит формулировка «чей это сбой».
            verdict = check(health, telegram_reachable=telegram_reachable())

        if verdict.healthy:
            if alerted:
                downtime = int(time.time() - (incident_started_at or time.time()))
                send_alert(describe_recovery(downtime, last_reason))
                print(f'[watchdog] восстановление, простой {downtime} с', flush=True)
            failures = 0
            alerted = False
            incident_started_at = None
            last_reason = None
        else:
            failures += 1
            last_reason = verdict.reason
            if incident_started_at is None:
                incident_started_at = time.time()
            print(f'[watchdog] плохая проверка {failures}: {verdict.reason}', flush=True)
            if failures >= FAILURES_BEFORE_ALERT and not alerted:
                if ALERTS_CONFIGURED:
                    alerted = send_alert(describe_alert(verdict, failures))
                else:
                    # Отправлять некому — считаем инцидент объявленным, чтобы не
                    # повторять одну и ту же строку в логе каждую минуту.
                    alerted = True

        save_state(
            {
                'failures': failures,
                'alerted': alerted,
                'incident_started_at': incident_started_at,
                'last_reason': last_reason,
            }
        )
        time.sleep(INTERVAL_SECONDS)


if __name__ == '__main__':
    sys.exit(main())
