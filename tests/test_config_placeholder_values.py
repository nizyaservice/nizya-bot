"""Заглушки из шаблона .env не должны валить старт бота.

Settings() создаётся на импорте app.config и валидирует все поля разом — до
того, как кто-либо посмотрит на флаги *_ENABLED. Поэтому незаполненные
числовые поля выключенных сервисов обязаны читаться как «не задано».
"""

import pytest

from app.config import Settings


def build_settings(monkeypatch, **env: str) -> Settings:
    """Settings из переменных окружения, без чтения реального .env."""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


@pytest.mark.parametrize(
    ('name', 'raw'),
    [
        # выключенные платёжные шлюзы: шаблон оставляет заглушку
        ('MULENPAY_SHOP_ID', 'change_me_mulenpay_shop_id'),
        ('FREEKASSA_SHOP_ID', 'change_me_freekassa_shop_id'),
        ('KASSA_AI_SHOP_ID', 'change_me_kassa_ai_shop_id'),
        ('SEVERPAY_MID', 'change_me_severpay_mid'),
        # поле есть, значение пустое
        ('FREEKASSA_PAYMENT_SYSTEM_ID', ''),
        ('APPLE_IAP_APP_APPLE_ID', ''),
        ('LOG_ROTATION_TOPIC_ID', ''),
        # docker compose отдаёт хвостовой комментарий пустой строки как значение
        ('ADMIN_REPORTS_TOPIC_ID', '# ID топика для отчетов'),
        ('LOG_ROTATION_TOPIC_ID', '   '),
        # поле «обнулили» словом вместо пустого значения
        ('MULENPAY_SHOP_ID', 'None'),
        ('SEVERPAY_MID', 'null'),
        ('LOG_ROTATION_TOPIC_ID', ' NONE '),
        ('KASSA_AI_SHOP_ID', '-'),
    ],
)
def test_placeholder_numeric_value_reads_as_unset(monkeypatch, name, raw):
    settings = build_settings(monkeypatch, **{name: raw})
    assert getattr(settings, name) is None


def test_whole_disabled_gateway_block_does_not_break_startup(monkeypatch):
    """Ровно тот набор значений, на котором контейнер падал с 8 ошибками."""
    settings = build_settings(
        monkeypatch,
        ADMIN_REPORTS_TOPIC_ID='# ID топика для отчетов',
        MULENPAY_SHOP_ID='change_me_mulenpay_shop_id',
        FREEKASSA_SHOP_ID='change_me_freekassa_shop_id',
        FREEKASSA_PAYMENT_SYSTEM_ID='',
        KASSA_AI_SHOP_ID='change_me_kassa_ai_shop_id',
        SEVERPAY_MID='change_me_severpay_mid',
        APPLE_IAP_APP_APPLE_ID='',
        LOG_ROTATION_TOPIC_ID='',
    )
    assert settings.MULENPAY_SHOP_ID is None
    assert settings.SEVERPAY_MID is None
    assert settings.ADMIN_REPORTS_TOPIC_ID is None


def test_real_numeric_values_still_parsed(monkeypatch):
    settings = build_settings(
        monkeypatch,
        MULENPAY_SHOP_ID='4242',
        LOG_ROTATION_TOPIC_ID=' 17 ',
    )
    assert settings.MULENPAY_SHOP_ID == 4242
    assert settings.LOG_ROTATION_TOPIC_ID == 17


def test_string_fields_keep_their_value(monkeypatch):
    """Чистка касается только числовых полей: строки не должны тихо обнуляться."""
    settings = build_settings(
        monkeypatch,
        REMNAWAVE_API_KEY='change_me_remnawave_api_key',
        BOT_USERNAME='',
    )
    assert settings.REMNAWAVE_API_KEY == 'change_me_remnawave_api_key'
    assert settings.BOT_USERNAME == ''


def test_invalid_numeric_value_still_rejected(monkeypatch):
    """Опечатка в числе — по-прежнему ошибка, а не молчаливый None."""
    with pytest.raises(ValueError, match='MULENPAY_SHOP_ID'):
        build_settings(monkeypatch, MULENPAY_SHOP_ID='42abc')


def test_placeholder_database_url_falls_back_to_postgres_settings(monkeypatch):
    """Заглушка в DATABASE_URL не должна подменять собой адрес из POSTGRES_*."""
    settings = build_settings(
        monkeypatch,
        DATABASE_URL='change_me_database_url',
        DATABASE_MODE='postgresql',
        POSTGRES_HOST='postgres',
        POSTGRES_PORT='5432',
        POSTGRES_DB='remnawave_bot',
        POSTGRES_USER='remnawave_user',
        POSTGRES_PASSWORD='secret',
    )
    assert settings.get_database_url() == 'postgresql+asyncpg://remnawave_user:secret@postgres:5432/remnawave_bot'


def test_real_database_url_is_used_as_is(monkeypatch):
    dsn = 'postgresql+asyncpg://user:pass@db:5432/bot'
    settings = build_settings(monkeypatch, DATABASE_URL=dsn, DATABASE_MODE='postgresql')
    assert settings.get_database_url() == dsn


@pytest.mark.parametrize('password', ['StrPwd*56h3%', 'p@ss:w/ord', 'already%25encoded', 'про#цент?'])
def test_special_characters_in_password_survive_dsn(monkeypatch, password):
    """Пароль — часть URI: спецсимволы кодируются и читаются обратно без потерь."""
    from sqlalchemy.engine import make_url

    settings = build_settings(
        monkeypatch,
        DATABASE_URL='',
        DATABASE_MODE='postgresql',
        POSTGRES_HOST='postgres',
        POSTGRES_PORT='5432',
        POSTGRES_DB='remnawave_bot',
        POSTGRES_USER='remnawave_user',
        POSTGRES_PASSWORD=password,
    )
    assert make_url(settings.get_database_url()).password == password


def test_alembic_config_survives_percent_in_password(monkeypatch):
    """ConfigParser внутри Alembic видит '%' как подстановку — адрес должен дойти целым."""
    from sqlalchemy.engine import make_url

    from app.database.migrations import _get_alembic_config

    password = 'StrPwd*56h3%'
    monkeypatch.setenv('DATABASE_URL', '')
    monkeypatch.setenv('DATABASE_MODE', 'postgresql')
    monkeypatch.setenv('POSTGRES_PASSWORD', password)
    monkeypatch.setenv('POSTGRES_USER', 'remnawave_user')
    monkeypatch.setenv('POSTGRES_HOST', 'postgres')
    monkeypatch.setenv('POSTGRES_DB', 'remnawave_bot')
    from app.config import Settings, settings as live_settings

    fresh = Settings(_env_file=None)
    monkeypatch.setattr(live_settings, 'POSTGRES_PASSWORD', fresh.POSTGRES_PASSWORD)
    monkeypatch.setattr(live_settings, 'DATABASE_URL', '')
    monkeypatch.setattr(live_settings, 'DATABASE_MODE', 'postgresql')
    monkeypatch.setattr(live_settings, 'POSTGRES_USER', 'remnawave_user')
    monkeypatch.setattr(live_settings, 'POSTGRES_HOST', 'postgres')
    monkeypatch.setattr(live_settings, 'POSTGRES_DB', 'remnawave_bot')

    cfg = _get_alembic_config()
    assert make_url(cfg.get_main_option('sqlalchemy.url')).password == password


def test_alembic_url_doubles_every_percent(monkeypatch):
    """Оба места, где адрес уходит в Alembic, берут его из одного метода."""
    settings = build_settings(
        monkeypatch,
        DATABASE_URL='',
        DATABASE_MODE='postgresql',
        POSTGRES_HOST='postgres',
        POSTGRES_PORT='5432',
        POSTGRES_DB='remnawave_bot',
        POSTGRES_USER='remnawave_user',
        POSTGRES_PASSWORD='StrPwd*56h3%',
    )
    alembic_url = settings.get_alembic_database_url()
    assert '%%' in alembic_url
    assert alembic_url.replace('%%', '%') == settings.get_database_url()
