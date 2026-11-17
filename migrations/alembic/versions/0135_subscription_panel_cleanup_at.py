"""отметка о чистке панельного аккаунта истёкшей подписки

Revision ID: 0135
Revises: 0134
Create Date: 2026-11-17

Чистка истёкших (``expired_panel_cleanup_service``) брала окно из 200 самых
старых истёкших подписок и решала по каждой уже в Python. Обработанная
подписка оставалась EXPIRED с той же старой датой, то есть не покидала окно:
после первого прохода оно целиком состояло из уже обработанных, и до свежих
истечений чистка не доходила больше никогда. В панели копились аккаунты
истёкших подписок, в основном пробных — их среди истёкших больше всего.

Отметка времени, а не флаг: она сравнивается с ``end_date`` подписки, поэтому
продлённая подписка после нового истечения сама вернётся в очередь — чистить
флаг во всех путях покупки не нужно.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0135'
down_revision: Union[str, None] = '0134'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return True  # таблицы нет — создастся уже с колонкой
    return column in [c['name'] for c in inspector.get_columns(table)]


def upgrade() -> None:
    if not _has_column('subscriptions', 'panel_cleanup_at'):
        op.add_column(
            'subscriptions',
            sa.Column('panel_cleanup_at', sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    if _has_column('subscriptions', 'panel_cleanup_at'):
        op.drop_column('subscriptions', 'panel_cleanup_at')
