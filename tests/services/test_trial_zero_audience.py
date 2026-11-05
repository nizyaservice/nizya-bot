"""Аудитория рассылки «Триал 0 ГБ»: триал, ровно 0 байт, аккаунт жив в панели.

Правила (заданы владельцем):

* число в предпросмотре обязано совпадать с фактическими получателями —
  поэтому и то и другое считает одна функция;
* трафик — **строго 0 байт**: 2 МБ это уже не ноль (на экранах такой расход
  показывается как «0.0 ГБ», но в аудиторию человек не входит);
* срок триала не важен: истёкший получит рассылку;
* важно одно — не удалён ли аккаунт из Remnawave.

Расход и существование аккаунта берём одним обходом панели: раньше тут была
колонка `subscriptions.traffic_used_gb`, а у её синхронизаторов допуск 0.01 ГБ
(≈10 МБ), то есть она могла показывать ноль человеку, который уже скачал
несколько мегабайт. Признака «удалён из панели» в базе бота нет вовсе.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.services.trial_zero_audience as audience
from app.handlers.admin import messages as admin_messages


@pytest.fixture(autouse=True)
def _clean_cache():
    audience.reset_panel_traffic_cache()
    yield
    audience.reset_panel_traffic_cache()


def _user(user_id: int, *, panel_id: int | None = None):
    return SimpleNamespace(id=user_id, telegram_id=100 + user_id, remnawave_id=panel_id)


def _ids(users) -> list[int]:
    return sorted(user.id for user in users)


# ─── отбор по карте панели ───


def test_zero_bytes_gets_the_broadcast():
    rows = [(_user(1, panel_id=11), 11)]

    assert _ids(audience.pick_zero_traffic_users(rows, {11: 0})) == [1]


@pytest.mark.parametrize('used_bytes', [1, 2 * 1024 * 1024, 10 * 1024 * 1024, 1024**3])
def test_any_traffic_at_all_excludes_the_user(used_bytes):
    """2 МБ — это уже не «0 ГБ», хотя на экране показывается как 0.0 ГБ."""
    rows = [(_user(1, panel_id=11), 11)]

    assert audience.pick_zero_traffic_users(rows, {11: used_bytes}) == []


def test_missing_panel_account_excludes_the_user():
    """Ключа нет в карте — значит аккаунта в панели нет: удалён или не создался."""
    rows = [(_user(1, panel_id=11), 11)]

    assert audience.pick_zero_traffic_users(rows, {22: 0}) == []


def test_user_without_panel_id_is_skipped():
    rows = [(_user(1, panel_id=None), None)]

    assert audience.pick_zero_traffic_users(rows, {11: 0}) == []


def test_panel_reporting_nothing_counts_as_zero():
    """Аккаунт есть, расход панель не прислала — считаем нулём."""
    rows = [(_user(1, panel_id=11), 11)]

    assert _ids(audience.pick_zero_traffic_users(rows, {11: None})) == [1]


def test_user_with_two_trials_counted_once():
    user = _user(1, panel_id=11)
    rows = [(user, 11), (user, 12)]

    assert _ids(audience.pick_zero_traffic_users(rows, {11: 0, 12: 0})) == [1]


def test_traffic_on_any_live_trial_disqualifies_the_person():
    """Считаем по человеку: подключился хоть одним триалом — не наш адресат."""
    user = _user(1, panel_id=11)
    rows = [(user, 11), (user, 12)]

    assert audience.pick_zero_traffic_users(rows, {11: 5, 12: 0}) == []


def test_deleted_account_does_not_spoil_a_live_zero_one():
    """Расход удалённого аккаунта нам неизвестен — он просто не в расчёте."""
    user = _user(1, panel_id=11)
    rows = [(user, 11), (user, 12)]

    assert _ids(audience.pick_zero_traffic_users(rows, {11: 0})) == [1]


def test_single_tariff_falls_back_to_the_user_account(monkeypatch):
    monkeypatch.setattr(type(audience.settings), 'is_multi_tariff_enabled', lambda self: False)
    rows = [(_user(1, panel_id=11), None)]

    assert _ids(audience.pick_zero_traffic_users(rows, {11: 0})) == [1]


def test_multi_tariff_never_borrows_the_user_account(monkeypatch):
    """В мультитарифе users.remnawave_id — аккаунт соседней подписки."""
    monkeypatch.setattr(type(audience.settings), 'is_multi_tariff_enabled', lambda self: True)
    rows = [(_user(1, panel_id=11), None)]

    assert audience.pick_zero_traffic_users(rows, {11: 0}) == []


# ─── обход панели ───


def _api_with(panel_users):
    api = AsyncMock()
    api.get_all_users_stream = AsyncMock(return_value=panel_users)

    class _Client:
        async def __aenter__(self):
            return api

        async def __aexit__(self, *exc):
            return False

    service = MagicMock()
    service.is_configured = True
    service.get_api_client = MagicMock(return_value=_Client())
    service.api_double = api
    return service


@pytest.mark.asyncio
async def test_panel_map_is_built_in_one_pass():
    service = _api_with([SimpleNamespace(id=11, used_traffic_bytes=0), SimpleNamespace(id=12, used_traffic_bytes=5)])

    with patch.object(audience, 'RemnaWaveService', return_value=service):
        traffic = await audience.get_panel_traffic_map()

    assert traffic == {11: 0, 12: 5}
    service.api_double.get_all_users_stream.assert_awaited_once()


@pytest.mark.asyncio
async def test_second_call_reuses_the_cached_map():
    """Предпросмотр и отправка подряд не должны обходить панель дважды."""
    service = _api_with([SimpleNamespace(id=11, used_traffic_bytes=0)])

    with patch.object(audience, 'RemnaWaveService', return_value=service):
        await audience.get_panel_traffic_map()
        await audience.get_panel_traffic_map()

    service.api_double.get_all_users_stream.assert_awaited_once()


@pytest.mark.asyncio
async def test_force_refresh_walks_the_panel_again():
    service = _api_with([SimpleNamespace(id=11, used_traffic_bytes=0)])

    with patch.object(audience, 'RemnaWaveService', return_value=service):
        await audience.get_panel_traffic_map()
        await audience.get_panel_traffic_map(force_refresh=True)

    assert service.api_double.get_all_users_stream.await_count == 2


@pytest.mark.asyncio
async def test_unreachable_panel_refuses_instead_of_returning_everyone():
    """Пустая карта означала бы «всех удалили», а молчаливый фоллбек — рассылку не тем."""
    service = _api_with([])
    service.api_double.get_all_users_stream = AsyncMock(side_effect=RuntimeError('panel down'))

    with (
        patch.object(audience, 'RemnaWaveService', return_value=service),
        pytest.raises(audience.PanelAudienceUnavailableError),
    ):
        await audience.get_panel_traffic_map()


@pytest.mark.asyncio
async def test_unconfigured_panel_refuses():
    service = _api_with([])
    service.is_configured = False

    with (
        patch.object(audience, 'RemnaWaveService', return_value=service),
        pytest.raises(audience.PanelAudienceUnavailableError),
    ):
        await audience.get_panel_traffic_map()


# ─── предпросмотр и отправка считают одно и то же ───


@pytest.mark.asyncio
async def test_preview_count_matches_the_recipients():
    users = [_user(1, panel_id=11), _user(2, panel_id=12)]

    with patch.object(admin_messages, 'get_trial_zero_traffic_users', new=AsyncMock(return_value=users)) as resolver:
        count = await admin_messages.get_target_users_count(AsyncMock(), 'trial_zero')
        recipients = await admin_messages.get_target_users(AsyncMock(), 'trial_zero')

    assert count == len(recipients) == 2
    assert resolver.await_count == 2, 'оба пути обязаны идти через один и тот же отбор'


@pytest.mark.asyncio
async def test_recipients_are_resolved_without_loading_every_user():
    """Ранний выход: аудитории не нужна выгрузка всех активных с подписками."""
    with (
        patch.object(admin_messages, 'get_trial_zero_traffic_users', new=AsyncMock(return_value=[])),
        patch.object(admin_messages, 'get_users_list', new=AsyncMock()) as users_list,
    ):
        await admin_messages.get_target_users(AsyncMock(), 'trial_zero')

    users_list.assert_not_awaited()
