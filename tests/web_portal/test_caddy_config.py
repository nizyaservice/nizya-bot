"""Caddy портала: подстановки из Caddyfile должны быть в его окружении.

Caddy падал на старте с «unrecognized global option: encode». Дело было не в
опции: `{$PORTAL_DOMAIN}` разворачивается из окружения самого контейнера Caddy,
а переменную отдавали только порталу. Пустая подстановка оставляла от первой
строки одинокую `{`, Caddy читал её как блок глобальных настроек — и спотыкался
на следующей директиве внутри.

Диагностируется это плохо (в сообщении ни слова про домен), а проверяется
дёшево: имена подстановок должны быть и в compose, и в шаблоне `.env`.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml


DEPLOY = Path(__file__).resolve().parents[2] / 'portal' / 'deploy'
CADDYFILE = DEPLOY / 'Caddyfile'
COMPOSE = DEPLOY / 'docker-compose.yml'
ENV_EXAMPLE = DEPLOY / '.env.example'


def _placeholders() -> set[str]:
    """Имена переменных из `{$NAME}` и `{$NAME:default}` в Caddyfile."""
    return set(re.findall(r'\{\$([A-Za-z_][A-Za-z0-9_]*)[:}]', CADDYFILE.read_text(encoding='utf-8')))


def _declared_names(env_file: Path) -> set[str]:
    lines = env_file.read_text(encoding='utf-8').splitlines()
    return {line.split('=', 1)[0].strip() for line in lines if '=' in line and not line.lstrip().startswith('#')}


def _caddy_service() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding='utf-8'))['services']['caddy']


def _environment_names() -> set[str]:
    service = _caddy_service()
    environment = service.get('environment') or {}
    if isinstance(environment, list):
        names = {item.split('=', 1)[0] for item in environment}
    else:
        names = set(environment)

    env_files = service.get('env_file') or []
    for entry in [env_files] if isinstance(env_files, str) else env_files:
        path = DEPLOY / (entry['path'] if isinstance(entry, dict) else entry)
        if path.exists():
            names |= _declared_names(path)

    return names


def test_caddy_gets_every_variable_its_config_expands() -> None:
    missing = _placeholders() - _environment_names()
    assert not missing, (
        f'Caddyfile разворачивает {sorted(missing)}, но контейнеру caddy их не передают: '
        f'подстановка даст пустую строку и Caddy не разберёт свой конфиг'
    )


def test_missing_domain_stops_compose_instead_of_caddy() -> None:
    """`:?` в значении — чтобы про незаполненный .env сказал compose, а не Caddy."""
    environment = _caddy_service().get('environment') or {}
    assert isinstance(environment, dict), 'ожидался environment словарём'
    for name in _placeholders() & set(environment):
        assert ':?' in str(environment[name]), f'{name} передают без `:?` — пустое значение доедет до Caddy молча'


def test_the_template_mentions_every_variable() -> None:
    missing = _placeholders() - _declared_names(ENV_EXAMPLE)
    assert not missing, f'{sorted(missing)} нет в .env.example — оператор не узнает, что их нужно заполнить'
