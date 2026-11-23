"""Сборка образа портала: пути в Dockerfile должны жить внутри своего контекста.

`docker compose up -d --build` падал с «portal: not found»: контекстом был корень
репозитория, а там корневой `.dockerignore` исключает `portal/` целиком — иначе
портал уезжал бы в образ бота, который делает `COPY . .`. Каталога в контексте
не было, и `COPY portal/` не находил ничего.

Проверка дешёвая, а ломается такое молча и только на ВПС при деплое: локально
образ портала никто не собирает.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPO_ROOT / 'portal' / 'deploy'
COMPOSE = DEPLOY / 'docker-compose.yml'


def _build_section() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding='utf-8'))['services']['portal']['build']


def _context() -> Path:
    return (DEPLOY / _build_section()['context']).resolve()


def _dockerfile() -> Path:
    return (_context() / _build_section()['dockerfile']).resolve()


def _copy_sources(dockerfile: Path) -> list[str]:
    """Источники всех COPY, кроме копирования из других стадий (`--from=`)."""
    sources: list[str] = []
    for line in dockerfile.read_text(encoding='utf-8').splitlines():
        match = re.match(r'^COPY\s+(.*)$', line.strip())
        if not match:
            continue
        parts = match.group(1).split()
        if any(part.startswith('--from=') for part in parts):
            continue
        sources.extend(part for part in parts[:-1] if not part.startswith('--'))
    return sources


def _ignore_patterns(context: Path) -> list[str]:
    ignore = context / '.dockerignore'
    if not ignore.exists():
        return []
    lines = (line.strip() for line in ignore.read_text(encoding='utf-8').splitlines())
    return [line.rstrip('/') for line in lines if line and not line.startswith('#')]


def _ignored(path: str, patterns: list[str]) -> str | None:
    """Первый шаблон .dockerignore, выбрасывающий путь или любого его предка."""
    candidates = [path, *(str(parent) for parent in PurePosixPath(path).parents if str(parent) != '.')]
    for pattern in patterns:
        if pattern.startswith('!'):
            continue
        if any(fnmatch(candidate, pattern) for candidate in candidates):
            return pattern
    return None


def test_context_is_not_the_repo_root_while_it_hides_portal() -> None:
    root_ignores_portal = _ignored('portal', _ignore_patterns(REPO_ROOT))
    assert root_ignores_portal, 'portal/ больше не исключён из образа бота — проверьте корневой Dockerfile'
    assert _context() != REPO_ROOT, (
        f'контекст сборки портала — корень репозитория, где `{root_ignores_portal}` '
        f'из корневого .dockerignore прячет portal/: сборка упадёт на COPY'
    )


def test_every_copy_source_exists_inside_the_context() -> None:
    context = _context()
    for source in _copy_sources(_dockerfile()):
        resolved = (context / source).resolve()
        assert resolved.is_relative_to(context), f'COPY {source} указывает за пределы контекста {context}'
        found = list(context.glob(source)) if {'*', '?', '['} & set(source) else [resolved] if resolved.exists() else []
        assert found, f'COPY {source}: в контексте {context} такого пути нет'


def test_no_copy_source_is_excluded_by_dockerignore() -> None:
    patterns = _ignore_patterns(_context())
    for source in _copy_sources(_dockerfile()):
        if source == '.':
            continue
        pattern = _ignored(source, patterns)
        assert pattern is None, f'COPY {source} исключён шаблоном `{pattern}` — сборщик его не увидит'


def test_operator_env_stays_out_of_the_image() -> None:
    """`.env` портала оператор создаёт рядом с compose, а COPY забирает весь каталог."""
    patterns = _ignore_patterns(_context())
    assert _ignored('deploy/.env', patterns), '.env оператора попадёт в образ — добавьте его в portal/.dockerignore'
