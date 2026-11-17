"""Снос аккаунтов Remnawave через N дней после истечения подписки.

Удаление необратимо, поэтому проверяется не только «сносит», но и весь набор
случаев, когда сносить НЕЛЬЗЯ: свежеистёкшие, подписки под грейс-доступом,
люди с другой живой подпиской.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.expired_panel_cleanup_service as svc
from app.config import settings
from app.database.models import Subscription, SubscriptionStatus, User
from tests.fixtures.sqlite_memory import memory_session


TABLES = (User.__table__, Subscription.__table__)

PANEL_ID = 4242


@pytest.fixture
def api():
    client = MagicMock()
    client.delete_user = AsyncMock(return_value=True)
    client.disable_user = AsyncMock(return_value=True)
    return client


@pytest.fixture(autouse=True)
def _configured_panel(monkeypatch, api):
    """Панель настроена, клиент — заглушка: сеть в тестах не трогаем."""

    class _Service:
        is_configured = True

        def get_api_client(self):
            class _Ctx:
                async def __aenter__(self_inner):
                    return api

                async def __aexit__(self_inner, *exc):
                    return False

            return _Ctx()

    monkeypatch.setattr('app.services.remnawave_service.RemnaWaveService', lambda: _Service())
    monkeypatch.setattr(settings, 'REMNAWAVE_DELETE_EXPIRED_AFTER_DAYS', 1)
    monkeypatch.setattr(type(settings), 'is_multi_tariff_enabled', lambda self: False)
    monkeypatch.setattr(type(settings), 'get_remnawave_user_delete_mode', lambda self: 'delete')


async def _user_with_expired(
    db,
    *,
    days_ago: float = 3,
    status: str | None = None,
    telegram_id: int = 1,
    panel_id: int | None = PANEL_ID,
    **sub_fields,
) -> Subscription:
    user = User(telegram_id=telegram_id, first_name='Тест', language='ru', remnawave_id=panel_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    now = datetime.now(UTC)
    subscription = Subscription(
        user_id=user.id,
        status=status or SubscriptionStatus.EXPIRED.value,
        is_trial=False,
        start_date=now - timedelta(days=30),
        end_date=now - timedelta(days=days_ago),
        traffic_limit_gb=100,
        device_limit=1,
        remnawave_id=panel_id,
        remnawave_short_uuid='short-uuid',
        subscription_url='https://sub.example/x',
        remnawave_short_id=f'short-{telegram_id}',
        **sub_fields,
    )
    db.add(subscription)
    await db.commit()
    await db.refresh(subscription)
    return subscription


# ---- сносит ----


async def test_deletes_panel_user_after_the_grace_days(monkeypatch, api) -> None:
    async with memory_session(monkeypatch, TABLES) as db:
        await _user_with_expired(db, days_ago=3)

        processed = await svc.cleanup_expired_panel_users(db)

        assert processed == 1
        api.delete_user.assert_awaited_once_with(PANEL_ID)


async def test_panel_identity_is_forgotten(monkeypatch, api) -> None:
    """Иначе повторная покупка полезет обновлять несуществующий аккаунт."""
    async with memory_session(monkeypatch, TABLES) as db:
        subscription = await _user_with_expired(db, days_ago=3)

        await svc.cleanup_expired_panel_users(db)
        await db.refresh(subscription)
        user = await db.get(User, subscription.user_id)

        assert subscription.remnawave_id is None
        assert subscription.remnawave_short_uuid is None
        assert subscription.subscription_url == ''
        assert user.remnawave_id is None


async def test_disable_mode_keeps_the_link(monkeypatch, api) -> None:
    """В режиме disable аккаунт жив — связь рвать нельзя.

    Иначе следующая покупка заведёт рядом дубль вместо включения
    отключённого аккаунта.
    """
    monkeypatch.setattr(type(settings), 'get_remnawave_user_delete_mode', lambda self: 'disable')
    async with memory_session(monkeypatch, TABLES) as db:
        subscription = await _user_with_expired(db, days_ago=3)

        await svc.cleanup_expired_panel_users(db)
        await db.refresh(subscription)

        api.disable_user.assert_awaited_once_with(PANEL_ID)
        api.delete_user.assert_not_awaited()
        assert subscription.remnawave_id == PANEL_ID


# ---- не сносит ----


async def test_keeps_recently_expired(monkeypatch, api) -> None:
    """Сутки ещё не прошли — у человека есть время оплатить."""
    async with memory_session(monkeypatch, TABLES) as db:
        await _user_with_expired(db, days_ago=0.5)

        assert await svc.cleanup_expired_panel_users(db) == 0
        api.delete_user.assert_not_awaited()


async def test_keeps_active_subscription(monkeypatch, api) -> None:
    async with memory_session(monkeypatch, TABLES) as db:
        await _user_with_expired(db, days_ago=10, status=SubscriptionStatus.ACTIVE.value)

        assert await svc.cleanup_expired_panel_users(db) == 0
        api.delete_user.assert_not_awaited()


async def test_keeps_subscription_under_grace(monkeypatch, api) -> None:
    """Грейс-доступ продлил человеку панель — снос обрубил бы живой доступ."""
    async with memory_session(monkeypatch, TABLES) as db:
        await _user_with_expired(db, days_ago=10, grace_session_open=True)

        assert await svc.cleanup_expired_panel_users(db) == 0
        api.delete_user.assert_not_awaited()


async def test_keeps_user_with_another_live_subscription(monkeypatch, api) -> None:
    """Аккаунт панели общий — снос убил бы доступ по второй, живой подписке."""
    async with memory_session(monkeypatch, TABLES) as db:
        expired = await _user_with_expired(db, days_ago=10)
        now = datetime.now(UTC)
        db.add(
            Subscription(
                user_id=expired.user_id,
                status=SubscriptionStatus.ACTIVE.value,
                is_trial=False,
                start_date=now,
                end_date=now + timedelta(days=30),
                traffic_limit_gb=100,
                device_limit=1,
                # remnawave_id не ставим: в одиночном режиме аккаунт панели
                # висит на пользователе, а колонка подписки уникальна.
                # short_id задаём явно — он тоже уникален, а дефолт у обеих строк общий.
                remnawave_short_id='second-sub',
            )
        )
        await db.commit()

        assert await svc.cleanup_expired_panel_users(db) == 0
        api.delete_user.assert_not_awaited()


async def test_disabled_by_zero_days(monkeypatch, api) -> None:
    """0 — выключатель: ни одного обращения к панели."""
    monkeypatch.setattr(settings, 'REMNAWAVE_DELETE_EXPIRED_AFTER_DAYS', 0)
    async with memory_session(monkeypatch, TABLES) as db:
        await _user_with_expired(db, days_ago=30)

        assert await svc.cleanup_expired_panel_users(db) == 0
        api.delete_user.assert_not_awaited()


async def test_panel_error_does_not_break_the_batch(monkeypatch, api) -> None:
    """Сбой по одному аккаунту не должен останавливать проход и чистить связь."""
    api.delete_user = AsyncMock(side_effect=RuntimeError('panel down'))
    async with memory_session(monkeypatch, TABLES) as db:
        subscription = await _user_with_expired(db, days_ago=3)

        assert await svc.cleanup_expired_panel_users(db) == 0
        await db.refresh(subscription)
        assert subscription.remnawave_id == PANEL_ID, 'связь стёрта, хотя аккаунт в панели остался'


# ---- очередь не забивается ----


async def test_processed_subscriptions_do_not_block_the_queue(monkeypatch, api) -> None:
    """Главная причина, по которой чистка переставала работать.

    Окно — самые старые истёкшие подписки. Обработанная подписка остаётся
    EXPIRED с той же датой, поэтому без отметки она попадает в окно снова и
    занимает место: после первого прохода окно состояло целиком из
    обработанных, и аккаунты свежих истечений оставались в панели навсегда.
    """
    async with memory_session(monkeypatch, TABLES) as db:
        # Окно целиком забито подписками, которые сносить нечего — панельного
        # аккаунта у них нет, и это не изменится.
        for i in range(svc._BATCH_LIMIT):
            await _user_with_expired(db, telegram_id=1000 + i, days_ago=100 - i * 0.1, panel_id=None)
        target = await _user_with_expired(db, telegram_id=1, days_ago=2)

        processed = await svc.cleanup_expired_panel_users(db)

        assert processed == 1, 'аккаунт свежеистёкшей подписки обязан сноситься'
        api.delete_user.assert_awaited_once_with(PANEL_ID)
        await db.refresh(target)
        assert target.panel_cleanup_at is not None


async def test_second_pass_does_not_touch_the_panel_again(monkeypatch, api) -> None:
    """Повторный проход по той же подписке — ни одного запроса в панель."""
    monkeypatch.setattr(type(settings), 'get_remnawave_user_delete_mode', lambda self: 'disable')
    async with memory_session(monkeypatch, TABLES) as db:
        await _user_with_expired(db, days_ago=3)

        assert await svc.cleanup_expired_panel_users(db) == 1
        api.disable_user.reset_mock()

        assert await svc.cleanup_expired_panel_users(db) == 0
        api.disable_user.assert_not_awaited()


async def test_renewed_and_expired_again_is_cleaned_once_more(monkeypatch, api) -> None:
    """Отметка привязана к сроку, а не к подписке.

    Состояние вернувшегося клиента: чистку делали давно, потом он продлил
    подписку (срок уехал в будущее, то есть позже отметки), и продление тоже
    истекло. Такую подписку нужно чистить заново — иначе её новый панельный
    аккаунт останется в панели навсегда.
    """
    monkeypatch.setattr(type(settings), 'get_remnawave_user_delete_mode', lambda self: 'disable')
    async with memory_session(monkeypatch, TABLES) as db:
        subscription = await _user_with_expired(db, days_ago=2)
        subscription.panel_cleanup_at = datetime.now(UTC) - timedelta(days=10)
        await db.commit()

        assert await svc.cleanup_expired_panel_users(db) == 1
        api.disable_user.assert_awaited_once_with(PANEL_ID)


async def test_account_already_gone_from_the_panel_counts_as_done(monkeypatch, api) -> None:
    """404 — не ошибка: аккаунт снесли до нас, подписку нужно убрать из очереди."""
    api.delete_user = AsyncMock(side_effect=RuntimeError('404 User not found'))
    async with memory_session(monkeypatch, TABLES) as db:
        subscription = await _user_with_expired(db, days_ago=3)

        assert await svc.cleanup_expired_panel_users(db) == 1
        await db.refresh(subscription)
        assert subscription.remnawave_id is None
        assert subscription.panel_cleanup_at is not None


async def test_panel_outage_leaves_the_subscription_in_the_queue(monkeypatch, api) -> None:
    """Панель лежит — отметку не ставим, иначе аккаунт остался бы в панели навсегда."""
    api.delete_user = AsyncMock(side_effect=RuntimeError('panel down'))
    async with memory_session(monkeypatch, TABLES) as db:
        subscription = await _user_with_expired(db, days_ago=3)

        assert await svc.cleanup_expired_panel_users(db) == 0
        await db.refresh(subscription)
        assert subscription.panel_cleanup_at is None


def test_candidates_are_filtered_in_sql() -> None:
    """Условия отбора обязаны быть в запросе, а не проверками после LIMIT.

    Проверка после ``LIMIT`` — это и есть пробка: непроходимые подписки самые
    старые, поэтому занимают окно целиком и навсегда.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / 'app' / 'services' / 'expired_panel_cleanup_service.py').read_text(
        encoding='utf-8'
    )
    query = source[source.index('async def _select_candidates') : source.index('def _panel_user_id')]

    assert '_not_cleaned_yet()' in query
    assert '_panel_account_exists()' in query
    assert '_grace_is_closed(' in query
    assert 'ACTIVE.value, SubscriptionStatus.TRIAL.value' in query, 'другая живая подписка — тоже в SQL'


def test_monitoring_cycle_runs_the_cleanup() -> None:
    """Сервис должен быть подключён к циклу, иначе он мёртвый код."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / 'app' / 'services' / 'monitoring_service.py').read_text(
        encoding='utf-8'
    )

    assert 'await self._cleanup_expired_panel_users(db)' in source
    assert 'cleanup_expired_panel_users' in source
