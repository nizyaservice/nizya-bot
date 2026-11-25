"""Настройки портала.

Все переменные с префиксом ``PORTAL_``, чтобы в одном окружении не
перепутаться с настройками бота (у него свои ~1000 ключей без префикса).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class PortalSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix='PORTAL_',
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
    )

    #: Адрес API бота. В проде — приватный адрес внутри туннеля WireGuard,
    #: наружу этот порт не смотрит.
    api_base_url: str = 'http://127.0.0.1:8080'
    api_timeout_seconds: float = 10.0

    #: Redis портала (не бота): в нём живут серверные сессии.
    redis_url: str = 'redis://127.0.0.1:6379/0'

    session_cookie: str = 'portal_session'
    #: Срок сессии равен сроку refresh-токена кабинета: дольше держать нечего.
    session_ttl_seconds: int = 7 * 24 * 3600
    #: Выключается только для локальной разработки по http.
    cookie_secure: bool = True

    #: Публичный адрес портала — для ссылок в письмах и редиректов.
    public_url: str = 'https://example.com'
    brand_name: str = 'Nizya VPN'
    #: Строка под логотипом и в титуле главной.
    brand_tagline: str = 'Низя? Можна!'
    default_language: str = 'ru'
    #: Куда вести за помощью: портал своих тикетов не ведёт, помощь — в боте.
    support_url: str = ''
    #: Ссылка на бота: ею же закрывается всё, чего на портале нет.
    bot_url: str = ''

    debug: bool = False


@lru_cache
def get_settings() -> PortalSettings:
    """Настройки одним экземпляром на процесс."""
    return PortalSettings()
