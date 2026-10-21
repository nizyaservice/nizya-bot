"""callback_data кнопок обязан влезать в 64 байта — лимит Telegram.

Превышение не видно ни при чтении кода, ни в тестах с короткими заглушками:
Telegram просто отвечает ``BUTTON_DATA_INVALID``, и экран не открывается
целиком. Ловилось дважды и оба раза в проде:

* экран «Внешний сквад» тарифа — префикс ``admin_tariff_set_ext_squad``
  (26 байт) плюс UUID сквада давал 65 байт при любом тарифе, экран не
  открывался вовсе;
* экран «Лимиты по серверам» — ``admin_tariff_srv_limit`` укладывался в 61
  байт при однозначном id и сломался бы на пятизначном.

Оба раза бомбой был UUID: 36 символов, которые в тестах подменяли на ``sq-1``.
Поэтому проверка статическая и смотрит именно на подстановку UUID — разбирает
AST и считает длину f-строк, уходящих в ``callback_data``.
"""

from __future__ import annotations

import ast
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / 'app'

# Лимит Telegram на callback_data, в байтах.
CALLBACK_DATA_LIMIT = 64

# Длина UUID в каноническом виде: '3f2b7c18-5a41-4e0d-9c6b-2d8e1f0a7b53'.
UUID_LENGTH = 36

# Запас на автоинкрементный id: миллионы тарифов/промогрупп не ожидаются,
# но запас должен быть больше сегодняшних однозначных значений — иначе
# проверка «зелёная сейчас, красная через год».
ID_DIGITS = 7


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Модульные ``NAME = 'строка'`` — префиксы кнопок часто вынесены в такие.

    Без этого проверка молча слепнет: вынесенный в константу префикс выглядит
    как обычный плейсхолдер, и его настоящая длина в подсчёт не попадает.
    """
    constants: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                constants[target.id] = node.value.value
    return constants


def _placeholder_length(node: ast.FormattedValue, constants: dict[str, str]) -> int:
    """Сколько байт займёт подставляемое значение.

    Точно посчитать нельзя — выражение вычисляется в рантайме. Известную
    строковую константу берём как есть, UUID узнаём по имени
    (``squad.squad_uuid``, ``squad.uuid``), остальное считаем числовым id.
    """
    if isinstance(node.value, ast.Name) and node.value.id in constants:
        return len(constants[node.value.id].encode())
    source = ast.unparse(node.value).lower()
    return UUID_LENGTH if 'uuid' in source else ID_DIGITS


def _callback_data_f_strings() -> list[tuple[str, int, str, int]]:
    """(файл, строка, шаблон, максимальная длина) для каждого callback_data=f'…'."""
    found: list[tuple[str, int, str, int]] = []

    for path in sorted(APP_DIR.rglob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        constants = _module_string_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.keyword) or node.arg != 'callback_data':
                continue
            if not isinstance(node.value, ast.JoinedStr):
                continue

            total = 0
            template: list[str] = []
            has_uuid = False
            for part in node.value.values:
                if isinstance(part, ast.Constant):
                    total += len(str(part.value).encode())
                    template.append(str(part.value))
                elif isinstance(part, ast.FormattedValue):
                    total += _placeholder_length(part, constants)
                    has_uuid = has_uuid or 'uuid' in ast.unparse(part.value).lower()
                    template.append('{' + ast.unparse(part.value) + '}')

            if has_uuid:
                found.append((str(path.relative_to(APP_DIR.parent)), node.value.lineno, ''.join(template), total))

    return found


def test_callback_data_with_uuid_fits_the_limit() -> None:
    """Ни одна кнопка с UUID не перебирает 64 байта.

    Если тест упал — укоротите префикс, а не UUID. Образцы уже есть в коде:
    ``trf_sq`` (серверы тарифа), ``trf_xsq`` (внешний сквад), ``trf_lim``
    (лимиты по серверам).
    """
    oversized = {
        f'{path}:{line}': f'{total} байт — {template}'
        for path, line, template, total in _callback_data_f_strings()
        if total > CALLBACK_DATA_LIMIT
    }

    assert not oversized, f'callback_data длиннее {CALLBACK_DATA_LIMIT} байт: {oversized}'


def test_the_scan_actually_finds_callback_data_with_uuid() -> None:
    """Страховка от молчаливой проверки.

    Переименуют аргумент, поменяется способ сборки кнопок — и тест выше станет
    зелёным просто потому, что ничего не нашёл. Такое уже бывало с тестами на
    PostgreSQL, которые без базы молча пропускались и выглядели как успех.
    """
    assert _callback_data_f_strings(), 'сканер не нашёл ни одного callback_data с UUID — проверка перестала работать'
