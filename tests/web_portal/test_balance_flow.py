"""Баланс и полный цикл оплаты на сайте.

Главное, что закреплено: портал денег не зачисляет. Он создаёт платёж, уводит
человека к провайдеру и на возврате спрашивает бота о статусе — поэтому заход
на страницу возврата сам по себе ничего не меняет.

Остальное — про деньги и ввод: сумма переводится в копейки без потерь, лимиты
способа проверяются до перехода к провайдеру, а подделанная форма не создаёт
платёж.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from portal.api import CabinetApi


METHODS = [
    {
        'id': 'yookassa',
        'name': 'ЮKassa',
        'description': 'Карта или СБП',
        'min_amount_kopeks': 10000,
        'max_amount_kopeks': 5000000,
        'is_available': True,
    },
    {
        'id': 'cryptobot',
        'name': 'CryptoBot',
        'min_amount_kopeks': 50000,
        'max_amount_kopeks': 10000000,
        'is_available': True,
    },
]

PAYMENT = {
    'payment_id': 'pay-1',
    'payment_url': 'https://pay.example/invoice/pay-1',
    'amount_kopeks': 30000,
    'amount_rubles': 300.0,
    'status': 'pending',
}


class Bot:
    def __init__(self, **overrides: Any) -> None:
        self.overrides = overrides
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))

        if path.endswith('/auth/email/otp/verify'):
            return httpx.Response(
                200,
                json={'access_token': 'a', 'refresh_token': 'r', 'user': {'id': 7, 'email': 'user@example.com'}},
            )
        if path.endswith('/auth/me'):
            return httpx.Response(200, json={'id': 7, 'email': 'user@example.com'})
        if path.endswith('/balance/payment-methods'):
            return self.overrides.get('methods', httpx.Response(200, json=METHODS))
        if path.endswith('/balance/topup'):
            return self.overrides.get('topup', httpx.Response(200, json=PAYMENT))
        if path.endswith('/balance/transactions'):
            return httpx.Response(
                200,
                json={
                    'transactions': [
                        {
                            'id': 1,
                            'type': 'deposit',
                            'amount_rubles': 300.0,
                            'description': 'Пополнение',
                            'created_at': '2026-11-17T10:00:00',
                        }
                    ]
                },
            )
        if path.endswith('/balance/pending-payments'):
            return httpx.Response(200, json={'payments': []})
        if path.endswith('/latest'):
            return self.overrides.get(
                'latest',
                httpx.Response(
                    200,
                    json={
                        'id': 1,
                        'method': 'yookassa',
                        'method_display': 'ЮKassa',
                        'identifier': 'pay-1',
                        'amount_kopeks': 30000,
                        'amount_rubles': 300.0,
                        'status': 'pending',
                        'status_emoji': '⏳',
                        'status_text': 'Ожидает оплаты',
                        'is_paid': False,
                        'is_checkable': True,
                        'created_at': '2026-11-17T10:00:00',
                    },
                ),
            )
        if path.endswith('/check'):
            return self.overrides.get(
                'check',
                httpx.Response(
                    200,
                    json={
                        'success': True,
                        'message': 'Платёж подтверждён',
                        'status_changed': True,
                        'payment': {
                            'id': 1,
                            'method': 'yookassa',
                            'method_display': 'ЮKassa',
                            'identifier': 'pay-1',
                            'amount_kopeks': 30000,
                            'amount_rubles': 300.0,
                            'status': 'paid',
                            'status_emoji': '✅',
                            'status_text': 'Оплачен',
                            'is_paid': True,
                            'is_checkable': False,
                            'created_at': '2026-11-17T10:00:00',
                        },
                    },
                ),
            )
        if path.endswith('/balance'):
            return self.overrides.get(
                'balance', httpx.Response(200, json={'balance_kopeks': 50000, 'balance_rubles': 500.0})
            )
        return httpx.Response(200, json={})

    def calls_to(self, needle: str) -> list[dict[str, Any] | None]:
        return [body for _, path, body in self.calls if path.endswith(needle)]


@pytest.fixture
def bot() -> Bot:
    return Bot()


@pytest.fixture
def logged_in(portal_app):
    opened: list[TestClient] = []

    def make(double: Bot) -> TestClient:
        transport = httpx.MockTransport(double)
        portal_app.state.api_double = CabinetApi(httpx.AsyncClient(base_url='http://bot.invalid', transport=transport))
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


def _csrf_of(html: str) -> str:
    marker = 'name="csrf_token" value="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


# ─── экран баланса ───


def test_balance_page_shows_amount_and_history(client: TestClient):
    response = client.get('/balance')

    assert response.status_code == 200
    # Рубли из копеек с неразрывным пробелом в разряде: «500 ₽», не «500.00 ₽».
    assert '500' in response.text
    assert 'Пополнение' in response.text
    assert '+300' in response.text


def test_balance_page_names_the_points_and_their_value(logged_in):
    """Балл стоит 0,5 ₽, поэтому одной суммы мало: нужно и количество баллов.

    Иначе человек с 49 баллами видит в карточке «24,50 ₽ бонусными баллами» и
    не понимает, откуда взялась эта сумма, — числа-то разные.
    """
    double = Bot(
        balance=httpx.Response(
            200,
            json={
                'balance_kopeks': 10000,
                'balance_rubles': 100.0,
                'bonus_points': 49,
                'bonus_points_kopeks': 2450,
                'bonus_points_rubles': 24.5,
                'available_kopeks': 12450,
                'available_rubles': 124.5,
            },
        )
    )

    text = logged_in(double).get('/balance').text

    assert '24,50' in text, 'стоимость баллов в рублях'
    assert '49 баллов' in text, 'и само количество — 49 баллов это 24,50 ₽, а не 49 ₽'


def test_topup_form_lists_methods_from_the_bot(client: TestClient):
    response = client.get('/balance/topup')

    assert response.status_code == 200
    assert 'ЮKassa' in response.text
    assert 'CryptoBot' in response.text
    assert 'от 100' in response.text, 'минимальная сумма способа видна до оплаты'


def test_without_methods_the_page_says_so(logged_in):
    client = logged_in(Bot(methods=httpx.Response(200, json=[])))

    response = client.get('/balance/topup')

    assert 'Способов оплаты нет' in response.text


# ─── создание платежа ───


def test_amount_in_rubles_becomes_kopeks(client: TestClient, bot: Bot):
    csrf = _csrf_of(client.get('/balance/topup').text)

    response = client.post('/balance/topup', data={'method': 'yookassa', 'amount': '300', 'csrf_token': csrf})

    assert response.status_code == 200
    assert bot.calls_to('/balance/topup') == [{'amount_kopeks': 30000, 'payment_method': 'yookassa'}]
    assert 'https://pay.example/invoice/pay-1' in response.text


def test_fractional_amount_is_not_lost(client: TestClient, bot: Bot):
    csrf = _csrf_of(client.get('/balance/topup').text)

    client.post('/balance/topup', data={'method': 'yookassa', 'amount': '149,50', 'csrf_token': csrf})

    assert bot.calls_to('/balance/topup') == [{'amount_kopeks': 14950, 'payment_method': 'yookassa'}]


def test_amount_below_the_method_minimum_is_refused_before_the_provider(client: TestClient, bot: Bot):
    csrf = _csrf_of(client.get('/balance/topup').text)

    response = client.post('/balance/topup', data={'method': 'yookassa', 'amount': '50', 'csrf_token': csrf})

    assert response.status_code == 400
    assert 'Минимальная сумма' in response.text
    assert bot.calls_to('/balance/topup') == []


def test_nonsense_amount_is_refused(client: TestClient, bot: Bot):
    csrf = _csrf_of(client.get('/balance/topup').text)

    response = client.post('/balance/topup', data={'method': 'yookassa', 'amount': 'много', 'csrf_token': csrf})

    assert response.status_code == 400
    assert bot.calls_to('/balance/topup') == []


def test_unknown_method_is_refused(client: TestClient, bot: Bot):
    csrf = _csrf_of(client.get('/balance/topup').text)

    response = client.post('/balance/topup', data={'method': 'chineseyuan', 'amount': '300', 'csrf_token': csrf})

    assert response.status_code == 400
    assert bot.calls_to('/balance/topup') == []


def test_without_csrf_token_no_payment_is_created(client: TestClient, bot: Bot):
    response = client.post('/balance/topup', data={'method': 'yookassa', 'amount': '300'})

    assert response.status_code == 400
    assert bot.calls_to('/balance/topup') == []


def test_bot_refusal_is_shown_on_the_form(logged_in):
    bot = Bot(topup=httpx.Response(400, json={'detail': 'Payment method is temporarily unavailable'}))
    client = logged_in(bot)
    csrf = _csrf_of(client.get('/balance/topup').text)

    response = client.post('/balance/topup', data={'method': 'yookassa', 'amount': '300', 'csrf_token': csrf})

    assert response.status_code == 400
    assert 'temporarily unavailable' in response.text


# ─── возврат с оплаты ───


def test_return_page_asks_the_bot_instead_of_trusting_the_browser(client: TestClient, bot: Bot):
    """Заход на страницу возврата ничего не зачисляет — только спрашивает статус."""
    response = client.get('/balance/payment/yookassa/pay-1')

    assert response.status_code == 200
    assert 'Оплачен' in response.text
    assert len(bot.calls_to('/check')) == 1, 'статус берётся у бота'
    assert bot.calls_to('/balance/topup') == [], 'никаких начислений со страницы возврата'


def test_unconfirmed_payment_keeps_the_way_back_to_the_provider(logged_in):
    bot = Bot(
        check=httpx.Response(
            200,
            json={
                'success': True,
                'message': 'Платёж ещё не подтверждён',
                'status_changed': False,
                'payment': {
                    'id': 1,
                    'method': 'yookassa',
                    'method_display': 'ЮKassa',
                    'identifier': 'pay-1',
                    'amount_kopeks': 30000,
                    'amount_rubles': 300.0,
                    'status': 'pending',
                    'status_emoji': '⏳',
                    'status_text': 'Ожидает оплаты',
                    'is_paid': False,
                    'is_checkable': True,
                    'created_at': '2026-11-17T10:00:00',
                    'payment_url': 'https://pay.example/invoice/pay-1',
                },
            },
        )
    )
    client = logged_in(bot)

    response = client.get('/balance/payment/yookassa/pay-1')

    assert 'Ожидает оплаты' in response.text
    assert 'https://pay.example/invoice/pay-1' in response.text


def test_provider_return_url_finds_the_payment_by_method(client: TestClient, bot: Bot):
    """Бот строит адрес возврата из CABINET_URL и знает только способ оплаты."""
    response = client.get('/balance/top-up/result?method=yookassa&status=success')

    assert response.status_code == 200
    assert len(bot.calls_to('/latest')) == 1, 'идентификатор платежа спрашиваем у бота'
    assert len(bot.calls_to('/check')) == 1, 'статус подтверждает провайдер, а не адрес'
    assert 'Оплачен' in response.text


def test_success_in_the_return_url_alone_proves_nothing(logged_in):
    """status=success в адресе — слово из браузера, а не подтверждение оплаты."""
    bot = Bot(
        check=httpx.Response(
            200,
            json={
                'success': True,
                'message': 'Платёж ещё не подтверждён',
                'status_changed': False,
                'payment': {
                    'id': 1,
                    'method': 'yookassa',
                    'method_display': 'ЮKassa',
                    'identifier': 'pay-1',
                    'amount_kopeks': 30000,
                    'amount_rubles': 300.0,
                    'status': 'pending',
                    'status_emoji': '⏳',
                    'status_text': 'Ожидает оплаты',
                    'is_paid': False,
                    'is_checkable': True,
                    'created_at': '2026-11-17T10:00:00',
                },
            },
        )
    )
    client = logged_in(bot)

    response = client.get('/balance/top-up/result?method=yookassa&status=success')

    assert 'Ожидает оплаты' in response.text


def test_return_without_a_known_payment_sends_to_the_balance(logged_in):
    bot = Bot(latest=httpx.Response(404, json={'detail': 'Payment not found'}))
    client = logged_in(bot)

    response = client.get('/balance/top-up/result?method=yookassa')

    assert response.status_code == 200
    assert 'Проверьте баланс' in response.text


def test_check_failure_is_explained(logged_in):
    bot = Bot(check=httpx.Response(404, json={'detail': 'Payment not found'}))
    client = logged_in(bot)

    response = client.get('/balance/payment/yookassa/pay-404')

    assert response.status_code == 200
    assert 'Payment not found' in response.text
