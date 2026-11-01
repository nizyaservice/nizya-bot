"""оплаченные роутер-ссылки

Revision ID: 0133
Revises: 0132
Create Date: 2026-11-01

Пользователь платит за vless-ссылку на одну локацию, чтобы прописать её в
роутер. Таблица хранит право («локацию оплатили»), а не саму ссылку: в ссылке
vless-UUID пользователя, то есть его ключ доступа, он меняется при перевыпуске
подписки, и копия в нашей базе только добавляет место утечки. Ссылка каждый раз
берётся из панели в момент показа.

Пара (user_id, host_uuid) уникальна: локация покупается один раз, повторный
показ бесплатный, а двойной тап по кнопке не должен списать деньги дважды.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0133'
down_revision: Union[str, None] = '0132'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = 'router_link_purchases'


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table(TABLE):
        return

    op.create_table(
        TABLE,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('subscription_id', sa.Integer(), nullable=True),
        sa.Column('squad_uuid', sa.String(length=255), nullable=False),
        sa.Column('squad_name', sa.String(length=255), nullable=False),
        sa.Column('host_uuid', sa.String(length=255), nullable=False),
        sa.Column('host_remark', sa.String(length=255), nullable=False),
        sa.Column('price_kopeks', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'host_uuid', name='uq_router_link_purchases_user_host'),
    )
    op.create_index(f'ix_{TABLE}_id', TABLE, ['id'])
    op.create_index(f'ix_{TABLE}_created_at', TABLE, ['created_at'])


def downgrade() -> None:
    if not _has_table(TABLE):
        return

    op.drop_index(f'ix_{TABLE}_created_at', table_name=TABLE)
    op.drop_index(f'ix_{TABLE}_id', table_name=TABLE)
    op.drop_table(TABLE)
