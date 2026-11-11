"""бонусные баллы пользователя

Revision ID: 0134
Revises: 0133
Create Date: 2026-11-11

Второй кошелёк рядом с деньгами: баллы начисляет оператор в админке, а при
любой оплате с баланса они тратятся раньше денег (см. subtract_user_balance).

Копейки, а не «баллы штуками»: 1 балл = 1 ₽, и в копейках вся арифметика цен
(прораты за остаток периода, проценты скидок) работает без отдельного
пересчёта, а частичная оплата баллами ложится точно.

Отдельная колонка, а не часть balance_kopeks: баллы не выводятся наружу
(реферальные выплаты считают по деньгам) и показываются человеку отдельной
строкой в главном меню.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0134'
down_revision: Union[str, None] = '0133'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return True  # таблицы нет — создастся уже с колонкой
    return column in [c['name'] for c in inspector.get_columns(table)]


def upgrade() -> None:
    if not _has_column('users', 'bonus_points_kopeks'):
        op.add_column(
            'users',
            sa.Column('bonus_points_kopeks', sa.Integer(), nullable=False, server_default='0'),
        )


def downgrade() -> None:
    if _has_column('users', 'bonus_points_kopeks'):
        op.drop_column('users', 'bonus_points_kopeks')
