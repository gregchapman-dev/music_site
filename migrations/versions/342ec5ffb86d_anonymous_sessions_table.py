"""anonymous sessions table

Revision ID: 342ec5ffb86d
Revises: 
Create Date: 2024-09-14 14:31:13.611284

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '342ec5ffb86d'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('anonymous_session',
    sa.Column('sessionUUID', sa.String(length=128), nullable=False),
    sa.Column('musicEngine', sa.LargeBinary(), nullable=True),
    sa.Column('mei', sa.LargeBinary(), nullable=True),
    sa.Column('humdrum', sa.LargeBinary(), nullable=True),
    sa.Column('musicxml', sa.LargeBinary(), nullable=True),
    sa.PrimaryKeyConstraint('sessionUUID')
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('anonymous_session')
    # ### end Alembic commands ###