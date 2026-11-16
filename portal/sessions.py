"""Серверные сессии портала.

В браузер уходит только непредсказуемый идентификатор сессии; токены кабинета
лежат в Redis портала. Так их не достать из браузера, выход работает сразу
(запись удаляется), а обновление пары токенов не требует переписывать cookie.

CSRF-токен живёт в той же записи: формы, которые что-то меняют (выход,
покупка), присылают его скрытым полем. Cookie с ``SameSite=Lax`` сама по себе
POST-запрос со стороннего сайта не пропустит, но на деньгах одного рубежа мало.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any

import structlog


logger = structlog.get_logger(__name__)

SESSION_KEY_PREFIX = 'portal:session:'


@dataclass(slots=True)
class Session:
    """Что портал знает о вошедшем человеке."""

    sid: str
    access_token: str
    refresh_token: str
    csrf_token: str
    #: Срез профиля для шапки страниц: имя, почта, баланс. Обновляется при
    #: каждом открытии главной, поэтому долго устаревшим не бывает.
    user: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return {
            'access_token': self.access_token,
            'refresh_token': self.refresh_token,
            'csrf_token': self.csrf_token,
            'user': self.user,
        }


class SessionStore:
    """Хранилище сессий в Redis.

    Принимает любой объект с ``get``/``set``/``delete`` в стиле redis-py,
    чтобы в тестах подставлялся словарь, а не поднимался Redis.
    """

    def __init__(self, redis: Any, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    @staticmethod
    def _key(sid: str) -> str:
        return f'{SESSION_KEY_PREFIX}{sid}'

    async def create(self, access_token: str, refresh_token: str, user: dict[str, Any] | None = None) -> Session:
        session = Session(
            sid=secrets.token_urlsafe(32),
            access_token=access_token,
            refresh_token=refresh_token,
            csrf_token=secrets.token_urlsafe(32),
            user=user or {},
        )
        await self.save(session)
        return session

    async def get(self, sid: str | None) -> Session | None:
        if not sid:
            return None

        raw = await self._redis.get(self._key(sid))
        if raw is None:
            return None

        payload = _decode(raw)
        if not isinstance(payload, dict):
            logger.warning('Непригодная запись сессии — выбрасываем')
            await self.delete(sid)
            return None

        access_token = payload.get('access_token')
        refresh_token = payload.get('refresh_token')
        csrf_token = payload.get('csrf_token')
        if not (isinstance(access_token, str) and isinstance(refresh_token, str) and isinstance(csrf_token, str)):
            await self.delete(sid)
            return None

        user = payload.get('user')
        return Session(
            sid=sid,
            access_token=access_token,
            refresh_token=refresh_token,
            csrf_token=csrf_token,
            user=user if isinstance(user, dict) else {},
        )

    async def save(self, session: Session) -> None:
        await self._redis.set(self._key(session.sid), _encode(session.as_payload()), ex=self._ttl)

    async def delete(self, sid: str) -> None:
        await self._redis.delete(self._key(sid))


def _encode(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)


def _decode(raw: Any) -> Any:
    import json

    if isinstance(raw, bytes):
        raw = raw.decode('utf-8')
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return None
