"""Initial schema

Revision ID: 001
Revises:
Create Date: 2025-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Run autogenerate via: alembic revision --autogenerate -m "initial schema"
    # This file is a placeholder. Use the following command to generate the real migration:
    #   cd multi_user_bot/backend
    #   alembic revision --autogenerate -m "initial_schema"
    #   alembic upgrade head
    pass


def downgrade() -> None:
    pass
