"""create_api_clients

Revision ID: 08a270703c92
Revises: d4c165b49d52
Create Date: 2026-08-20 21:55:33.996642

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '08a270703c92'
down_revision: Union[str, Sequence[str], None] = 'd4c165b49d52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
