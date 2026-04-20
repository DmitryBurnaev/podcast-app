"""Pre-initial: support for previous ones

===
Pre-initial script which skips all action, but supports run-logic migrations after already
applied ones (on non-empty DB)
===

Revision ID: 0021
Revises: 0020
"""

from typing import Union, Sequence

# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
