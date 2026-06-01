"""Podcasts: Allow missing generated media files

Revision ID: 0024
Revises: 0023
Create Date: 2026-05-30

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0024"
down_revision: Union[str, Sequence[str], None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Allow podcasts to exist before their optional RSS and image files are created."""
    op.alter_column("podcast_podcasts", "rss_id", nullable=True)
    op.alter_column("podcast_podcasts", "image_id", nullable=True)


def downgrade() -> None:
    """Restore the previous required media references."""
    op.alter_column("podcast_podcasts", "image_id", nullable=False)
    op.alter_column("podcast_podcasts", "rss_id", nullable=False)
