"""своя картинка тарифа

Revision ID: 0132
Revises: 0131
Create Date: 2026-10-24

Все экраны покупки показывали один баннер раздела «Покупка подписки», поэтому
тарифы в воронке выглядели одинаково — человек не видел разницы между тем, что
выбирает. Колонка хранит Telegram file_id картинки, загруженной оператором в
админке; пусто — показывается прежний общий баннер.

file_id, а не путь к файлу: картинку загружает оператор прямо в чат, и в
Telegram она уже лежит. Колонка в tariffs, а не ключ в system_settings, —
чтобы картинка удалялась вместе с тарифом и база не копила сирот.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0132'
down_revision: Union[str, None] = '0131'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return True  # таблицы нет — создастся уже с колонкой
    return column in [c['name'] for c in inspector.get_columns(table)]


def upgrade() -> None:
    if not _has_column('tariffs', 'image_file_id'):
        op.add_column('tariffs', sa.Column('image_file_id', sa.String(length=255), nullable=True))


def downgrade() -> None:
    if _has_column('tariffs', 'image_file_id'):
        op.drop_column('tariffs', 'image_file_id')
