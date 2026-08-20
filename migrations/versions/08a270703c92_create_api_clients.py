"""create_api_clients

Revision ID: 08a270703c92
Revises: d4c165b49d52
Create Date: 2026-08-20 21:55:33.996642

"""
from typing import Sequence, Union

from alembic import op


revision: str = '08a270703c92'
down_revision: Union[str, Sequence[str], None] = 'd4c165b49d52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE api_clients (
            client_id     TEXT        PRIMARY KEY,
            api_key_hash  TEXT        NOT NULL UNIQUE,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS api_clients;")
