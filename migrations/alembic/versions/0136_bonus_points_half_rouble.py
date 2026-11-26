"""балл стал стоить 0,5 ₽: пересчёт уже начисленных баллов

Revision ID: 0136
Revises: 0135
Create Date: 2026-11-26

В ``users.bonus_points_kopeks`` лежит денежная СТОИМОСТЬ баллов, а количество
получается делением на курс (``app/utils/bonus_points.py``). Курс сменился с
1 ₽ за балл на 0,5 ₽ — значит, чтобы у человека осталось то же количество
баллов, их стоимость надо уполовинить: было 100 баллов за 100 ₽, стало те же
100 баллов, но оплатят они 50 ₽. Решение владельца: смена курса касается и уже
выданных баллов.

Делим целочисленно: курс — 50 копеек, а все начисления шли кратными рублю или
половине рубля, поэтому нечётной копейки взяться неоткуда. Если она всё же
есть (частичная оплата копейками), округление вниз отнимает полкопейки — это
меньше, чем можно показать человеку, и в его пользу ошибаться тут нечем.

Назад курс не вернуть без потерь: ``downgrade`` удваивает стоимость, то есть
восстанавливает прежнюю сумму ровно у тех, кого миграция делила без остатка.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0136'
down_revision: Union[str, None] = '0135'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return False
    return column in [c['name'] for c in inspector.get_columns(table)]


def upgrade() -> None:
    if not _has_column('users', 'bonus_points_kopeks'):
        return

    op.execute(sa.text('UPDATE users SET bonus_points_kopeks = bonus_points_kopeks / 2 WHERE bonus_points_kopeks > 0'))


def downgrade() -> None:
    if not _has_column('users', 'bonus_points_kopeks'):
        return

    op.execute(sa.text('UPDATE users SET bonus_points_kopeks = bonus_points_kopeks * 2 WHERE bonus_points_kopeks > 0'))
