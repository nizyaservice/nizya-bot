"""Покупка тарифа на портале: каталог → подтверждение → списание.

Что здесь закреплено:

* цену и итоговое списание считает бот — портал только показывает его
  подписи и передаёт выбор человека;
* подтверждение открывается GET'ом и ничего не списывает: обновление страницы
  и «назад» безопасны;
* списывает только POST с CSRF-токеном сессии;
* отказ «не хватает средств» (402) показывается текстом бота, а не пятисоткой;
* из каталога убрано то, чего портал не умеет оформлять, и об этом сказано;
* каталог открыт без входа (цены смотрят до регистрации), а вот подтверждение
  покупки уводит на вход — и возвращает ровно на выбранный тариф.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from portal.api import CabinetApi


TARIFF_BASIC: dict[str, Any] = {
    'id': 11,
    'name': 'Базовый',
    'description': 'Для одного устройства',
    'traffic_limit_label': '♾️ Безлимит',
    'traffic_limit_gb': 0,
    'device_limit': 1,
    'servers_count': 3,
    'is_available': True,
    'is_purchased': False,
    'is_current': False,
    'is_daily': False,
    'periods': [
        {'days': 30, 'label': '1 месяц', 'price_kopeks': 29900, 'price_label': '299 ₽'},
        {
            'days': 90,
            'label': '3 месяца',
            'price_kopeks': 79900,
            'price_label': '799 ₽',
            'original_price_label': '897 ₽',
            'is_highlighted': True,
        },
    ],
}

TARIFF_DAILY: dict[str, Any] = {
    'id': 12,
    'name': 'Суточный',
    'traffic_limit_label': '10 ГБ',
    'device_limit': 1,
    'is_available': True,
    'is_daily': True,
    'periods': [{'days': 1, 'label': '1 день', 'price_kopeks': 1500, 'price_label': '15 ₽'}],
}

TARIFF_CUSTOM_DAYS_ONLY: dict[str, Any] = {
    'id': 13,
    'name': 'По дням',
    'traffic_limit_label': '50 ГБ',
    'device_limit': 2,
    'is_available': True,
    'custom_days_enabled': True,
    'periods': [],
}


def _options(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        'sales_mode': 'tariffs',
        'tariffs': [TARIFF_BASIC],
        'balance_kopeks': 50000,
        'balance_label': '500 ₽',
        'has_subscription': False,
    }
    payload.update(overrides)
    return payload


class Bot:
    """Двойник бота: отвечает на вход, профиль и покупку, помнит запросы."""

    def __init__(
        self,
        options: dict[str, Any],
        purchase: httpx.Response | None = None,
        available_kopeks: int | None = None,
    ) -> None:
        self.options = options
        #: Сумма, доступная к оплате (деньги + бонусные баллы). По умолчанию
        #: равна денежному кошельку каталога — баллов у человека нет.
        self.available_kopeks = (
            available_kopeks if available_kopeks is not None else int(options.get('balance_kopeks') or 0)
        )
        self.purchase = purchase or httpx.Response(
            200,
            json={
                'success': True,
                'message': "Тариф 'Базовый' успешно активирован",
                'charged_label': '299 ₽',
                'balance_label': '201 ₽',
                'subscription': {'end_date': '2026-12-16T10:00:00'},
            },
        )
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))

        if path.endswith('/auth/email/otp/verify'):
            return httpx.Response(
                200,
                json={
                    'access_token': 'access-1',
                    'refresh_token': 'refresh-1',
                    'user': {'id': 7, 'email': 'user@example.com'},
                },
            )
        if path.endswith('/auth/me'):
            return httpx.Response(200, json={'id': 7, 'email': 'user@example.com'})
        if path.endswith('/balance'):
            return httpx.Response(
                200,
                json={
                    'balance_kopeks': int(self.options.get('balance_kopeks') or 0),
                    'bonus_points_kopeks': self.available_kopeks - int(self.options.get('balance_kopeks') or 0),
                    'available_kopeks': self.available_kopeks,
                },
            )
        if path.endswith('/subscription/purchase-options'):
            return httpx.Response(200, json=self.options)
        if path.endswith('/public/tariffs'):
            # Публичная витрина: тот же каталог, но без баланса и скидок.
            return httpx.Response(
                200,
                json={'sales_mode': self.options['sales_mode'], 'tariffs': self.options['tariffs']},
            )
        if path.endswith('/subscription/purchase-tariff'):
            return self.purchase
        return httpx.Response(200, json={})

    @property
    def purchase_calls(self) -> list[dict[str, Any] | None]:
        return [body for _, path, body in self.calls if path.endswith('/subscription/purchase-tariff')]


@pytest.fixture
def bot() -> Bot:
    return Bot(_options())


@pytest.fixture
def logged_in(portal_app):
    """Фабрика: подставить двойника бота и войти на портал по коду."""
    opened: list[TestClient] = []

    def make(double: Bot) -> TestClient:
        portal_app.state.api_double = CabinetApi(
            httpx.AsyncClient(base_url='http://bot.invalid', transport=httpx.MockTransport(double))
        )
        client = TestClient(portal_app, follow_redirects=False)
        client.__enter__()
        opened.append(client)
        client.post('/login/code', data={'email': 'user@example.com', 'code': '123456'})
        return client

    yield make

    for client in opened:
        client.__exit__(None, None, None)


@pytest.fixture
def client(logged_in, bot: Bot) -> TestClient:
    return logged_in(bot)


# ─── каталог ───


def test_catalog_shows_tariffs_with_bot_prices(client: TestClient):
    """Подписи цен берутся у бота: портал не пересчитывает копейки сам."""
    response = client.get('/tariffs')

    assert response.status_code == 200
    assert 'Базовый' in response.text
    # По умолчанию выбран срок, отмеченный оператором как выгодный, — 3 месяца.
    assert '799' in response.text, 'цена выбранного срока'
    assert '/tariffs?days=30' in response.text, 'остальные сроки — в переключателе'
    assert '500' in response.text, 'сумма человека в шапке'


def test_catalog_opens_without_a_session(portal_app, bot: Bot):
    """Цену человек смотрит до регистрации — иначе он просто уйдёт."""
    portal_app.state.api_double = CabinetApi(
        httpx.AsyncClient(base_url='http://bot.invalid', transport=httpx.MockTransport(bot))
    )
    with TestClient(portal_app, follow_redirects=False) as anonymous:
        response = anonymous.get('/tariffs')

    assert response.status_code == 200
    assert 'Базовый' in response.text
    # Анониму цены даёт публичная ручка: персональных скидок у него быть не может.
    assert [path for _, path, _ in bot.calls if path.endswith('/public/tariffs')]
    assert not [path for _, path, _ in bot.calls if path.endswith('/purchase-options')]


def test_buying_requires_login_and_comes_back_to_the_chosen_tariff(portal_app, bot: Bot):
    portal_app.state.api_double = CabinetApi(
        httpx.AsyncClient(base_url='http://bot.invalid', transport=httpx.MockTransport(bot))
    )
    with TestClient(portal_app, follow_redirects=False) as anonymous:
        response = anonymous.get('/tariffs/11?days=30')

    assert response.status_code == 303
    assert response.headers['location'] == '/login?next=%2Ftariffs%2F11%3Fdays%3D30'
    assert bot.purchase_calls == []


def test_the_switcher_chooses_which_period_is_priced(client: TestClient):
    """?days= выбирает срок, и карточка показывает цену именно этого срока."""
    response = client.get('/tariffs?days=90')

    assert response.status_code == 200
    assert '799' in response.text
    assert '/tariffs/11?days=90' in response.text, 'кнопка ведёт на выбранный срок'


def test_tariffs_the_portal_cannot_sell_are_hidden_with_a_note(logged_in):
    """Суточный тариф и тариф без готовых сроков оформляются только в боте."""
    bot = Bot(_options(tariffs=[TARIFF_BASIC, TARIFF_DAILY, TARIFF_CUSTOM_DAYS_ONLY]))
    client = logged_in(bot)

    response = client.get('/tariffs')

    assert 'Суточный' not in response.text
    assert 'По дням' not in response.text
    assert 'только в боте' in response.text


def test_purchased_tariff_is_not_offered_again(logged_in):
    bot = Bot(_options(tariffs=[{**TARIFF_BASIC, 'is_purchased': True}]))
    client = logged_in(bot)

    response = client.get('/tariffs')

    assert 'Базовый' not in response.text
    assert 'только в боте' in response.text


def test_classic_sales_mode_says_so_instead_of_half_a_funnel(logged_in):
    bot = Bot(_options(sales_mode='classic', tariffs=[]))
    client = logged_in(bot)

    response = client.get('/tariffs')

    assert response.status_code == 200
    assert 'в боте' in response.text
    assert bot.purchase_calls == []


# ─── подтверждение ───


def test_confirmation_shows_the_price_and_does_not_charge(client: TestClient, bot: Bot):
    response = client.get('/tariffs/11?days=30')

    assert response.status_code == 200
    assert '1 месяц' in response.text
    assert '299' in response.text
    assert bot.purchase_calls == [], 'GET не должен списывать деньги'


def test_confirmation_of_a_vanished_period_is_refused(client: TestClient):
    response = client.get('/tariffs/11?days=365')

    assert response.status_code == 404
    assert 'больше не продаётся' in response.text


def test_confirmation_without_money_offers_no_payment_button(logged_in):
    bot = Bot(_options(balance_kopeks=1000, balance_label='10 ₽'))
    client = logged_in(bot)

    response = client.get('/tariffs/11?days=30')

    assert 'не хватает' in response.text.lower()
    assert 'с баланса' not in response.text, 'кнопки списания быть не должно'
    assert '/balance/topup' in response.text, 'взамен — путь к пополнению'


def test_bonus_points_count_towards_the_price(logged_in):
    """Денег 10 ₽, баллов 290 ₽ — покупка за 299 ₽ возможна.

    Бот списывает по ``available_balance_kopeks`` (деньги + баллы), и каталог
    покупки присылает только денежный кошелёк. Если сравнивать с ним, человеку
    с баллами отказали бы в покупке, которая прошла бы.
    """
    bot = Bot(_options(balance_kopeks=1000, balance_label='10 ₽'), available_kopeks=30000)
    client = logged_in(bot)

    response = client.get('/tariffs/11?days=30')

    assert response.status_code == 200
    assert 'с баланса' in response.text, 'кнопка оплаты должна быть'
    assert 'не хватает' not in response.text.lower()


# ─── списание ───


def test_purchase_passes_the_choice_to_the_bot(client: TestClient, bot: Bot):
    csrf = _csrf_of(client.get('/tariffs/11?days=30').text)

    response = client.post('/tariffs/11/buy', data={'days': 30, 'csrf_token': csrf})

    assert response.status_code == 200
    assert bot.purchase_calls == [{'tariff_id': 11, 'period_days': 30}]
    assert 'успешно активирован' in response.text


def test_purchase_without_csrf_token_does_not_charge(client: TestClient, bot: Bot):
    response = client.post('/tariffs/11/buy', data={'days': 30})

    assert response.status_code == 400
    assert bot.purchase_calls == []


def test_purchase_with_a_foreign_csrf_token_does_not_charge(client: TestClient, bot: Bot):
    response = client.post('/tariffs/11/buy', data={'days': 30, 'csrf_token': 'подделка'})

    assert response.status_code == 400
    assert bot.purchase_calls == []


def test_insufficient_funds_is_shown_as_the_bot_worded_it(logged_in):
    bot = Bot(
        _options(),
        purchase=httpx.Response(
            402,
            json={
                'detail': {
                    'code': 'insufficient_funds',
                    'message': 'Недостаточно средств. Не хватает 99 ₽',
                    'missing_amount': 9900,
                }
            },
        ),
    )
    client = logged_in(bot)
    csrf = _csrf_of(client.get('/tariffs/11?days=30').text)

    response = client.post('/tariffs/11/buy', data={'days': 30, 'csrf_token': csrf})

    assert response.status_code == 402
    assert 'Не хватает 99 ₽' in response.text


def test_bot_refusal_is_shown_without_a_stacktrace(logged_in):
    bot = Bot(_options(), purchase=httpx.Response(400, json={'detail': 'Selected period is not available'}))
    client = logged_in(bot)
    csrf = _csrf_of(client.get('/tariffs/11?days=30').text)

    response = client.post('/tariffs/11/buy', data={'days': 30, 'csrf_token': csrf})

    assert response.status_code == 400
    assert 'Selected period is not available' in response.text


def _csrf_of(html: str) -> str:
    marker = 'name="csrf_token" value="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]
