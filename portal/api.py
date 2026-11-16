"""Клиент к API кабинета бота.

Портал — серверный клиент: запросы уходят из процесса портала в бот по
туннелю, а не из браузера. Поэтому CORS тут не при чём, и токены
пользователя в браузер не попадают.

Все ответы бота приходят как JSON; ошибку он отдаёт полем ``detail``.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog


logger = structlog.get_logger(__name__)

#: Префикс пользовательских ручек кабинета (см. app/cabinet/routes/__init__.py).
CABINET_PREFIX = '/cabinet'


class CabinetApiError(RuntimeError):
    """Бот ответил ошибкой. ``detail`` годится для показа человеку."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f'{status_code}: {detail}')
        self.status_code = status_code
        self.detail = detail


class CabinetUnavailableError(CabinetApiError):
    """Бот не ответил вовсе: сеть, туннель, таймаут."""

    def __init__(self, detail: str = 'Сервис временно недоступен') -> None:
        super().__init__(503, detail)


class CabinetApi:
    """Тонкая обёртка над httpx: один клиент на процесс, токен — на запрос."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _call(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> Any:
        headers = {'Authorization': f'Bearer {token}'} if token else None
        try:
            response = await self._client.request(method, f'{CABINET_PREFIX}{path}', json=json, headers=headers)
        except httpx.HTTPError as error:
            # Туннель лёг или бот не отвечает — отличаем от «бот сказал нет».
            logger.warning('API бота недоступен', path=path, error=str(error)[:200])
            raise CabinetUnavailableError from error

        if response.status_code >= 400:
            raise CabinetApiError(response.status_code, _detail_of(response))

        if not response.content:
            return None
        return response.json()

    # ─── вход по одноразовому коду ───

    async def request_login_code(self, email: str) -> dict[str, Any]:
        return await self._call('POST', '/auth/email/otp/request', json={'email': email})

    async def verify_login_code(
        self,
        email: str,
        code: str,
        *,
        language: str,
        accepted_legal_documents: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {'email': email, 'code': code, 'language': language}
        if accepted_legal_documents:
            payload['accepted_legal_documents'] = accepted_legal_documents
        return await self._call('POST', '/auth/email/otp/verify', json=payload)

    async def refresh_tokens(self, refresh_token: str) -> dict[str, Any]:
        return await self._call('POST', '/auth/refresh', json={'refresh_token': refresh_token})

    async def logout(self, refresh_token: str) -> None:
        await self._call('POST', '/auth/logout', json={'refresh_token': refresh_token})

    # ─── профиль и подписка ───

    async def me(self, token: str) -> dict[str, Any]:
        return await self._call('GET', '/auth/me', token=token)

    async def subscription(self, token: str) -> Any:
        return await self._call('GET', '/subscription', token=token)

    async def balance(self, token: str) -> Any:
        return await self._call('GET', '/balance', token=token)

    async def legal_consent_config(self) -> Any:
        """Какие документы требуется принять на первом входе."""
        return await self._call('GET', '/info/legal-consent')

    # ─── покупка тарифа ───

    async def purchase_options(self, token: str) -> Any:
        """Каталог: тарифы с ценами уже со скидками группы и баланс человека."""
        return await self._call('GET', '/subscription/purchase-options', token=token)

    async def purchase_tariff(self, token: str, tariff_id: int, period_days: int) -> Any:
        """Купить тариф с баланса.

        Цену считает бот: портал присылает только выбор человека. Иначе
        скидка группы, промо-предложение и продление считались бы дважды и
        расходились бы с тем, что спишется.
        """
        return await self._call(
            'POST',
            '/subscription/purchase-tariff',
            json={'tariff_id': tariff_id, 'period_days': period_days},
            token=token,
        )


def _detail_of(response: httpx.Response) -> str:
    """Человеческая причина ошибки из ответа бота."""
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200] or 'Неизвестная ошибка'

    if isinstance(payload, dict):
        detail = payload.get('detail')
        if isinstance(detail, str):
            return detail
        if isinstance(detail, list) and detail:
            # Ошибка валидации FastAPI: список объектов с msg.
            first = detail[0]
            if isinstance(first, dict) and isinstance(first.get('msg'), str):
                return first['msg']
        if isinstance(detail, dict):
            # Отказы с кодом (не хватает денег и т.п.) приходят объектом.
            message = detail.get('message')
            if isinstance(message, str):
                return message
    return 'Неизвестная ошибка'


def build_client(base_url: str, timeout_seconds: float) -> httpx.AsyncClient:
    """Клиент к боту: один на процесс, создаётся в lifespan приложения."""
    return httpx.AsyncClient(base_url=base_url.rstrip('/'), timeout=timeout_seconds)
