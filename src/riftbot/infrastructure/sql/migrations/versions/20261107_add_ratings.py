"""add glicko-2 ratings and match rating flag

Revision ID: 20261107_add_ratings
Revises: 72c4e96a034d
Create Date: 2026-11-07 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261107_add_ratings"
down_revision: str | None = "72c4e96a034d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("players", schema=None) as batch_op:
        batch_op.add_column(sa.Column("rating", sa.Float(), nullable=False, server_default="1500"))
        batch_op.add_column(
            sa.Column("rating_deviation", sa.Float(), nullable=False, server_default="350")
        )
        batch_op.add_column(
            sa.Column("rating_volatility", sa.Float(), nullable=False, server_default="0.06")
        )
        batch_op.add_column(
            sa.Column("rated_matches", sa.BigInteger(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("inactive_weeks", sa.BigInteger(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("byes", sa.BigInteger(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("last_match_at", sa.DateTime(), nullable=True))

    with op.batch_alter_table("matches", schema=None) as batch_op:
        batch_op.add_column(sa.Column("rated", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("matches", schema=None) as batch_op:
        batch_op.drop_column("rated")

    with op.batch_alter_table("players", schema=None) as batch_op:
        batch_op.drop_column("last_match_at")
        batch_op.drop_column("byes")
        batch_op.drop_column("inactive_weeks")
        batch_op.drop_column("rated_matches")
        batch_op.drop_column("rating_volatility")
        batch_op.drop_column("rating_deviation")
        batch_op.drop_column("rating")
