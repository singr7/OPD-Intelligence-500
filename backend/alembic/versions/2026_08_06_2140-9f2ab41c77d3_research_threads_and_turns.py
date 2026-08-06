"""research assistant: threads and the turns in them

Two new tables, nothing touched, no backfill. A visit nobody looked anything up
about simply has no thread, which is what every visit is today.

Note what these tables do *not* have, because it is the module's safety
argument. There is no signature, no status, no `applied` or `accepted` column
and no foreign key from any clinical record back to a turn. A research answer
cannot be adopted into the record — what a doctor takes from it they write
themselves, on the consult note, in their own words. The moment a turn can be
marked accepted, a model's prose has become a clinical decision with a doctor's
name attached, which is what plan decision 7 refuses.

`research_turns.answer` is Text and nothing parses it. There is no JSONB
`structured` column here, unlike `dictations` and `clinical_notes`, and that is
deliberate rather than an omission: a schema is the first step towards a field
on a clinical record.

The unique constraint on (visit_id, doctor_id) is what makes the tab resumable
without merging two doctors' reasoning into one conversation.

Revision ID: 9f2ab41c77d3
Revises: 02571a5c1871
Create Date: 2026-08-06 21:40:11.882043

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9f2ab41c77d3'
down_revision: Union[str, Sequence[str], None] = '02571a5c1871'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('research_threads',
    sa.Column('visit_id', sa.UUID(), nullable=False),
    sa.Column('doctor_id', sa.UUID(), nullable=False),
    sa.Column('context_include', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['doctor_id'], ['doctors.id'], name=op.f('fk_research_threads_doctor_id_doctors')),
    sa.ForeignKeyConstraint(['visit_id'], ['visits.id'], name=op.f('fk_research_threads_visit_id_visits')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_research_threads')),
    sa.UniqueConstraint('visit_id', 'doctor_id', name='uq_research_threads_visit_id_doctor_id')
    )
    op.create_index(op.f('ix_research_threads_doctor_id'), 'research_threads', ['doctor_id'], unique=False)
    op.create_index(op.f('ix_research_threads_visit_id'), 'research_threads', ['visit_id'], unique=False)
    op.create_table('research_turns',
    sa.Column('thread_id', sa.UUID(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('answer', sa.Text(), nullable=False),
    sa.Column('context_sent', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('provider_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('prompt_refs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['thread_id'], ['research_threads.id'], name=op.f('fk_research_turns_thread_id_research_threads')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_research_turns'))
    )
    op.create_index(op.f('ix_research_turns_thread_id'), 'research_turns', ['thread_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_research_turns_thread_id'), table_name='research_turns')
    op.drop_table('research_turns')
    op.drop_index(op.f('ix_research_threads_visit_id'), table_name='research_threads')
    op.drop_index(op.f('ix_research_threads_doctor_id'), table_name='research_threads')
    op.drop_table('research_threads')
