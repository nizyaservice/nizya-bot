import pytest
import structlog

from app.config import settings
from app.services.payment.payer_identity import PayerIdentity
from app.services.platega_service import PlategaService


PAYER = PayerIdentity(user_id='555', user_name='@neo', contact='555')


def _configure_platega(monkeypatch: pytest.MonkeyPatch, **overrides) -> None:
    values = {
        'PLATEGA_ENABLED': True,
        'PLATEGA_MERCHANT_ID': 'merchant',
        'PLATEGA_SECRET': 'secret',
        'PLATEGA_BASE_URL': 'https://app.platega.io',
        'PLATEGA_API_VERSION': 'v1',
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setattr(settings, key, value, raising=False)


def _fake_request(calls: list[dict], statuses: list[int] | None = None):
    """Заглушка ``_request`` в status-режиме: отдаёт ``(payload, status)``.

    ``statuses`` — ответы по порядку попыток, чтобы проверять фолбэк.
    """
    queue = list(statuses or [200])

    async def fake_request(method, endpoint, **kwargs):
        calls.append({'method': method, 'endpoint': endpoint, **kwargs})
        status = queue.pop(0) if queue else 200
        return {'transactionId': 'tx', 'status': 'PENDING'}, status

    return fake_request


async def _captured_create_endpoint(monkeypatch: pytest.MonkeyPatch, service: PlategaService) -> str:
    calls: list[dict] = []

    monkeypatch.setattr(service, '_request', _fake_request(calls))
    await service.create_payment(payer=PAYER, payment_method=2, amount=100.0, currency='RUB')
    assert calls[0]['method'] == 'POST'
    return calls[0]['endpoint']


def test_sanitize_description_limits_utf8_bytes() -> None:
    original = 'Интернет-сервис - Пополнение баланса на 50 ₽ и ещё чуть-чуть'

    with structlog.testing.capture_logs() as logs:
        trimmed = PlategaService._sanitize_description(original, 64)

    assert len(trimmed.encode('utf-8')) <= 64
    assert trimmed != original
    assert any('trimmed' in entry.get('event', '') for entry in logs)


def test_sanitize_description_returns_clean_value() -> None:
    original = '  Обычное описание  '

    trimmed = PlategaService._sanitize_description(original, 64)

    assert trimmed == 'Обычное описание'
    assert len(trimmed.encode('utf-8')) <= 64


# --- API version selection (#2934) ---
# Platega v2 (POST /v2/transaction/process) отвечает полем `url`, v1 — `redirect`.
# У части мерчантов карточный flow (метод 11) в v1 отдаёт 400 «No available card
# cascades», поэтому версия create-эндпоинта настраивается через PLATEGA_API_VERSION.


async def test_create_payment_defaults_to_the_with_method_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_platega(monkeypatch, PLATEGA_API_VERSION='auto')

    service = PlategaService()

    assert service.api_version == 'auto'
    assert await _captured_create_endpoint(monkeypatch, service) == '/transaction/process'


async def test_create_payment_uses_v2_endpoint_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_platega(monkeypatch, PLATEGA_API_VERSION='v2')

    service = PlategaService()

    assert service.api_version == 'v2'
    assert await _captured_create_endpoint(monkeypatch, service) == '/v2/transaction/process'


async def test_base_url_version_suffix_forces_version_and_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Обход из #2934 (PLATEGA_BASE_URL=…/v2) не должен собирать /v2/v2/… и не
    должен уводить неверсионированный статусный GET на /v2/transaction/{id}."""
    _configure_platega(monkeypatch, PLATEGA_BASE_URL='https://app.platega.io/v2', PLATEGA_API_VERSION='v1')

    service = PlategaService()

    assert service.base_url == 'https://app.platega.io'
    assert service.api_version == 'v2'
    assert await _captured_create_endpoint(monkeypatch, service) == '/v2/transaction/process'


async def test_get_transaction_stays_unversioned(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_platega(monkeypatch, PLATEGA_API_VERSION='v2')

    service = PlategaService()
    captured: dict[str, str] = {}

    async def fake_request(method, endpoint, **kwargs):
        captured['method'] = method
        captured['endpoint'] = endpoint
        return {'id': 'tx', 'status': 'PENDING'}

    monkeypatch.setattr(service, '_request', fake_request)
    await service.get_transaction('tx')

    assert captured == {'method': 'GET', 'endpoint': '/transaction/tx'}


def test_unknown_api_version_falls_back_to_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_platega(monkeypatch, PLATEGA_API_VERSION='v3')

    with structlog.testing.capture_logs() as logs:
        service = PlategaService()

    assert service.api_version == 'auto'
    assert any('PLATEGA_API_VERSION' in entry.get('event', '') for entry in logs)


# --- Redirect URL parsing (#2934) ---


def test_parse_redirect_url_accepts_v1_field() -> None:
    response = {'transactionId': 'tx', 'status': 'PENDING', 'redirect': 'https://pay.platega.io?id=tx'}

    assert PlategaService.parse_redirect_url(response) == 'https://pay.platega.io?id=tx'


def test_parse_redirect_url_accepts_v2_field() -> None:
    response = {'transactionId': 'tx', 'status': 'PENDING', 'url': 'https://pay.platega.io?id=tx'}

    assert PlategaService.parse_redirect_url(response) == 'https://pay.platega.io?id=tx'


def test_parse_redirect_url_missing_or_empty() -> None:
    assert PlategaService.parse_redirect_url(None) is None
    assert PlategaService.parse_redirect_url({}) is None
    assert PlategaService.parse_redirect_url({'redirect': '', 'url': ''}) is None


def test_base_url_version_suffix_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """A manually appended suffix may be uppercase ('/V2'); it must still be stripped
    and treated as a forced version, not left to build a malformed '/V2/transaction/...'."""
    _configure_platega(monkeypatch, PLATEGA_BASE_URL='https://app.platega.io/V2', PLATEGA_API_VERSION='v1')

    service = PlategaService()

    assert service.base_url == 'https://app.platega.io'
    assert service.api_version == 'v2'


async def test_v2_url_field_reaches_returned_redirect_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """END-TO-END regression for #2934: a v2 create response carrying the link in
    ``url`` (not ``redirect``) must surface as ``redirect_url`` in the dict returned
    by the mixin — this pins the actual fix site (payment/platega.py), so reverting
    it to ``response.get('redirect')`` fails here even though the isolated
    parse_redirect_url tests stay green.
    """
    import types

    from app.services.payment import platega as platega_mixin

    _configure_platega(monkeypatch, PLATEGA_API_VERSION='v2')
    monkeypatch.setattr(settings, 'PLATEGA_MIN_AMOUNT_KOPEKS', 1, raising=False)
    monkeypatch.setattr(settings, 'PLATEGA_MAX_AMOUNT_KOPEKS', 10_000_000, raising=False)

    pay_link = 'https://pay.platega.io?id=tx'
    service = PlategaService()

    async def fake_request(method, endpoint, **kwargs):
        # v2 shape: link is under `url`, no `redirect` key at all
        return {'transactionId': 'tx', 'status': 'PENDING', 'url': pay_link}, 200

    monkeypatch.setattr(service, '_request', fake_request)

    # Stub the DB persistence so no real session is needed; it returns an object with .id
    payment_service_module = platega_mixin.import_module('app.services.payment_service')

    async def fake_persist(db, **kwargs):
        return types.SimpleNamespace(id=123)

    monkeypatch.setattr(payment_service_module, 'create_platega_payment', fake_persist, raising=False)

    mixin = platega_mixin.PlategaPaymentMixin()
    mixin.platega_service = service

    result = await mixin.create_platega_payment(
        db=None,
        user_id=1,
        amount_kopeks=10_000,
        description='Пополнение',
        language='ru',
        payment_method_code=2,
    )

    assert result is not None
    assert result['redirect_url'] == pay_link


# --- Данные плательщика (docs.platega.io: metadata.userId / metadata.userName) ---
# Без metadata.userId у магазинов части категорий отключается антифрод, и
# Platega отключает приём платежей.


@pytest.mark.parametrize('api_version', ['v1', 'v2'])
async def test_create_payment_sends_payer_metadata(monkeypatch: pytest.MonkeyPatch, api_version: str) -> None:
    _configure_platega(monkeypatch, PLATEGA_API_VERSION=api_version)
    service = PlategaService()
    calls: list[dict] = []

    monkeypatch.setattr(service, '_request', _fake_request(calls))
    await service.create_payment(payer=PAYER, payment_method=2, amount=100.0, currency='RUB')

    assert calls[0]['json_data']['metadata'] == {'userId': '555', 'userName': '@neo'}


async def test_create_payment_requires_the_payer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Забыть плательщика нельзя: без него магазин отключат."""
    _configure_platega(monkeypatch)

    with pytest.raises(TypeError):
        await PlategaService().create_payment(payment_method=2, amount=100.0, currency='RUB')  # type: ignore[call-arg]


# --- Выбор ручки создания: с заданным методом или без ---
# docs.platega.io: POST /transaction/process — «создание платежной ссылки с
# заданным методом», POST /v2/transaction/process — «БЕЗ заданного метода», и
# поля paymentMethod у второй ручки нет вовсе. Способ оплаты человек выбирает в
# боте, поэтому по умолчанию идём в первую: иначе страница шлюза спрашивает
# способ заново.


async def test_with_method_endpoint_carries_the_chosen_method(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_platega(monkeypatch, PLATEGA_API_VERSION='auto')
    service = PlategaService()
    calls: list[dict] = []

    monkeypatch.setattr(service, '_request', _fake_request(calls))
    await service.create_payment(payer=PAYER, payment_method=11, amount=100.0, currency='RUB')

    assert calls[0]['endpoint'] == '/transaction/process'
    assert calls[0]['json_data']['paymentMethod'] == 11


async def test_methodless_endpoint_gets_no_payment_method(monkeypatch: pytest.MonkeyPatch) -> None:
    """В ручке без метода это поле не задокументировано — не отправляем его."""
    _configure_platega(monkeypatch, PLATEGA_API_VERSION='v2')
    service = PlategaService()
    calls: list[dict] = []

    monkeypatch.setattr(service, '_request', _fake_request(calls))
    await service.create_payment(payer=PAYER, payment_method=11, amount=100.0, currency='RUB')

    assert calls[0]['endpoint'] == '/v2/transaction/process'
    assert 'paymentMethod' not in calls[0]['json_data']


async def test_auto_falls_back_to_the_methodless_link(monkeypatch: pytest.MonkeyPatch) -> None:
    """400 «No available card cascades» (#2934) не должен ронять оплату."""
    _configure_platega(monkeypatch, PLATEGA_API_VERSION='auto')
    service = PlategaService()
    calls: list[dict] = []

    monkeypatch.setattr(service, '_request', _fake_request(calls, statuses=[400, 200]))
    response = await service.create_payment(payer=PAYER, payment_method=12, amount=100.0, currency='RUB')

    assert response is not None
    assert [call['endpoint'] for call in calls] == ['/transaction/process', '/v2/transaction/process']
    assert 'paymentMethod' not in calls[1]['json_data']


async def test_auto_does_not_fall_back_on_bad_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """401 второй ручкой не лечится — незачем дёргать провайдера дважды."""
    _configure_platega(monkeypatch, PLATEGA_API_VERSION='auto')
    service = PlategaService()
    calls: list[dict] = []

    monkeypatch.setattr(service, '_request', _fake_request(calls, statuses=[401, 200]))
    response = await service.create_payment(payer=PAYER, payment_method=11, amount=100.0, currency='RUB')

    assert response is None
    assert [call['endpoint'] for call in calls] == ['/transaction/process']


async def test_forced_version_never_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_platega(monkeypatch, PLATEGA_API_VERSION='v1')
    service = PlategaService()
    calls: list[dict] = []

    monkeypatch.setattr(service, '_request', _fake_request(calls, statuses=[400, 200]))
    response = await service.create_payment(payer=PAYER, payment_method=11, amount=100.0, currency='RUB')

    assert response is None
    assert [call['endpoint'] for call in calls] == ['/transaction/process']


# --- Обязательные поля ссылок возврата ---


async def test_return_urls_are_always_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Докам обе ручки держат return/failedUrl обязательными.

    Пустые настройки раньше просто выкидывали поля из запроса.
    """
    _configure_platega(monkeypatch, PLATEGA_API_VERSION='auto')
    for key in ('PLATEGA_RETURN_URL', 'PLATEGA_FAILED_URL', 'WEBHOOK_URL'):
        monkeypatch.setattr(settings, key, None, raising=False)
    monkeypatch.setattr(settings, 'BOT_USERNAME', 'nizya_bot', raising=False)

    service = PlategaService()
    calls: list[dict] = []

    monkeypatch.setattr(service, '_request', _fake_request(calls))
    await service.create_payment(payer=PAYER, payment_method=11, amount=100.0, currency='RUB')

    body = calls[0]['json_data']
    assert body['return'] == 'https://t.me/nizya_bot'
    assert body['failedUrl'] == 'https://t.me/nizya_bot'
